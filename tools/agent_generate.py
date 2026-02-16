"""
Generate ONE new article per run, with auto-expanded topic bank.

Flow:
1) Expand topic bank automatically (tools/topic_expand.py -> topic_bank_auto.json)
2) Scan existing root HTML article pages
3) Pick next missing topic from topic_bank_auto.json (rank -> category balance -> stable)
4) Generate HTML via OpenAI
5) Rebuild sitemap.xml, articles.json, faq.json
6) Unify article pages (tools/unify_pages.py)

Required env:
- OPENAI_API_KEY
- SITE_BASE
Optional env:
- MODEL (default: gpt-5-mini)
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from openai import OpenAI

TODAY = date.today().isoformat()
SITE_BASE = os.environ.get("SITE_BASE", "").rstrip("/")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
MODEL = os.environ.get("MODEL", "gpt-5-mini")

if not SITE_BASE:
    raise SystemExit("ERROR: Missing SITE_BASE environment variable.")
if not OPENAI_API_KEY:
    raise SystemExit("ERROR: Missing OPENAI_API_KEY environment variable.")

client = OpenAI(api_key=OPENAI_API_KEY)

CATEGORY_ORDER = ["scheduling", "page-replacement",
                  "deadlock", "parsing", "other"]
BANK_PATH = Path("topic_bank_auto.json")

FEATURED_DEFAULT: Dict[str, Tuple[bool, int]] = {
    "page-replacement-fifo-example.html": (True, 1),
    "lru-page-replacement-example.html": (True, 2),
    "round-robin-scheduling-example-with-calculation.html": (True, 1),
    "banker-algorithm-example-step-by-step.html": (True, 1),
    "shift-reduce-parsing-example-step-by-step.html": (True, 1),
}


def read_text(path: Path) -> str:
    """Read UTF-8 text (ignore errors)."""
    return path.read_text(encoding="utf-8", errors="ignore")


def write_text(path: Path, text: str) -> None:
    """Write UTF-8 text."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, obj: Any) -> None:
    """Write JSON UTF-8 pretty."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False,
                    indent=2), encoding="utf-8")


def list_existing_article_html() -> List[str]:
    """List root-level *.html except index.html/articles.html."""
    pages: List[str] = []
    for p in Path(".").glob("*.html"):
        if p.name in {"index.html", "articles.html"}:
            continue
        pages.append(p.name)
    return sorted(pages)


def run_topic_expand() -> None:
    """Expand topic bank automatically."""
    subprocess.run(["python", "tools/topic_expand.py"], check=True)
    if not BANK_PATH.exists():
        raise SystemExit("ERROR: topic_bank_auto.json not generated.")


def load_topic_bank() -> List[Dict[str, Any]]:
    """Load topic_bank_auto.json."""
    data = json.loads(read_text(BANK_PATH))
    if not isinstance(data, list):
        return []
    out: List[Dict[str, Any]] = []
    for x in data:
        if not isinstance(x, dict):
            continue
        if not x.get("filename") or not x.get("prompt") or not x.get("category"):
            continue
        out.append(x)
    return out


def extract_title(html: str) -> str:
    """Extract <title> or first <h1>."""
    m = re.search(r"<title>(.*?)</title>", html,
                  flags=re.IGNORECASE | re.DOTALL)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html,
                  flags=re.IGNORECASE | re.DOTALL)
    if m:
        text = re.sub(r"<[^>]+>", "", m.group(1))
        return re.sub(r"\s+", " ", text).strip()
    return "Worked Example"


def extract_meta(html: str, name: str) -> str:
    """Extract meta name content."""
    pattern = r'<meta\s+name=["\']' + \
        re.escape(name) + r'["\']\s+content=["\'](.*?)["\']'
    m = re.search(pattern, html, flags=re.IGNORECASE | re.DOTALL)
    if not m:
        return ""
    return re.sub(r"\s+", " ", m.group(1)).strip()


def category_balance_order(existing_files: Set[str], bank: List[Dict[str, Any]]) -> List[str]:
    """Prefer categories with fewer existing pages (based on bank mapping when possible)."""
    bank_by_file = {str(t.get("filename")): t for t in bank}

    counts = {c: 0 for c in CATEGORY_ORDER}
    for f in existing_files:
        meta = bank_by_file.get(f, {})
        cat = str(meta.get("category") or "other")
        if cat not in counts:
            counts[cat] = 0
        counts[cat] += 1

    return sorted(
        CATEGORY_ORDER,
        key=lambda c: (counts.get(c, 0), CATEGORY_ORDER.index(c)),
    )


def pick_next_topic(existing_files: Set[str], bank: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Pick one missing topic automatically.
    Strategy:
      1) Category balance (fewer pages first)
      2) Rank ascending
      3) Stable order
    """
    cats = category_balance_order(existing_files, bank)

    def rank_of(t: Dict[str, Any]) -> int:
        r = t.get("rank")
        return r if isinstance(r, int) else 999

    for cat in cats:
        candidates = [
            t for t in bank
            if str(t.get("category")) == cat and str(t.get("filename")) not in existing_files
        ]
        candidates.sort(key=lambda t: (rank_of(t), str(t.get("filename"))))
        if candidates:
            return candidates[0]

    leftovers = [t for t in bank if str(
        t.get("filename")) not in existing_files]
    leftovers.sort(key=lambda t: (rank_of(t), str(t.get("filename"))))
    return leftovers[0] if leftovers else None


