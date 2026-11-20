import feedparser
import html
import os
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from urllib.parse import quote

MANILA = ZoneInfo("Asia/Manila")

PAGE_TITLE = "GIA News Tracker Agent V1"
HEADING = "GIA Automated News Tracker"
VERSION = "v1.0"
SUBTITLE = "Updates every 7:00 AM and 3:00 PM Manila time"

WINDOW = "7d"            # how far back to search, e.g. 1d, 3d, 7d
MAX_PER_SECTION = 10     # newest items shown per topic
BLOCKED_SOURCES = []     # e.g. ["Facebook"] to hide noisy sources

TOPICS = {
    "CARS": [
        "Comprehensive Automotive Resurgence Strategy",
        "CARS program Philippines",
    ],
    "RACE": [
        "RACE program Philippines DTI",
        "Philippines automotive RACE incentive",
    ],
    "EVIS": [
        "EVIS Philippines",
        "Electric Vehicle Incentive Strategy Philippines",
    ],
    "Taxes and tariffs": [
        "Philippines automotive excise tax",
        "Philippines vehicle tariff",
    ],
    "EV incentives": [
        "Philippines EV incentives",
    ],
    "Biofuels": [
        "Biofuels Philippines",
    ],
    "Hydrogen": [
        "Hydrogen Philippines",
    ],
    "Carbon trading": [
        "Carbon trading Philippines",
    ],
}


def fetch(query):
    url = (
        "https://news.google.com/rss/search?q="
        f"{quote(query)}+when:{WINDOW}&hl=en-PH&gl=PH&ceid=PH:en"
    )
    return feedparser.parse(url).entries


def parse_entry(e):
    title = e.get("title", "").strip()
    source = (e.get("source") or {}).get("title", "").strip()
    if source and title.endswith(" - " + source):
        title = title[: -(len(source) + 3)]
    elif not source and " - " in title:
        title, source = title.rsplit(" - ", 1)
    if len(title) > 150:
        title = title[:147].rstrip() + "..."
    dt = None
    if e.get("published_parsed"):
        dt = datetime(*e.published_parsed[:6], tzinfo=timezone.utc).astimezone(MANILA)
    return {"title": title, "source": source, "link": e.get("link", ""), "dt": dt}


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


now = datetime.now(MANILA)
seen_links, seen_titles = set(), set()
nav, sections = [], []
total = 0

for heading, queries in TOPICS.items():
    items = []
    for q in queries:
        for e in fetch(q):
            item = parse_entry(e)
            key = item["title"].lower()
            if (
                not item["link"]
                or item["link"] in seen_links
                or key in seen_titles
                or item["source"] in BLOCKED_SOURCES
            ):
                continue
            seen_links.add(item["link"])
            seen_titles.add(key)
            items.append(item)

    items.sort(key=lambda i: i["dt"] or datetime.min.replace(tzinfo=MANILA), reverse=True)
    items = items[:MAX_PER_SECTION]
    total += len(items)

    cards = []
    for i in items:
        meta = []
        if i["source"]:
            meta.append(f'<span class="src">{html.escape(i["source"])}</span>')
        if i["dt"]:
            meta.append(f'<span>{i["dt"]:%b %d, %Y &middot; %I:%M %p}</span>')
            if (now - i["dt"]).total_seconds() < 86400:
                meta.append('<span class="new">New</span>')
        cards.append(
            '<li class="card">'
            f'<a href="{html.escape(i["link"])}" target="_blank" rel="noopener">'
            f'{html.escape(i["title"])}</a>'
            f'<div class="meta">{"".join(meta)}</div></li>'
        )
    if not cards:
        cards = ['<li class="empty">No items found in the last ' + WINDOW + ".</li>"]

    slug = slugify(heading)
    nav.append(
        f'<a href="#{slug}">{html.escape(heading)} <span>{len(items)}</span></a>'
    )
    sections.append(
        f'<section id="{slug}"><h2>{html.escape(heading)}'
        f'<span class="count">{len(items)}</span></h2>'
        f'<ul>{"".join(cards)}</ul></section>'
    )

