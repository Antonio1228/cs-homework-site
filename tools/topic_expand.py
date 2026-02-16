"""
Auto-expand topic bank via public search suggestions (no API key required).

Data source:
- Google Suggest endpoint: suggestqueries.google.com (client=firefox)

Output:
- topic_bank_auto.json (repo root)

Safety:
- If network fails, it will keep existing bank (no crash).
- Dedupe by filename + normalized title.
- Hard limits to prevent unlimited growth.
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

BANK_PATH = Path("topic_bank_auto.json")

# Hard limits to keep repo stable
MAX_BANK_SIZE = 260
NEW_ITEMS_PER_RUN_LIMIT = 50

# Filter out low-quality / spammy suggestions
BANNED_SUBSTRINGS = [
    "pdf",
    "ppt",
    "slides",
    "download",
    "solution manual",
    "github",
    "youtube",
    "quizlet",
    "chegg",
    "coursehero",
]


@dataclass(frozen=True)
class Seed:
    """Query seed used to fetch suggestion expansions."""
    category: str
    query: str


# High-yield seeds (exam frequency + SEO)
SEEDS: List[Seed] = [
    Seed("scheduling", "round robin scheduling example"),
    Seed("scheduling", "preemptive priority scheduling example"),
    Seed("scheduling", "sjf srtf scheduling example"),
    Seed("scheduling", "mlfq scheduling example"),
    Seed("scheduling", "cpu scheduling gantt chart example"),
    Seed("page-replacement", "fifo page replacement example"),
    Seed("page-replacement", "lru page replacement example"),
    Seed("page-replacement", "optimal page replacement example"),
    Seed("page-replacement", "second chance clock page replacement example"),
    Seed("page-replacement", "belady anomaly example"),
    Seed("deadlock", "banker's algorithm example"),
    Seed("deadlock", "banker's request algorithm example"),
    Seed("deadlock", "deadlock detection algorithm example"),
    Seed("deadlock", "resource allocation graph deadlock example"),
    Seed("parsing", "shift reduce parsing example"),
    Seed("parsing", "first follow example"),
    Seed("parsing", "ll1 parsing table example"),
    Seed("parsing", "left recursion elimination example"),
]


def read_json(path: Path) -> Any:
    """Read JSON file; return None if missing/invalid."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def write_json(path: Path, obj: Any) -> None:
    """Write JSON UTF-8 pretty."""
    path.write_text(json.dumps(obj, ensure_ascii=False,
                    indent=2), encoding="utf-8")


def fetch_google_suggest(query: str, timeout: int = 12) -> List[str]:
    """
    Fetch suggestions from Google Suggest endpoint.

    Endpoint:
      https://suggestqueries.google.com/complete/search?client=firefox&q=...

    Response JSON:
      [query, [suggestions...], ...]
    """
    url = (
        "https://suggestqueries.google.com/complete/search"
        "?client=firefox&q="
        + quote(query)
    )
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; cs-homework-site-bot/1.0)"},
        method="GET",
    )

    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="ignore")

    data = json.loads(raw)
    if isinstance(data, list) and len(data) >= 2 and isinstance(data[1], list):
        return [str(x) for x in data[1] if x]
    return []


def normalize_text(s: str) -> str:
    """Normalize text for dedupe."""
    s = unicodedata.normalize("NFKC", s)
    s = s.strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def is_bad_topic(s: str) -> bool:
    """Heuristic filter for suggestion quality."""
    t = normalize_text(s)

    if len(t) < 8:
        return True

    if any(b in t for b in BANNED_SUBSTRINGS):
        return True

    # Require exam-ish intent
    if ("example" not in t) and ("algorithm" not in t) and ("parsing" not in t):
        return True

    return False


def slugify_filename(topic: str) -> str:
    """
    Convert suggestion text into a stable filename.
    Result always ends with .html
    """
    t = normalize_text(topic)

    # normalize apostrophes
    t = t.replace("banker's", "bankers").replace("banker’s", "bankers")
    t = t.replace("(srtf)", "srtf").replace("(opt)", "opt")

    # keep alnum and spaces
    t2 = re.sub(r"[^a-z0-9\s]+", " ", t)
    t2 = re.sub(r"\s+", " ", t2).strip()

    words = t2.split(" ")[:10]
    base = "-".join(words).strip("-") or "worked-example"

    if "example" not in base:
        base = f"{base}-example"

    return f"{base}.html"


def infer_category(seed_category: str, suggestion: str) -> str:
    """Infer category based on keywords; fallback to seed category."""
    t = normalize_text(suggestion)

    if any(k in t for k in ["fifo", "lru", "optimal", "second chance", "clock", "belady", "page replacement"]):
        return "page-replacement"

    if any(k in t for k in ["round robin", "sjf", "srtf", "priority scheduling", "mlfq", "cpu scheduling"]):
        return "scheduling"

    if any(k in t for k in ["banker", "deadlock", "resource allocation", "rag", "safety"]):
        return "deadlock"

    if any(k in t for k in ["parsing", "first", "follow", "ll1", "left recursion", "shift reduce", "lr parsing"]):
        return "parsing"

    return seed_category


