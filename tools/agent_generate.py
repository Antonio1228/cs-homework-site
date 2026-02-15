import os
from datetime import date
from pathlib import Path

from openai import OpenAI

# ======================
# Basic config
# ======================
SITE_BASE = os.environ.get("SITE_BASE", "").rstrip("/")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

if not SITE_BASE:
    raise SystemExit("ERROR: Missing SITE_BASE environment variable.")
if not OPENAI_API_KEY:
    raise SystemExit("ERROR: Missing OPENAI_API_KEY environment variable.")

client = OpenAI(api_key=OPENAI_API_KEY)
TODAY = date.today().isoformat()

# ======================
# Topic queue (you can expand this)
# Each run will generate ONE page that doesn't exist yet.
# ======================
TASKS = [
    {
        "filename": "belady-anomaly-example.html",
        "prompt": """Write a complete HTML page (no external CSS/JS) for:
Title: Belady's Anomaly Example (FIFO) with Step by Step Solution

Requirements:
- Use plain HTML similar to simple OS homework solution pages.
- Include:
  1) Short intro of Belady's anomaly
  2) Given: reference string and frames
  3) FIFO step-by-step table for 3 frames (show frames content each step + page fault yes/no)
  4) FIFO step-by-step table for 4 frames
  5) Total page faults comparison and conclusion showing anomaly (faults increase with more frames)
- Keep it easy to read, use <h1>, <h2>, <p>, <pre>, and <table border="1">.
- Add <meta charset>, viewport, <title>, meta description, meta keywords.
Output ONLY the final HTML document."""
    },
    {
        "filename": "round-robin-with-arrival-time-example.html",
        "prompt": """Write a complete HTML page (no external CSS/JS) for:
Topic: Round Robin Scheduling Example with Arrival Times (Step by Step)

Requirements:
- Include a table of processes with Arrival Time and Burst Time and Time Quantum.
- Show Gantt chart and compute Completion Time, Turnaround Time, Waiting Time, and averages.
- Use simple HTML like OS homework solution pages.
- Add <title>, meta description, meta keywords.
Output ONLY the final HTML document."""
    },
    {
        "filename": "preemptive-sjf-srtf-example.html",
        "prompt": """Write a complete HTML page (no external CSS/JS) for:
Topic: Preemptive Shortest Job First (SRTF) Scheduling Example (Step by Step)

Requirements:
- Include processes with Arrival Time and Burst Time.
- Show timeline / Gantt chart and compute Completion Time, Turnaround Time, Waiting Time, and averages.
- Use simple HTML like OS homework solution pages.
- Add <title>, meta description, meta keywords.
Output ONLY the final HTML document."""
    }
]

# ======================
# Helpers
# ======================


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def list_existing_html() -> list[str]:
    # All .html in repo root (excluding index.html)
    return sorted([p.name for p in Path(".").glob("*.html") if p.name != "index.html"])


def build_sitemap() -> None:
    """
    Rebuild sitemap.xml from current root html files (plus homepage).
    This is safest: no missing urls, no stale urls.
    """
    urls = []

    # Homepage
    urls.append(f"""  <url>
    <loc>{SITE_BASE}/</loc>
    <lastmod>{TODAY}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>1.0</priority>
  </url>""")

    # Article pages
    for name in list_existing_html():
        # Give slightly higher priority to "core" examples
        priority = "0.9" if "example" in name else "0.8"
        urls.append(f"""  <url>
    <loc>{SITE_BASE}/{name}</loc>
    <lastmod>{TODAY}</lastmod>
    <changefreq>monthly</changefreq>
    <priority>{priority}</priority>
  </url>""")

    content = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n\n'
        + "\n\n".join(urls)
        + "\n\n</urlset>\n"
    )
    write_text(Path("sitemap.xml"), content)


def generate_one_new_page() -> str | None:
    """
    Generate ONE page that does not exist yet.
    Return filename if generated, else None.
    """
    existing = set(list_existing_html())

    for task in TASKS:
        fname = task["filename"]
        if fname in existing:
            continue

        # Call OpenAI
        resp = client.responses.create(
            model="gpt-5-mini",
            input=task["prompt"]
        )
        html = resp.output_text.strip()

        # Basic sanity check
        if "<html" not in html.lower() or "</html>" not in html.lower():
            raise RuntimeError(
                f"Model output for {fname} doesn't look like HTML.")

        write_text(Path(fname), html)
        return fname

    return None


def main():
    created = generate_one_new_page()

    # Always rebuild sitemap (so lastmod updates + includes everything)
    build_sitemap()

    if created:
        print(f"Generated: {created}")
    else:
        print("No new page generated (all tasks already exist). Sitemap rebuilt.")


if __name__ == "__main__":
    main()
