"""
Auto-expand topic bank via public search suggestions (no API key required).

Data source:
- Google Suggest endpoint: suggestqueries.google.com (client=firefox)

Output:
- topic_bank_auto.json (repo root)

Behavior:
- If network fails, keep existing bank (no crash).
- Dedupe by (filename + normalized title).
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
    Seed("scheduling", "mlfq scheduling example"),
    Seed("page-replacement", "fifo page replacement example"),
    Seed("page-replacement", "lru page replacement example"),
    Seed("page-replacement", "optimal page replacement example"),
    Seed("page-replacement", "belady anomaly example"),
    Seed("deadlock", "banker's algorithm example"),
    Seed("deadlock", "deadlock detection algorithm example"),
    Seed("deadlock", "resource allocation graph deadlock example"),
    Seed("parsing", "shift reduce parsing example"),
    Seed("parsing", "first follow example"),
    Seed("parsing", "left recursion elimination example"),
]


def normalize_title(text: str) -> str:
    """Normalize string for stable dedupe."""
    norm = unicodedata.normalize("NFKC", text)
    norm = norm.strip().lower()
    norm = re.sub(r"\s+", " ", norm)
    return norm


def safe_filename_from_title(title: str) -> str:
    """Create a safe slug filename from title."""
    t = normalize_title(title)
    t = re.sub(r"[^a-z0-9\s\-']", "", t)
    t = t.replace("'", "")
    t = re.sub(r"\s+", "-", t).strip("-")
    if not t:
        t = "article"
    return f"{t}.html"


def looks_banned(text: str) -> bool:
    """Return True if the title contains banned substrings."""
    sl = normalize_title(text)
    return any(b in sl for b in BANNED_SUBSTRINGS)


def fetch_suggestions(query: str, timeout: float = 10.0) -> List[str]:
    """Fetch Google Suggest suggestions for a query."""
    q = quote(query)
    url = (
        "https://suggestqueries.google.com/complete/search?"
        f"client=firefox&q={q}"
    )
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode("utf-8", errors="ignore")
        arr = json.loads(data)
        if isinstance(arr, list) and len(arr) >= 2 and isinstance(arr[1], list):
            return [str(x) for x in arr[1]]
        return []
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
        return []


def build_prompt(category: str, suggestion: str) -> str:
    """Build a high-quality exam-style generation prompt from suggestion + category."""
    title = suggestion.strip()

    base_rules = (
        "Write a complete HTML page (no external CSS/JS). "
        "Output ONLY the final HTML document.\n\n"
        "Hard requirements (must follow ALL):\n"
        "- Include <meta charset>, viewport, <title>, meta description, meta keywords.\n"
        "- Use exam-style step-by-step solution with real numeric values.\n"
        "- Use simple HTML tags only: <h1>, <h2>, <h3>, <p>, <pre>, "
        "<table border=\"1\">, <ul>, <li>.\n"
        "- MUST include multiple worked tables/calculations and final numeric answer(s).\n"
        "- Target length: 1500–2500+ words (rich explanation, not fluff).\n"
        "- Add sections: Problem Setup, Step-by-step Solution, Final Answers, "
        "Common Mistakes, FAQ (3–5 Q&As), and a short Conclusion.\n"
        "- Avoid repetitive boilerplate phrasing. Vary the narrative naturally "
        "while keeping accuracy.\n"
        "- Do NOT include external links. Do NOT mention 'AI' or the prompt.\n"
    )

    if category == "scheduling":
        specific = (
            "Topic: CPU Scheduling worked example.\n"
            "- Provide a process table (Process, Arrival Time, Burst Time; "
            "include Priority ONLY if needed by the topic).\n"
            "- Choose a realistic time quantum if Round Robin is involved.\n"
            "- Show a Gantt chart (text is fine).\n"
            "- Compute Completion Time, Turnaround Time, Waiting Time for each "
            "process and the averages.\n"
            "- Include at least one short 'extra practice' question at the end "
            "(with final answers).\n"
        )
    elif category == "page-replacement":
        specific = (
            "Topic: Page Replacement worked example.\n"
            "- Provide a reference string and number of frames.\n"
            "- Show the frame table step-by-step and count page faults clearly.\n"
            "- If the topic compares algorithms (e.g., FIFO vs LRU vs OPT), "
            "compute page faults for EACH and summarize in a comparison table.\n"
            "- Conclude total page faults (and page fault rate if you choose to add it).\n"
            "- Include at least one short 'extra practice' question at the end "
            "(with final answers).\n"
        )
    elif category == "deadlock":
        specific = (
            "Topic: Deadlock / Banker / Detection worked example.\n"
            "- Provide Allocation / Max / Available (or a resource-allocation graph) "
            "with concrete numbers.\n"
            "- Do step-by-step reasoning: Need matrix, safety check iterations, "
            "and safe sequence (or prove unsafe/deadlock).\n"
            "- Include a final summary table (e.g., Work/Finish sequence).\n"
            "- Include at least one short 'extra practice' question at the end "
            "(with final answers).\n"
        )
    elif category == "parsing":
        specific = (
            "Topic: Compiler parsing worked example.\n"
            "- Provide a grammar.\n"
            "- Compute needed sets/tables (FIRST/FOLLOW and parsing table OR LR items/"
            "action-goto), depending on the topic.\n"
            "- Show step-by-step parsing actions on an input string "
            "(stack / input / action).\n"
            "- Conclude accept/reject and include a parse tree or derivation steps "
            "(text form is fine).\n"
            "- Include at least one short 'extra practice' question at the end "
            "(with final answers).\n"
        )
    else:
        specific = (
            "Topic: Worked example. Provide a full step-by-step solution with "
            "tables and final answers.\n"
        )

    return (
        base_rules
        + "\nTitle: " + title + "\n\n"
        + "Topic title (use as H1): " + title + "\n\n"
        + specific
    )


def load_bank() -> List[Dict[str, Any]]:
    """Load topic bank JSON array."""
    if not BANK_PATH.exists():
        return []
    try:
        obj = json.loads(BANK_PATH.read_text(
            encoding="utf-8", errors="ignore"))
        if isinstance(obj, list):
            return [x for x in obj if isinstance(x, dict)]
        return []
    except json.JSONDecodeError:
        return []


def save_bank(bank: List[Dict[str, Any]]) -> None:
    """Save topic bank JSON array."""
    BANK_PATH.write_text(
        json.dumps(bank, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def bank_keys(bank: List[Dict[str, Any]]) -> Set[Tuple[str, str]]:
    """Build dedupe keys: (filename, normalized title)."""
    keys: Set[Tuple[str, str]] = set()
    for it in bank:
        fn = str(it.get("filename", "")).strip()
        title = normalize_title(str(it.get("title_hint", "")).strip())
        if fn and title:
            keys.add((fn, title))
    return keys


def add_item(
    bank: List[Dict[str, Any]],
    keys: Set[Tuple[str, str]],
    category: str,
    suggestion: str,
    seed_query: str,
) -> bool:
    """Try add one new item. Return True if added."""
    title = suggestion.strip()
    if not title:
        return False
    if looks_banned(title):
        return False

    filename = safe_filename_from_title(title)
    key = (filename, normalize_title(title))
    if key in keys:
        return False

    item = {
        "filename": filename,
        "category": category,
        "featured": False,
        "rank": 90,
        "tags": title,
        "title_hint": title,
        "prompt": build_prompt(category, title),
        "source": "google_suggest",
        "seed": seed_query,
    }
    bank.append(item)
    keys.add(key)
    return True


def main() -> None:
    """Expand topic bank by querying suggestions and appending new deduped entries."""
    bank = load_bank()
    keys = bank_keys(bank)

    new_count = 0
    for sd in SEEDS:
        time.sleep(0.2)
        suggestions = fetch_suggestions(sd.query)

        for sug in suggestions:
            if len(bank) >= MAX_BANK_SIZE:
                break
            if new_count >= NEW_ITEMS_PER_RUN_LIMIT:
                break
            if add_item(bank, keys, sd.category, sug, sd.query):
                new_count += 1

        if len(bank) >= MAX_BANK_SIZE or new_count >= NEW_ITEMS_PER_RUN_LIMIT:
            break

    def sort_key(x: Dict[str, Any]) -> Tuple[int, str, str]:
        """Stable sort key for topic bank."""
        try:
            r = int(x.get("rank", 9999))
        except (TypeError, ValueError):
            r = 9999
        c = str(x.get("category", ""))
        f = str(x.get("filename", ""))
        return (r, c, f)

    bank.sort(key=sort_key)
    save_bank(bank)


if __name__ == "__main__":
    main()