TEMPLATE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="Automated policy and industry news monitoring.">
<title>__PAGE_TITLE__</title>
<style>
  :root {
    --bg: #f4f6f9; --card: #ffffff; --text: #1b2430; --muted: #667085;
    --line: #e4e7ec; --brand: #0b3d91; --brand-soft: #e8eefb; --new: #067647; --new-bg: #dcfae6;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #0f141b; --card: #171e28; --text: #e8edf4; --muted: #9aa5b4;
      --line: #263041; --brand: #7aa2f7; --brand-soft: #1d2a44; --new: #75e0a7; --new-bg: #0f3a28;
    }
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--text);
         font-family: "Segoe UI", system-ui, -apple-system, Roboto, sans-serif; line-height: 1.5; }
  header { background: var(--brand); color: #fff; padding: 2rem 1rem 2.5rem; }
  @media (prefers-color-scheme: dark) { header { background: #14233f; } }
  .wrap { max-width: 880px; margin: 0 auto; padding: 0 1rem; }
  header .wrap { padding: 0; }
  h1 { margin: 0; font-size: 1.7rem; letter-spacing: -.01em; }
  .badge { font-size: .7rem; font-weight: 600; background: rgba(255,255,255,.2);
           padding: .15rem .55rem; border-radius: 999px; margin-left: .5rem; vertical-align: middle; }
  .sub { margin: .4rem 0 0; opacity: .9; }
  .stamp { margin: .9rem 0 0; font-size: .85rem; opacity: .8; }
  .tools { margin-top: -1.3rem; }
  .search { width: 100%; padding: .85rem 1rem; font-size: 1rem; border: 1px solid var(--line);
            border-radius: 10px; background: var(--card); color: var(--text);
            box-shadow: 0 2px 8px rgba(16,24,40,.08); }
  .chips { display: flex; flex-wrap: wrap; gap: .5rem; margin: 1rem 0 .5rem; }
  .chips a { text-decoration: none; font-size: .85rem; color: var(--brand); background: var(--brand-soft);
             padding: .35rem .75rem; border-radius: 999px; font-weight: 500; }
  .chips a span { opacity: .7; margin-left: .25rem; }
  section { margin-top: 1.75rem; scroll-margin-top: 1rem; }
  h2 { font-size: 1.1rem; margin: 0 0 .6rem; display: flex; align-items: center; gap: .5rem; }
  .count { font-size: .75rem; font-weight: 600; color: var(--muted); background: var(--card);
           border: 1px solid var(--line); border-radius: 999px; padding: .05rem .5rem; }
  ul { list-style: none; margin: 0; padding: 0; display: grid; gap: .6rem; }
  .card { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: .8rem 1rem; }
  .card:hover { border-color: var(--brand); }
  .card a { color: var(--text); text-decoration: none; font-weight: 600; }
  .card a:hover { color: var(--brand); text-decoration: underline; }
  .meta { display: flex; flex-wrap: wrap; align-items: center; gap: .6rem; margin-top: .3rem;
          font-size: .8rem; color: var(--muted); }
  .src { font-weight: 600; color: var(--brand); }
  .new { color: var(--new); background: var(--new-bg); font-weight: 600; font-size: .7rem;
         padding: .05rem .45rem; border-radius: 999px; }
  .empty { color: var(--muted); font-style: italic; padding: .4rem 0; }
  footer { margin: 2.5rem 0 2rem; font-size: .8rem; color: var(--muted); text-align: center; }
  @media (max-width: 600px) { h1 { font-size: 1.4rem; } }
</style></head>
<body>
<header><div class="wrap">
  <h1>__HEADING__<span class="badge">__VERSION__</span></h1>
  <p class="sub">__SUBTITLE__</p>
  <p class="stamp">Last updated __UPDATED__ (Manila time) &middot; __TOTAL__ items tracked</p>
</div></header>
<main class="wrap">
  <div class="tools"><input id="q" class="search" type="search" placeholder="Search headlines or sources..."></div>
  <nav class="chips">__NAV__</nav>
  __SECTIONS__
</main>
<footer class="wrap">Headlines are collected automatically from Google News and link to the original publishers.</footer>
<script>
  document.getElementById('q').addEventListener('input', function () {
    var v = this.value.toLowerCase();
    document.querySelectorAll('.card').forEach(function (c) {
      c.style.display = c.textContent.toLowerCase().includes(v) ? '' : 'none';
    });
  });
</script>
</body></html>"""

page = (
    TEMPLATE.replace("__PAGE_TITLE__", html.escape(PAGE_TITLE))
    .replace("__HEADING__", html.escape(HEADING))
    .replace("__VERSION__", html.escape(VERSION))
    .replace("__SUBTITLE__", html.escape(SUBTITLE))
    .replace("__UPDATED__", f"{now:%B %d, %Y %I:%M %p}")
    .replace("__TOTAL__", str(total))
    .replace("__NAV__", "".join(nav))
    .replace("__SECTIONS__", "".join(sections))
)

os.makedirs("docs", exist_ok=True)
with open("docs/index.html", "w", encoding="utf-8") as f:
    f.write(page)