def generate_one_new_page(topic: Dict[str, Any]) -> str:
    """Generate one new HTML page from a topic bank item."""
    fname = str(topic["filename"])
    prompt = str(topic["prompt"]).strip()

    final_prompt = (
        prompt
        + "\n\nIMPORTANT:\n"
        "- Output ONLY a valid complete HTML document.\n"
        "- Include <meta charset>, viewport, <title>, meta description, meta keywords.\n"
        "- Use exam-style step-by-step solution.\n"
        "- Use <table border=\"1\"> for the step table.\n"
    )

    resp = client.responses.create(model=MODEL, input=final_prompt)
    html = (resp.output_text or "").strip()

    if "<html" not in html.lower() or "</html>" not in html.lower():
        raise RuntimeError(f"Model output for {fname} doesn't look like HTML.")

    write_text(Path(fname), html)
    return fname


def build_sitemap() -> None:
    """Rebuild sitemap.xml from current root html files."""
    urls: List[str] = []

    urls.append(
        "  <url>\n"
        f"    <loc>{SITE_BASE}/</loc>\n"
        f"    <lastmod>{TODAY}</lastmod>\n"
        "    <changefreq>weekly</changefreq>\n"
        "    <priority>1.0</priority>\n"
        "  </url>"
    )
    urls.append(
        "  <url>\n"
        f"    <loc>{SITE_BASE}/articles.html</loc>\n"
        f"    <lastmod>{TODAY}</lastmod>\n"
        "    <changefreq>weekly</changefreq>\n"
        "    <priority>0.9</priority>\n"
        "  </url>"
    )

    for name in list_existing_article_html():
        priority = "0.9" if "example" in name else "0.8"
        urls.append(
            "  <url>\n"
            f"    <loc>{SITE_BASE}/{name}</loc>\n"
            f"    <lastmod>{TODAY}</lastmod>\n"
            "    <changefreq>monthly</changefreq>\n"
            f"    <priority>{priority}</priority>\n"
            "  </url>"
        )

    content = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n\n'
        + "\n\n".join(urls)
        + "\n\n</urlset>\n"
    )
    write_text(Path("sitemap.xml"), content)


def guess_category(filename: str, title: str) -> str:
    """Infer category by keywords."""
    f = filename.lower()
    t = title.lower()

    def has(*words: str) -> bool:
        return any(w in f or w in t for w in words)

    if has("page-replacement", "fifo", "lru", "belady", "clock", "second-chance", "optimal"):
        return "page-replacement"
    if has("scheduling", "round-robin", "sjf", "srtf", "priority", "mlfq", "fcfs"):
        return "scheduling"
    if has("deadlock", "banker", "allocation", "resource"):
        return "deadlock"
    if has("parsing", "shift-reduce", "first", "follow", "ll1", "left-recursion"):
        return "parsing"
    return "other"


def build_articles_json(bank: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Rebuild articles.json from HTML pages and topic bank metadata when available."""
    bank_by_file = {str(t.get("filename")): t for t in bank}
    articles: List[Dict[str, Any]] = []

    for fname in list_existing_article_html():
        html = read_text(Path(fname))
        title = extract_title(html)
        desc = extract_meta(html, "description")
        keywords = extract_meta(html, "keywords")

        meta = bank_by_file.get(fname, {})
        category = str(meta.get("category") or guess_category(fname, title))
        tags = str(meta.get("tags") or keywords or category)

        featured = bool(meta.get("featured")) if "featured" in meta else False
        rank_val = meta.get("rank", None)

        if fname in FEATURED_DEFAULT and "featured" not in meta:
            featured, default_rank = FEATURED_DEFAULT[fname]
            if rank_val is None:
                rank_val = default_rank

        if rank_val is None:
            rank_val = 99

        if not desc:
            desc = f"Step-by-step {category} worked example with tables and calculations."

        articles.append(
            {
                "title": title,
                "url": fname,
                "category": category,
                "tags": tags,
                "description": desc,
                "featured": featured,
                "rank": int(rank_val),
            }
        )

    def cat_index(c: str) -> int:
        try:
            return CATEGORY_ORDER.index(c)
        except ValueError:
            return 999

    articles.sort(key=lambda a: (
        cat_index(a["category"]), a["rank"], a["title"].lower()))
    write_json(Path("articles.json"), articles)
    return articles


def build_faq_json(articles: List[Dict[str, Any]]) -> None:
    """Build FAQ JSON automatically (dynamic counts)."""
    total = len(articles)
    cats = sorted({(a.get("category") or "other") for a in articles})
    featured_count = sum(1 for a in articles if a.get("featured") is True)

    faq = [
        {"q": "Are these real exam-style questions?",
            "a": "Yes. The examples are written to match common university exam styles."},
        {"q": "Do you show full calculations?",
            "a": "Yes. Each page includes step-by-step tables and calculations with final answers."},
        {"q": "How many worked examples are available right now?",
            "a": f"Currently {total} worked example pages across {len(cats)} categories. Featured/top examples: {featured_count}."},
        {"q": "Do I need to edit index.html after adding new pages?",
            "a": "No. Homepage uses articles.json and faq.json, which are auto-rebuilt."},
    ]
    write_json(Path("faq.json"), faq)


def unify_pages() -> None:
    """Run tools/unify_pages.py to apply wrapper layout + related posts."""
    subprocess.run(["python", "tools/unify_pages.py"], check=True)


def main() -> None:
    """Entry point."""
    run_topic_expand()
    bank = load_topic_bank()

    existing_files = set(list_existing_article_html())
    topic = pick_next_topic(existing_files, bank)

    created: Optional[str] = None
    if topic is not None:
        created = generate_one_new_page(topic)
        print(f"Generated new page: {created}")
    else:
        print("No available topic to generate (topic bank exhausted).")

    build_sitemap()
    articles = build_articles_json(bank)
    build_faq_json(articles)
    unify_pages()

    if created:
        print("DONE: expanded bank + generated + rebuilt + unified.")
    else:
        print("DONE: expanded bank + rebuilt + unified (no new page).")


if __name__ == "__main__":
    main()
