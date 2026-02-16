"""
Unify all article HTML pages into a consistent site layout.

What it does
- Wrap each article page (root *.html excluding index/articles) with:
  header/nav + main content + related posts + footer
- Ensures <meta charset>, viewport, and <link rel="stylesheet" href="style.css">
- Optionally injects canonical URL (if SITE_BASE env var exists)
- Related posts are generated from articles.json

Safe to run repeatedly.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date
from pathlib import Path
from typing import Any

TODAY = date.today().isoformat()

EXCLUDE = {"index.html", "articles.html", "404.html"}
WRAP_MARKER_START = "<!-- SITE_WRAPPER_START -->"
WRAP_MARKER_END = "<!-- SITE_WRAPPER_END -->"


def read_text(path: Path) -> str:
    """Read a text file as UTF-8 (ignoring errors)."""
    return path.read_text(encoding="utf-8", errors="ignore")


def write_text(path: Path, content: str) -> None:
    """Write a text file as UTF-8."""
    path.write_text(content, encoding="utf-8")


def escape_html(text: str) -> str:
    """Escape HTML special chars."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def indent_lines(s: str, spaces: int) -> str:
    """Indent each line by `spaces`."""
    pad = " " * spaces
    return "\n".join(pad + line for line in s.splitlines())


def ensure_viewport_and_charset(html: str) -> str:
    """Add charset/viewport in head if missing."""
    head_m = re.search(r"<head[^>]*>(.*?)</head>",
                       html, flags=re.IGNORECASE | re.DOTALL)
    if not head_m:
        return html

    head_inner = head_m.group(1)

    need_charset = not re.search(
        r"<meta[^>]+charset=", head_inner, flags=re.IGNORECASE)
    need_viewport = not re.search(
        r'name=["\']viewport["\']', head_inner, flags=re.IGNORECASE)

    insert = ""
    if need_charset:
        insert += '  <meta charset="UTF-8">\n'
    if need_viewport:
        insert += '  <meta name="viewport" content="width=device-width, initial-scale=1.0">\n'

    if not insert:
        return html

    return re.sub(
        r"<head[^>]*>",
        lambda m: m.group(0) + "\n" + insert,
        html,
        flags=re.IGNORECASE,
        count=1,
    )


def ensure_stylesheet_in_head(html: str) -> str:
    """Ensure the page links to style.css in head."""
    if re.search(r'href=["\']style\.css["\']', html, flags=re.IGNORECASE):
        return html

    if re.search(r"</head>", html, flags=re.IGNORECASE):
        return re.sub(
            r"</head>",
            '  <link rel="stylesheet" href="style.css">\n</head>',
            html,
            flags=re.IGNORECASE,
            count=1,
        )

    return html


def extract_title(html: str, fallback: str) -> str:
    """Extract <title> or first <h1> as title; fallback if none."""
    m = re.search(r"<title>(.*?)</title>", html,
                  flags=re.IGNORECASE | re.DOTALL)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()

    m = re.search(r"<h1[^>]*>(.*?)</h1>", html,
                  flags=re.IGNORECASE | re.DOTALL)
    if m:
        text = re.sub(r"<[^>]+>", "", m.group(1))
        return re.sub(r"\s+", " ", text).strip()

    return fallback


def extract_body_inner(html: str) -> str:
    """Extract inner content of <body>...</body>."""
    m = re.search(r"<body[^>]*>(.*?)</body>", html,
                  flags=re.IGNORECASE | re.DOTALL)
    if m:
        return m.group(1).strip()
    return html.strip()