def build_prompt(category: str, suggestion: str) -> str:
    """Build an exam-style generation prompt from suggestion + category."""
    title = suggestion.strip()

    base_rules = (
        "Write a complete HTML page (no external CSS/JS). "
        "Output ONLY the final HTML document.\n\n"
        "Hard requirements:\n"
        "- Include <meta charset>, viewport, <title>, meta description, meta keywords.\n"
        "- Use exam-style step-by-step solution.\n"
        "- Use simple HTML structure: <h1>, <h2>, <p>, <pre>, <table border=\"1\">.\n"
        "- Must include worked tables/calculations and final answer.\n"
    )

    if category == "scheduling":
        specific = (
            "Topic: CPU Scheduling worked example.\n"
            "- Include process table (Arrival Time, Burst Time, and if needed Priority).\n"
            "- Show Gantt chart (text is fine).\n"
            "- Compute Completion Time, Turnaround Time, Waiting Time, and averages.\n"
        )
    elif category == "page-replacement":
        specific = (
            "Topic: Page Replacement worked example.\n"
            "- Provide reference string and number of frames.\n"
            "- Show step-by-step frame table and page fault count.\n"
            "- Conclude total page faults.\n"
        )
    elif category == "deadlock":
        specific = (
            "Topic: Deadlock / Banker / Detection worked example.\n"
            "- Provide Allocation/Max/Available (or graph) and do step-by-step reasoning.\n"
            "- Show safe sequence or prove deadlock.\n"
        )
    elif category == "parsing":
        specific = (
            "Topic: Compiler parsing worked example.\n"
            "- Provide a grammar.\n"
            "- Compute needed sets/tables (FIRST/FOLLOW, LL(1) table, or parsing steps).\n"
            "- Show step-by-step parsing actions.\n"
        )
    else:
        specific = (
            "Topic: Computer Science worked example.\n"
            "- Provide a clear worked solution with steps and final answer.\n"
        )

    return f"{base_rules}\nTitle: {title}\n\n{specific}\n"


def load_existing_bank() -> List[Dict[str, Any]]:
    """Load existing bank items from topic_bank_auto.json if present."""
    data = read_json(BANK_PATH)
    if not isinstance(data, list):
        return []

    cleaned: List[Dict[str, Any]] = []
    for x in data:
        if not isinstance(x, dict):
            continue
        if x.get("filename") and x.get("prompt") and x.get("category"):
            cleaned.append(x)
    return cleaned


def bank_keys(bank: List[Dict[str, Any]]) -> Tuple[Set[str], Set[str]]:
    """Return sets for dedupe: filenames and normalized title_hint."""
    filenames: Set[str] = set()
    titles: Set[str] = set()

    for x in bank:
        filenames.add(str(x.get("filename", "")).lower())
        title = str(x.get("title_hint", "") or "")
        if title:
            titles.add(normalize_text(title))

    return filenames, titles


def expand_bank() -> None:
    """Expand topic bank based on suggestion queries."""
    bank = load_existing_bank()
    existing_filenames, existing_titles = bank_keys(bank)

    new_items: List[Dict[str, Any]] = []

    for seed in SEEDS:
        try:
            suggestions = fetch_google_suggest(seed.query)
        except (URLError, HTTPError, TimeoutError, json.JSONDecodeError):
            suggestions = []

        suggestions = suggestions[:10]

        for sug in suggestions:
            if is_bad_topic(sug):
                continue

            cat = infer_category(seed.category, sug)
            filename = slugify_filename(sug)
            title_norm = normalize_text(sug)

            if filename.lower() in existing_filenames:
                continue
            if title_norm in existing_titles:
                continue

            item = {
                "filename": filename,
                "category": cat,
                "featured": False,
                "rank": 90,
                "tags": normalize_text(sug),
                "title_hint": sug.strip(),
                "prompt": build_prompt(cat, sug),
                "source": "google_suggest",
                "seed": seed.query,
            }

            new_items.append(item)
            existing_filenames.add(filename.lower())
            existing_titles.add(title_norm)

            if len(new_items) >= NEW_ITEMS_PER_RUN_LIMIT:
                break

        if len(new_items) >= NEW_ITEMS_PER_RUN_LIMIT:
            break

        time.sleep(0.2)

    merged = bank + new_items

    # Dedupe by filename again (safety)
    seen: Set[str] = set()
    deduped: List[Dict[str, Any]] = []
    for x in merged:
        f = str(x.get("filename", "")).lower()
        if not f or f in seen:
            continue
        seen.add(f)
        deduped.append(x)

    if len(deduped) > MAX_BANK_SIZE:
        deduped = deduped[:MAX_BANK_SIZE]

    write_json(BANK_PATH, deduped)
    print(f"OK: topic bank size = {len(deduped)}, added = {len(new_items)}")


if __name__ == "__main__":
    expand_bank()
