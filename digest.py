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

# ---------------------------------------------------------------
# SETTINGS
# ---------------------------------------------------------------
LOCAL_ONLY = True        # True = search only Philippine news sites
STRICT_FILTER = True     # True = drop headlines that don't match the topic rules
MAX_PER_SECTION = 10     # newest items shown per topic
BLOCKED_SOURCES = []     # e.g. ["Facebook"] to hide a source

LOCAL_SITES = [
    "inquirer.net", "philstar.com", "bworldonline.com", "businessmirror.com.ph",
    "mb.com.ph", "pna.gov.ph", "manilatimes.net", "pia.gov.ph", "tribune.net.ph",
    "gmanetwork.com", "rappler.com", "autoindustriya.com", "carguide.ph",
    "topgear.com.ph", "dti.gov.ph", "boi.gov.ph",
]

PH_CONTEXT = (
    r"philippine|filipino|\bphl\b|\bph\b|manila|\bdti\b|\bboi\b|\bdof\b|\bdoe\b"
    r"|bureau of customs|tariff commission|\blto\b|marcos|\bneda\b|jpepa"
)
VEHICLE = (
    r"\b(autos?|automotive|automobiles?|cars?|vehicles?|evs?|electric vehicles?"
    r"|motor vehicles?|carmakers?|automakers?|cbu|ckd|hybrids?|motorcycles?|auto parts)\b"
)
POLICY = (
    r"tariffs?|\btax(es)?\b|dut(y|ies)|excise|jpepa|atiga|asean|rcep|\bfta\b"
    r"|free trade|trade (deal|pact|agreement)|customs|import|safeguard"
)

# ---------------------------------------------------------------
# TOPICS
#   queries : what we ask Google News
#   window  : how far back to look (days)
#   require : every pattern here must appear in the headline
#   exclude : if any pattern appears in the headline, drop it
# ---------------------------------------------------------------
TOPICS = [
    {
        "name": "CARS",
        "full": "Comprehensive Automotive Resurgence Strategy",
        "window": "30d",
        "queries": [
            '"Comprehensive Automotive Resurgence Strategy"',
            '"CARS program" Philippines automotive',
            '"CARS" DTI BOI automotive incentive',
        ],
        "require": [
            r"comprehensive automotive resurgence"
            r"|\bcars\b.{0,40}\b(program|programme|strategy|scheme|incentives?|dti|boi"
            r"|automotive|carmakers?|assemblers?|manufactur\w*)"
        ],
        "exclude": [],
    },
    {
        "name": "EVIS",
        "full": "Electric Vehicle Incentive Strategy",
        "window": "30d",
        "queries": [
            '"Electric Vehicle Incentive Strategy"',
            '"EVIS" Philippines electric vehicle',
            'Philippines EV incentives DTI BOI',
        ],
        "require": [
            r"electric vehicle incentive strategy|\bevis\b"
            r"|\b(ev|evs|electric vehicles?)\b.{0,30}\bincentives?\b"
            r"|\bincentives?\b.{0,30}\b(ev|evs|electric vehicles?)\b"
        ],
        "exclude": [
            r"\bu\.s\.|united states|american|canada|\buk\b|britain|europe\w*"
            r"|\bindia\b|australia|new zealand|california|tesla"
        ],
    },
    {
        "name": "RACE",
        "full": "Revitalizing the Automotive Industry for Competitiveness Enhancement",
        "window": "30d",
        "queries": [
            '"Revitalizing the Automotive Industry for Competitiveness Enhancement"',
            '"RACE program" Philippines automotive',
            '"RACE" DTI BOI automotive incentive',
        ],
        "require": [
            r"revitalizing the automotive industry"
            r"|\brace (program|programme|incentives?|scheme)\b"
            r"|\brace\b.{0,30}\b(dti|boi)\b"
        ],
        "exclude": [
            r"horse|marathon|election|presidential|mayoral|senate|motogp|formula"
            r"|\bf1\b|nascar|grand prix|racing|fun run|\brace to\b"
        ],
    },
    {
        "name": "Automotive tariffs and taxes",
        "full": "Vehicle tariffs, excise taxes, JPEPA, ASEAN and other trade agreements",
        "window": "14d",
        "queries": [
            "Philippines automotive tariff",
            "Philippines vehicle excise tax",
            "JPEPA automotive Philippines Japan",
            "ASEAN ATIGA automotive tariff Philippines",
            "RCEP automotive Philippines tariff",
            "Philippines electric vehicle tariff executive order",
            "Philippines vehicle imports duty Bureau of Customs",
        ],
        "require": [VEHICLE, POLICY, PH_CONTEXT + r"|asean|atiga|rcep"],
        "exclude": [r"canada|thailand|\buk\b|britain"],
    },
    {
        "name": "Hydrogen",
        "full": "Hydrogen and clean fuels in the Philippines",
        "window": "7d",
        "queries": [
            "hydrogen Philippines energy DOE",
            "green hydrogen Philippines",
        ],
        "require": [r"hydrogen"],
        "exclude": [],
    },
    {
        "name": "Biofuels",
        "full": "Biodiesel, bioethanol and blending mandates",
        "window": "7d",
        "queries": [
            "biofuels Philippines",
            "biodiesel Philippines coconut methyl ester",
            "bioethanol Philippines DOE blend",
        ],
        "require": [
            r"bio-?fuels?|bio-?diesel|bio-?ethanol|ethanol|coconut methyl ester"
            r"|\bcme\b|sustainable aviation fuel"
        ],
        "exclude": [],
    },
    {
        "name": "Carbon trading",
        "full": "Carbon markets, credits and carbon pricing",
        "window": "7d",
        "queries": [
            "carbon market Philippines",
            "carbon trading Philippines",
            "carbon credits Philippines DENR",
        ],
        "require": [
            r"carbon (market|markets|trading|credits?|tax|pricing|offsets?|emissions?)"
            r"|emissions? trading|\bets\b|cap-and-trade"
        ],
        "exclude": [],
    },
]