def extract_existing_main(body_inner: str) -> str | None:
    """
    If this page was already wrapped, recover original content from main.
    """
    if WRAP_MARKER_START not in body_inner:
        return None
    m = re.search(
        r"<main class=[\"']card[\"']>(.*?)</main>",
        body_inner,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if m:
        return m.group(1).strip()
    return None


def set_body_preserve_attrs(html: str, new_body_inner: str) -> str:
    """Replace body content but preserve existing <body ...> attributes."""
    m = re.search(r"<body([^>]*)>(.*?)</body>", html,
                  flags=re.IGNORECASE | re.DOTALL)
    if not m:
        return html

    attrs = m.group(1)
    return re.sub(
        r"<body([^>]*)>(.*?)</body>",
        f"<body{attrs}>\n{new_body_inner}\n</body>",
        html,
        flags=re.IGNORECASE | re.DOTALL,
        count=1,
    )


def ensure_canonical(html: str, site_base: str, filename: str) -> str:
    """Inject canonical link into head if missing and site_base provided."""
    if not site_base:
        return html
    if re.search(r'rel=["\']canonical["\']', html, flags=re.IGNORECASE):
        return html

    canonical = f'  <link rel="canonical" href="{site_base}/{filename}">\n'
    return re.sub(r"</head>", canonical + "</head>", html, flags=re.IGNORECASE, count=1)


def load_articles() -> list[dict[str, Any]]:
    """Load articles.json (array of objects)."""
    p = Path("articles.json")
    if not p.exists():
        return []

    try:
        data = json.loads(read_text(p))
    except json.JSONDecodeError:
        return []

    if not isinstance(data, list):
        return []

    out: list[dict[str, Any]] = []
    for x in data:
        if isinstance(x, dict) and x.get("title") and x.get("url"):
            out.append(x)
    return out


def _rank(item: dict[str, Any]) -> int:
    """Rank helper."""
    r = item.get("rank")
    return r if isinstance(r, int) else 9999


def related_for(filename: str, articles: list[dict[str, Any]], k: int = 6) -> list[dict[str, Any]]:
    """
    Pick related posts for a given filename.
    Strategy:
      1) Same category by rank
      2) Featured by rank
      3) Any remaining by rank
    """
    self_item = next((a for a in articles if a.get("url") == filename), None)
    category = (self_item.get("category") if self_item else "other") or "other"

    same_cat = [
        a for a in articles
        if a.get("url") != filename and (a.get("category") or "other") == category
    ]
    same_cat.sort(key=lambda x: (_rank(x), str(x.get("title") or "")))

    featured = [a for a in articles if a.get(
        "url") != filename and a.get("featured") is True]
    featured.sort(key=lambda x: (_rank(x), str(x.get("title") or "")))

    merged: list[dict[str, Any]] = []
    seen: set[str] = set()

    def push_list(lst: list[dict[str, Any]]) -> None:
        for a in lst:
            url = str(a.get("url") or "")
            if not url or url in seen:
                continue
            seen.add(url)
            merged.append(a)
            if len(merged) >= k:
                return

    push_list(same_cat)
    push_list(featured)
    if len(merged) < k:
        rest = [a for a in articles if a.get("url") != filename]
        rest.sort(key=lambda x: (_rank(x), str(x.get("title") or "")))
        push_list(rest)

    return merged[:k]


def build_wrapper(page_title: str, original_inner: str, rel: list[dict[str, Any]]) -> str:
    """Build wrapper HTML for one page."""
    related_links = "\n".join(
        [
            f'      <li><a href="{escape_html(a["url"])}">{escape_html(a["title"])}</a></li>'
            for a in rel
        ]
    )
    if not related_links:
        related_links = '      <li><span style="color:#a9b7d6;">No related posts yet.</span></li>'

    original_block = indent_lines(original_inner, 4)

    return f"""{WRAP_MARKER_START}
<div class="site">

  <header class="header">
    <div class="brand">
      <div class="brand-title">CS Homework &amp; Exam Solutions</div>
      <div class="brand-sub">{escape_html(page_title)}</div>
    </div>

    <nav class="nav">
      <a href="index.html#top">Home</a>
      <a href="index.html#top-examples">Top Examples</a>
      <a href="articles.html">All Articles</a>
      <a href="index.html#faq">FAQ</a>
    </nav>
  </header>

  <main class="card">
{original_block}
  </main>

  <section class="card">
    <h2>Related Examples</h2>
    <p>Auto-generated links to help you continue practicing.</p>
    <ul class="links">
{related_links}
    </ul>
  </section>

  <div class="footer">
    <span class="badge">Updated • {TODAY}</span>
  </div>

</div>
{WRAP_MARKER_END}"""


def unify_all_pages() -> None:
    """Unify all root article pages (excluding index/articles)."""
    site_base = os.environ.get("SITE_BASE", "").rstrip("/")
    articles = load_articles()

    html_files = sorted(Path(".").glob("*.html"))
    for path in html_files:
        if path.name in EXCLUDE:
            continue

        html = read_text(path)

        html = ensure_viewport_and_charset(html)
        html = ensure_stylesheet_in_head(html)

        title = extract_title(html, path.stem.replace("-", " ").title())

        body_inner = extract_body_inner(html)

        # If already wrapped, recover original content from main.
        recovered = extract_existing_main(body_inner)
        if recovered is not None:
            original_inner = recovered
        else:
            original_inner = body_inner

        rel = related_for(path.name, articles, k=6)
        wrapper = build_wrapper(title, original_inner, rel)

        html = set_body_preserve_attrs(html, wrapper)
        html = ensure_canonical(html, site_base, path.name)

        write_text(path, html)

    print("OK: unified article pages.")


if __name__ == "__main__":
    unify_all_pages()
