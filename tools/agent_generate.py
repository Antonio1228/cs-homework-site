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

import hashlib
import json
import os
import re
import subprocess
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

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

CATEGORY_ORDER = ["scheduling", "page-replacement", "deadlock", "parsing"]


def read_text(path: Path) -> str:
    """Read a UTF-8 text file (ignore decode errors)."""
    return path.read_text(encoding="utf-8", errors="ignore")


def write_text(path: Path, content: str) -> None:
    """Write a UTF-8 text file."""
    path.write_text(content, encoding="utf-8")


def load_json(path: Path, default: Any) -> Any:
    """Load JSON file; return default if missing/invalid."""
    if not path.exists():
        return default
    try:
        return json.loads(read_text(path))
    except json.JSONDecodeError:
        return default


def list_existing_article_files() -> Set[str]:
    """List existing root article HTML filenames (exclude non-article pages)."""
    exclude = {"index.html", "articles.html", "404.html"}
    existing: Set[str] = set()
    for p in Path(".").glob("*.html"):
        if p.name in exclude:
            continue
        existing.add(p.name)
    return existing


def load_topic_bank() -> List[Dict[str, Any]]:
    """Load topic_bank_auto.json entries with required fields."""
    bank = load_json(Path("topic_bank_auto.json"), [])
    if not isinstance(bank, list):
        return []
    return [
        x
        for x in bank
        if isinstance(x, dict) and "filename" in x and "prompt" in x
    ]


def rank_of(topic: Dict[str, Any]) -> int:
    """Get numeric rank from a topic entry (lower is better)."""
    try:
        return int(topic.get("rank", 9999))
    except (TypeError, ValueError):
        return 9999


def category_of(topic: Dict[str, Any]) -> str:
    """Get category string from a topic entry."""
    c = str(topic.get("category", "")).strip()
    return c if c else "misc"


def category_priority(cat: str) -> int:
    """Map category to a stable priority for balancing."""
    return CATEGORY_ORDER.index(cat) if cat in CATEGORY_ORDER else 999


def choose_next_topic(
    bank: List[Dict[str, Any]],
    existing_files: Set[str],
) -> Optional[Dict[str, Any]]:
    """
    Pick next topic:
    - Not yet generated (filename not in existing_files)
    - Prefer lower rank
    - Among best-rank items, prefer earlier category in CATEGORY_ORDER
    - Stable fallback by filename
    """
    leftovers = [
        t for t in bank if str(t.get("filename")) not in existing_files
    ]
    if not leftovers:
        return None

    leftovers.sort(key=lambda t: (rank_of(t), str(t.get("filename"))))

    best_rank = rank_of(leftovers[0])
    top = [t for t in leftovers if rank_of(t) == best_rank]

    top.sort(
        key=lambda t: (
            category_priority(category_of(t)),
            str(t.get("filename")),
        )
    )
    return top[0] if top else leftovers[0]