def build_url(query, window):
    q = query
    if LOCAL_ONLY:
        sites = " OR ".join(f"site:{d}" for d in LOCAL_SITES)
        q = f"{query} ({sites})"
    q = f"{q} when:{window}"
    return (
        "https://news.google.com/rss/search?q="
        f"{quote(q)}&hl=en-PH&gl=PH&ceid=PH:en"
    )


def fetch(query, window):
    return feedparser.parse(build_url(query, window)).entries


def parse_entry(e):
    title = (e.get("title") or "").strip()
    src = e.get("source") or {}
    source = (src.get("title") or "").strip()
    href = (src.get("href") or "").strip()
    if source and title.endswith(" - " + source):
        title = title[: -(len(source) + 3)]
    elif not source and " - " in title:
        title, source = title.rsplit(" - ", 1)
    text = title.lower()
    if len(title) > 150:
        title = title[:147].rstrip() + "..."
    dt = None
    if e.get("published_parsed"):
        dt = datetime(*e["published_parsed"][:6], tzinfo=timezone.utc).astimezone(MANILA)
    return {
        "title": title, "text": text, "source": source,
        "href": href, "link": e.get("link", ""), "dt": dt,
    }


def is_local(item):
    return any(d in item["href"] for d in LOCAL_SITES)


def is_relevant(item, topic):
    if not STRICT_FILTER:
        return True
    text = item["text"]
    if not is_local(item) and not re.search(PH_CONTEXT, text):
        return False
    if any(re.search(p, text) for p in topic["exclude"]):
        return False
    return all(re.search(p, text) for p in topic["require"])


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


now = datetime.now(MANILA)
nav, sections = [], []
total = 0

for topic in TOPICS:
    items, seen_links, seen_titles = [], set(), set()
    for q in topic["queries"]:
        for e in fetch(q, topic["window"]):
            item = parse_entry(e)
            if (
                not item["link"]
                or item["link"] in seen_links
                or item["text"] in seen_titles
                or item["source"] in BLOCKED_SOURCES
                or not is_relevant(item, topic)
            ):
                continue
            seen_links.add(item["link"])
            seen_titles.add(item["text"])
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
        days = topic["window"].rstrip("d")
        cards = [f'<li class="empty">No matching local news in the last {days} days.</li>']

    slug = slugify(topic["name"])
    nav.append(f'<a href="#{slug}">{html.escape(topic["name"])} <span>{len(items)}</span></a>')
    sections.append(
        f'<section id="{slug}"><h2>{html.escape(topic["name"])}'
        f'<span class="count">{len(items)}</span></h2>'
        f'<p class="desc">{html.escape(topic["full"])}</p>'
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
  h2 { font-size: 1.1rem; margin: 0; display: flex; align-items: center; gap: .5rem; }
  .desc { margin: .15rem 0 .6rem; font-size: .85rem; color: var(--muted); }
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
<footer class="wrap">Headlines are collected automatically from Philippine news sources via Google News and link to the original publishers.</footer>
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