def generate_one_new_page(topic: Dict[str, Any]) -> str:
    """Generate one new HTML page from a topic bank item using OpenAI."""
    fname = str(topic["filename"])
    prompt = str(topic["prompt"]).strip()

    seed_hex = hashlib.sha256((fname + TODAY).encode("utf-8")).hexdigest()[:8]
    uniqueness_seed = int(seed_hex, 16)

    final_prompt = (
        prompt
        + "\n\nIMPORTANT (quality + uniqueness rules):\n"
        "- Output ONLY a valid complete HTML document. No markdown fences.\n"
        "- Include <meta charset>, viewport, <title>, meta description, meta keywords.\n"
        "- No external CSS/JS. Use only basic HTML tags: "
        "<h1>, <h2>, <h3>, <p>, <pre>, <table border=\"1\">, <ul>, <li>.\n"
        "- Target length: 1500–2500+ words (dense, exam-style, but readable).\n"
        "- MUST include: (1) problem setup, (2) fully worked example with real numbers, "
        "(3) step-by-step table(s), (4) final numeric answer(s), (5) Common mistakes, "
        "(6) FAQ with 3–5 Q&As.\n"
        "- Use <table border=\"1\"> for all calculation / step tables.\n"
        f"- Uniqueness seed for this page: {uniqueness_seed}. Use it to choose ALL "
        "numeric values (reference string / burst times / arrivals / etc.) so this page "
        "is different from others.\n"
        "- Do NOT reuse the same example numbers across pages. "
        "Do NOT use placeholders like 'X', 'Y', '...'.\n"
        "- Vary wording and section headings naturally (avoid looking like a rigid template).\n"
    )

    resp = client.responses.create(model=MODEL, input=final_prompt)
    html = (resp.output_text or "").strip()

    html_lower = html.lower()
    if "<html" not in html_lower or "</html>" not in html_lower:
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
        "  </url>\n"
    )
    urls.append(
        "  <url>\n"
        f"    <loc>{SITE_BASE}/articles.html</loc>\n"
        f"    <lastmod>{TODAY}</lastmod>\n"
        "  </url>\n"
    )

    exclude = {"index.html", "articles.html", "404.html"}
    for p in sorted(Path(".").glob("*.html")):
        if p.name in exclude:
            continue
        urls.append(
            "  <url>\n"
            f"    <loc>{SITE_BASE}/{p.name}</loc>\n"
            f"    <lastmod>{TODAY}</lastmod>\n"
            "  </url>\n"
        )

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "".join(urls)
        + "</urlset>\n"
    )
    write_text(Path("sitemap.xml"), xml)


def extract_title_from_html(html: str, fallback: str) -> str:
    """Extract <title> from HTML; fallback if missing."""
    m = re.search(r"<title>(.*?)</title>", html,
                  flags=re.IGNORECASE | re.DOTALL)
    if not m:
        return fallback
    t = re.sub(r"\s+", " ", m.group(1)).strip()
    return t if t else fallback


def rebuild_articles_json() -> None:
    """Rebuild articles.json from current root html files."""
    exclude = {"index.html", "articles.html", "404.html"}

    items: List[Dict[str, Any]] = []
    for p in sorted(Path(".").glob("*.html")):
        if p.name in exclude:
            continue
        html = read_text(p)
        title = extract_title_from_html(html, p.stem.replace("-", " "))
        items.append(
            {
                "url": p.name,
                "title": title,
                "category": "",
                "updated": TODAY,
            }
        )

    write_text(Path("articles.json"), json.dumps(
        items, ensure_ascii=False, indent=2))


def rebuild_faq_json() -> None:
    """Create a minimal faq.json if missing (keeps index stable)."""
    p = Path("faq.json")
    if p.exists():
        return
    faq = [
        {
            "q": "How are these examples generated?",
            "a": "Each page is an exam-style worked example with step tables and final answers.",
        },
        {
            "q": "Can I request a topic?",
            "a": "Yes. New topics are added continuously based on common search suggestions.",
        },
        {
            "q": "Do pages include worked calculations?",
            "a": "Yes. Tables show each step and the final numeric results.",
        },
    ]
    write_text(p, json.dumps(faq, ensure_ascii=False, indent=2))


def run_unify_pages() -> None:
    """Run tools/unify_pages.py to wrap all pages consistently."""
    subprocess.run(["python", "tools/unify_pages.py"], check=True)


def run_topic_expand() -> None:
    """Run tools/topic_expand.py to refresh topic_bank_auto.json."""
    subprocess.run(["python", "tools/topic_expand.py"], check=True)


def main() -> None:
    """Entry point."""
    run_topic_expand()

    bank = load_topic_bank()
    if not bank:
        raise SystemExit("ERROR: topic_bank_auto.json is empty or invalid.")

    existing = list_existing_article_files()
    topic = choose_next_topic(bank, existing)
    if not topic:
        raise SystemExit(
            "No new topics to generate (all filenames already exist).")

    created = generate_one_new_page(topic)
    print(f"Generated: {created}")

    rebuild_articles_json()
    rebuild_faq_json()
    build_sitemap()
    run_unify_pages()


if __name__ == "__main__":
    main()
