import feedparser
import html
import json
import os
import re
import socket
import sys
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from urllib.parse import quote

MANILA = ZoneInfo("Asia/Manila")
socket.setdefaulttimeout(20)

PAGE_TITLE = "NEXUS by GIA"
STATE_FILE = "state.json"   # remembers the last story seen per topic

# ---------------------------------------------------------------
# SETTINGS
# ---------------------------------------------------------------
LOCAL_ONLY = True          # True = search only Philippine news sites
STRICT_FILTER = True       # True = drop headlines that don't match the topic rules
MAX_PER_SECTION = 10       # newest items kept per topic
BLOCKED_SOURCES = []       # e.g. ["Facebook"] to hide a source
REFRESH_MINUTES = 30       # shown on the page; keep in step with the cron line in update-digest.yml
STALE_AFTER_HOURS = 3      # show the "out of date" banner after this long without a refresh
REQUEST_DELAY = 0.25       # seconds between Google News requests (keeps us polite)

LOCAL_SITES = [
    "inquirer.net", "philstar.com", "bworldonline.com", "businessmirror.com.ph",
    "mb.com.ph", "pna.gov.ph", "manilatimes.net", "pia.gov.ph", "tribune.net.ph",
    "gmanetwork.com", "rappler.com", "autoindustriya.com", "carguide.ph",
    "topgear.com.ph", "dti.gov.ph", "boi.gov.ph", "customs.gov.ph", "lto.gov.ph",
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
#   tier    : groups the filter chips ("core", "agencies", "company", "energy")
#   queries : what we ask Google News
#   window  : how far back to look (days)
#   require : every pattern here must appear in the headline
#   exclude : if any pattern appears in the headline, drop it
# A story that fits several topics is shown once, with several tags.
# ---------------------------------------------------------------
TOPICS = [
    {
        "name": "CARS", "tier": "core",
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
        "name": "EVIS", "tier": "core",
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
        "name": "RACE", "tier": "core",
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
        "name": "Tariffs and taxes", "tier": "core",
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
        "name": "BOI", "tier": "agencies",
        "full": "Board of Investments news, approvals and incentives",
        "window": "7d",
        "queries": [
            '"Board of Investments" Philippines',
            "BOI approves investments Philippines",
            "BOI incentives registered projects Philippines",
        ],
        "require": [r"board of investments|\bboi\b"],
        "exclude": [],
    },
    {
        "name": "BOC", "tier": "agencies",
        "full": "Bureau of Customs news, orders and enforcement",
        "window": "7d",
        "queries": [
            '"Bureau of Customs" Philippines',
            "BOC customs Philippines import",
            "Bureau of Customs vehicles imported cars",
        ],
        "require": [r"bureau of customs|\bboc\b|\bcustoms\b"],
        "exclude": [
            r"thai customs|chinese customs|u\.s\. customs|\bus customs\b|indian customs"
            r"|canada border|\bcbp\b|hmrc"
        ],
    },
    {
        "name": "LTO and road safety", "tier": "agencies",
        "full": "Land Transportation Office news and road safety",
        "window": "7d",
        "queries": [
            '"Land Transportation Office" road safety',
            "LTO road safety Philippines",
            "LTO driver's license registration plates Philippines",
            '"road safety" Philippines',
        ],
        "require": [r"\blto\b|land transportation office|road safety"],
        "exclude": [],
    },
    {
        "name": "Toyota Philippines", "tier": "company",
        "full": "Toyota Motor Philippines and Toyota news in local media",
        "window": "14d",
        "queries": [
            '"Toyota Motor Philippines"',
            '"Toyota Philippines"',
            "Toyota Philippines DTI BOI automotive",
        ],
        "require": [r"toyota|\btmpc\b"],
        "exclude": [
            r"\bu\.s\.|united states|nhtsa|north america|\beurope\w*|\bindia\b"
            r"|australia|\bcanada\b"
        ],
    },
    {
        "name": "Hydrogen", "tier": "energy",
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
        "name": "Biofuels", "tier": "energy",
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
        "name": "Carbon trading", "tier": "energy",
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

STATS = {"queries": 0, "failed": 0}


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
    if STATS["queries"]:
        time.sleep(REQUEST_DELAY)
    STATS["queries"] += 1
    try:
        feed = feedparser.parse(build_url(query, window))
    except Exception as exc:
        STATS["failed"] += 1
        print(f"WARN: request failed for {query!r}: {exc}")
        return []
    status = getattr(feed, "status", 200)
    if status >= 400 or (getattr(feed, "bozo", 0) and not feed.entries):
        STATS["failed"] += 1
        print(f"WARN: feed error (status {status}) for {query!r}")
        return []
    return feed.entries


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
    ts = None
    if e.get("published_parsed"):
        ts = int(datetime(*e["published_parsed"][:6], tzinfo=timezone.utc).timestamp() * 1000)
    return {
        "title": title, "text": text, "source": source,
        "href": href, "link": e.get("link", ""), "ts": ts,
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


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


now = datetime.now(MANILA)
state = load_state()
merged = {}      # link -> story (a story can carry several topic tags)
by_title = {}    # headline -> link, so the same story from two sources merges
topic_meta = []

for topic in TOPICS:
    found, seen_links, seen_titles = [], set(), set()
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
            found.append(item)

    found.sort(key=lambda i: i["ts"] or 0, reverse=True)
    found = found[:MAX_PER_SECTION]

    if found and found[0]["ts"]:
        state[topic["name"]] = {"ts": found[0]["ts"], "title": found[0]["title"]}

    for item in found:
        key = by_title.get(item["text"], item["link"])
        if key in merged:
            if topic["name"] not in merged[key]["topics"]:
                merged[key]["topics"].append(topic["name"])
        else:
            merged[item["link"]] = {
                "topics": [topic["name"]], "title": item["title"],
                "source": item["source"], "link": item["link"], "ts": item["ts"],
            }
            by_title[item["text"]] = item["link"]

    topic_meta.append({
        "name": topic["name"], "full": topic["full"], "tier": topic["tier"],
        "window": int(topic["window"].rstrip("d")),
        "last": state.get(topic["name"]),
    })

if STATS["queries"] and STATS["failed"] == STATS["queries"]:
    print("ERROR: every feed request failed, so the existing page was left untouched.")
    sys.exit(1)
if STATS["failed"]:
    print(f"WARN: {STATS['failed']} of {STATS['queries']} requests failed this run.")

with open(STATE_FILE, "w", encoding="utf-8") as f:
    json.dump(state, f, ensure_ascii=False, indent=2)

stories = sorted(merged.values(), key=lambda s: s["ts"] or 0, reverse=True)
data = {
    "built": int(now.timestamp() * 1000),
    "refresh": REFRESH_MINUTES,
    "stale": STALE_AFTER_HOURS,
    "topics": topic_meta,
    "items": stories,
}
data_json = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")

TEMPLATE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="NEXUS by GIA: Government and Industry Affairs news monitoring.">
<title>__TITLE__</title>
<style>
  :root {
    --bg:#f5f5f5; --card:#ffffff; --tx:#0b0b0b; --mu:#5f5f5f; --ln:#e3e3e3;
    --rd:#eb0a1e; --rdt:#c8081a; --tagbg:#0b0b0b; --tagtx:#ffffff;
    --warnbg:#fff0c2; --warntx:#6b4300; --warnln:#f0d58a;
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      --bg:#121212; --card:#1b1b1b; --tx:#f5f5f5; --mu:#a0a0a0; --ln:#2c2c2c;
      --rdt:#ff5a67; --tagbg:#ffffff; --tagtx:#0b0b0b;
      --warnbg:#3a2a00; --warntx:#ffd48a; --warnln:#5a4200;
    }
  }
  :root[data-theme="dark"] {
    --bg:#121212; --card:#1b1b1b; --tx:#f5f5f5; --mu:#a0a0a0; --ln:#2c2c2c;
    --rdt:#ff5a67; --tagbg:#ffffff; --tagtx:#0b0b0b;
    --warnbg:#3a2a00; --warntx:#ffd48a; --warnln:#5a4200;
  }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--tx); line-height:1.5;
         font-family:"Segoe UI", system-ui, -apple-system, Roboto, sans-serif; }
  .wrap { max-width:920px; margin:0 auto; padding:0 1rem; }
  button { font:inherit; cursor:pointer; }
  :focus-visible { outline:2px solid var(--rd); outline-offset:2px; }

  .top { background:#0b0b0b; border-top:4px solid var(--rd); color:#fff; padding:1rem 0; }
  .hd { display:flex; align-items:center; justify-content:space-between; gap:.75rem; flex-wrap:wrap; }
  .brand { display:flex; align-items:center; gap:.75rem; }
  .mark { width:38px; height:38px; border-radius:50%; background:var(--rd); color:#fff;
          display:flex; align-items:center; justify-content:center; font-weight:600; font-size:1.1rem; }
  h1 { margin:0; font-size:1.35rem; letter-spacing:.12em; font-weight:600; line-height:1.1; }
  h1 .by { letter-spacing:.02em; font-weight:400; font-size:.9rem; color:#cfcfcf; margin-left:.35rem; }
  .brand p { margin:.15rem 0 0; font-size:.8rem; color:#bdbdbd; }
  .hr { display:flex; align-items:center; gap:.6rem; font-size:.8rem; color:#cfcfcf; flex-wrap:wrap; }
  .dot { width:8px; height:8px; border-radius:50%; background:var(--rd); display:inline-block; margin-right:.4rem; }
  .ghost { background:transparent; border:1px solid #666; color:#fff; border-radius:999px;
           padding:.4rem .9rem; min-height:36px; font-size:.8rem; }
  .ghost:hover { border-color:#fff; }

  .stale { background:var(--warnbg); color:var(--warntx); border-bottom:1px solid var(--warnln);
           padding:.65rem 0; font-size:.9rem; }
  .stale[hidden] { display:none; }
  .newver { background:var(--tx); color:var(--bg); padding:.5rem 0; font-size:.9rem; }
  .newver[hidden] { display:none; }
  .nv { background:var(--rd); color:#fff; border:0; border-radius:999px; padding:.3rem .9rem; margin-left:.5rem; min-height:32px; }

  .stats { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:1px;
           background:var(--ln); border-bottom:1px solid var(--ln); }
  .st { background:var(--card); padding:.8rem 1rem; }
  .st small { display:block; font-size:.78rem; color:var(--mu); }
  .st b { font-size:1.4rem; font-weight:500; }
  .st b.red { color:var(--rdt); }
  .st b.sm { font-size:1.1rem; }

  main { padding-top:1.1rem; padding-bottom:1rem; }
  h2.sec { font-size:.85rem; font-weight:500; margin:0 0 .5rem; color:var(--mu); }
  .nwg { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:.6rem; margin-bottom:1.1rem; }
  .nc { display:block; text-decoration:none; color:var(--tx); background:var(--card);
        border:1px solid var(--ln); border-top:3px solid var(--rd); padding:.6rem .75rem; }
  .nc:hover { border-color:var(--tx); border-top-color:var(--rd); }
  .nc span { font-size:.75rem; color:var(--rdt); font-weight:600; }
  .nc p { margin:.15rem 0 0; font-size:.88rem; line-height:1.4; font-weight:500; }
  .muted { color:var(--mu); font-size:.9rem; margin:0 0 1rem; }

  .sticky { position:sticky; top:0; z-index:5; background:var(--bg); padding:.5rem 0 .1rem; }
  .bar { display:flex; gap:.5rem; margin-bottom:.5rem; }
  .q { flex:1; min-width:0; padding:.65rem .9rem; font:inherit; background:var(--card); color:var(--tx);
       border:1px solid var(--ln); border-radius:8px; }
  .cp { background:var(--tx); color:var(--bg); border:0; border-radius:8px; padding:0 1rem; min-height:44px; white-space:nowrap; }
  .cp:hover { background:var(--rd); color:#fff; }
  .chips { display:flex; gap:.4rem; overflow-x:auto; padding-bottom:.5rem; align-items:center; scrollbar-width:thin; }
  .chip { flex:none; font-size:.82rem; padding:.4rem .85rem; min-height:36px; border-radius:999px;
          border:1px solid var(--ln); background:var(--card); color:var(--tx); white-space:nowrap; }
  .chip:hover { border-color:var(--tx); }
  .chip.on { background:var(--rd); border-color:var(--rd); color:#fff; }
  .chip i { font-style:normal; opacity:.7; margin-left:.3rem; }
  .sep { flex:none; width:1px; height:18px; background:var(--ln); margin:0 .15rem; }

  .dh { display:flex; align-items:center; gap:.6rem; font-size:.8rem; font-weight:500; color:var(--mu); margin:1rem 0 .5rem; }
  .dh:after { content:""; flex:1; height:1px; background:var(--ln); }
  .cd { background:var(--card); border:1px solid var(--ln); border-left:3px solid var(--rd);
        padding:.65rem .9rem; margin-bottom:.5rem; transition:transform .12s; }
  .cd:hover { transform:translateX(3px); border-color:var(--tx); border-left-color:var(--rd); }
  .row { display:flex; align-items:center; gap:.4rem; flex-wrap:wrap; }
  .tag { font-size:.7rem; background:var(--tagbg); color:var(--tagtx); padding:.05rem .6rem; border-radius:999px; }
  .nw { font-size:.7rem; background:var(--rd); color:#fff; padding:.05rem .6rem; border-radius:999px; font-weight:600; }
  .ttl { display:block; margin-top:.3rem; font-size:.95rem; font-weight:600; line-height:1.45; color:var(--tx); text-decoration:none; }
  .ttl:hover { color:var(--rdt); text-decoration:underline; }
  .mt { margin-top:.25rem; font-size:.8rem; color:var(--mu); }
  .mt b { font-weight:600; color:var(--tx); }
  .qt { display:flex; align-items:center; gap:.6rem; flex-wrap:wrap; padding:.55rem .75rem; margin-bottom:.5rem;
        border:1px dashed var(--ln); color:var(--mu); font-size:.85rem; }
  .qt .tag { background:var(--ln); color:var(--tx); }
  .none { text-align:center; color:var(--mu); padding:1.2rem 0; }
  .none[hidden] { display:none; }
  footer { background:#0b0b0b; color:#b5b5b5; font-size:.8rem; text-align:center; padding:1rem 0; margin-top:1.5rem; }
  @media (max-width:600px) { .stats { grid-template-columns:1fr 1fr 1fr; } .st { padding:.6rem .6rem; } .st b { font-size:1.15rem; } }
  @media (prefers-reduced-motion: reduce) { .cd { transition:none; } .cd:hover { transform:none; } }
  @media print {
    .ghost, .sticky, .cp, .stale, .newver, .stats { display:none !important; }
    body { background:#fff; color:#000; } .top, footer { background:#fff; color:#000; border-color:#000; }
    h1 .by, .brand p { color:#333; } .cd { break-inside:avoid; }
  }
</style></head>
<body>
<header class="top"><div class="wrap hd">
  <div class="brand">
    <div class="mark" aria-hidden="true">N</div>
    <div><h1>NEXUS<span class="by">by GIA</span></h1><p>Government and Industry Affairs &middot; monitoring 24/7 &middot; refreshes about every __REFRESH__ minutes</p></div>
  </div>
  <div class="hr"><span><span class="dot"></span><span id="upd"></span></span><button class="ghost" id="theme" type="button">Dark mode</button></div>
</div></header>
<div class="stale" id="stale" role="status" hidden><div class="wrap" id="staletxt"></div></div>
<div class="newver" id="newver" role="status" hidden><div class="wrap">New headlines are available.<button class="nv" id="reload" type="button">Refresh now</button></div></div>
<div class="stats"><div class="st"><small>Headlines tracked</small><b id="tot">0</b></div><div class="st"><small>New in last 24 hours</small><b class="red" id="nwc">0</b></div><div class="st"><small>Last refreshed</small><b class="sm" id="lastref"></b></div></div>
<main class="wrap">
  <h2 class="sec">Latest in the last 24 hours</h2>
  <div class="nwg" id="newgrid"></div>
  <div class="sticky">
    <div class="bar"><input class="q" id="q" type="search" placeholder="Search headlines or sources... (press / to focus)" aria-label="Search headlines or sources"><button class="cp" id="cp" type="button">Copy brief</button></div>
    <div class="chips" id="chips"></div>
  </div>
  <div id="list"></div>
  <div id="quiet"></div>
  <p class="none" id="none" hidden>No matching headlines. Try another topic or search term.</p>
</main>
<footer><div class="wrap">Headlines are collected automatically from Philippine news sources and link to the original publishers.</div></footer>
<script type="application/json" id="data">__DATA__</script>
<script>
(function () {
  var D = JSON.parse(document.getElementById('data').textContent);
  var TZ = 'Asia/Manila', HOUR = 3600000, DAY = 86400000, NOW = Date.now();
  function $(id) { return document.getElementById(id); }
  function el(tag, cls, txt) { var e = document.createElement(tag); if (cls) e.className = cls; if (txt != null) e.textContent = txt; return e; }
  function safe(u) { return /^https?:\/\//i.test(u) ? u : '#'; }
  var fKey = new Intl.DateTimeFormat('en-CA', { timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit' });
  var fDate = new Intl.DateTimeFormat('en-US', { timeZone: TZ, month: 'short', day: 'numeric', year: 'numeric' });
  var fShort = new Intl.DateTimeFormat('en-US', { timeZone: TZ, month: 'short', day: 'numeric' });
  var fFull = new Intl.DateTimeFormat('en-US', { timeZone: TZ, month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' });
  function rel(ms) {
    var d = NOW - ms;
    if (d < HOUR) return Math.max(1, Math.round(d / 60000)) + 'm ago';
    if (d < DAY) return Math.floor(d / HOUR) + 'h ago';
    return Math.floor(d / DAY) + 'd ago';
  }
  function label(ms) {
    var k = fKey.format(ms);
    if (k === fKey.format(NOW)) return 'Today';
    if (k === fKey.format(NOW - DAY)) return 'Yesterday';
    return (NOW - ms) < 7 * DAY ? 'Earlier this week' : 'Earlier';
  }

  var items = D.items.slice().sort(function (a, b) { return (b.ts || 0) - (a.ts || 0); });
  var counts = {};
  items.forEach(function (i) { i.topics.forEach(function (t) { counts[t] = (counts[t] || 0) + 1; }); });
  var fresh = [], topic = 'all', query = '', visible = [];

  try {
    var p = new URLSearchParams(location.hash.slice(1));
    var t0 = p.get('t');
    if (t0 && D.topics.some(function (x) { return x.name === t0; })) topic = t0;
    query = (p.get('q') || '').trim();
  } catch (e) {}
  $('q').value = query;

  function renderTop() {
    fresh = items.filter(function (i) { return i.ts && NOW - i.ts < DAY; });
    $('upd').textContent = 'Updated ' + fFull.format(D.built) + ' Manila';
    var stale = NOW - D.built > D.stale * HOUR;
    $('stale').hidden = !stale;
    if (stale) $('staletxt').textContent = 'This page may be out of date. The last refresh was more than ' + D.stale + ' hours ago, so reload the page or check again shortly.';
    $('tot').textContent = items.length;
    $('nwc').textContent = fresh.length;
    $('lastref').textContent = rel(D.built);
    var ng = $('newgrid'); ng.textContent = '';
    if (!fresh.length) { ng.appendChild(el('p', 'muted', 'No new stories in the last 24 hours.')); }
    fresh.slice(0, 3).forEach(function (i) {
      var a = el('a', 'nc'); a.href = safe(i.link); a.target = '_blank'; a.rel = 'noopener';
      a.appendChild(el('span', null, i.topics.join(' + ') + ' \u00b7 ' + rel(i.ts)));
      a.appendChild(el('p', null, i.title));
      ng.appendChild(a);
    });
  }

  var chipsEl = $('chips'), btns = [];
  function addChip(name, count) {
    var b = el('button', 'chip'); b.type = 'button'; b.setAttribute('data-t', name);
    b.appendChild(document.createTextNode(name === 'all' ? 'All' : name));
    b.appendChild(el('i', null, String(count)));
    b.addEventListener('click', function () { topic = name; apply(); });
    chipsEl.appendChild(b); btns.push(b);
  }
  addChip('all', items.length);
  var prev = null;
  D.topics.forEach(function (t) {
    if (t.tier !== prev) { chipsEl.appendChild(el('span', 'sep')); prev = t.tier; }
    addChip(t.name, counts[t.name] || 0);
  });

  function card(i) {
    var c = el('article', 'cd'), row = el('div', 'row');
    i.topics.forEach(function (t) { row.appendChild(el('span', 'tag', t)); });
    if (i.ts && NOW - i.ts < DAY) row.appendChild(el('span', 'nw', 'New'));
    c.appendChild(row);
    var a = el('a', 'ttl', i.title); a.href = safe(i.link); a.target = '_blank'; a.rel = 'noopener';
    c.appendChild(a);
    var mt = el('div', 'mt');
    if (i.source) { mt.appendChild(el('b', null, i.source)); mt.appendChild(document.createTextNode(' \u00b7 ')); }
    var s = el('span', null, i.ts ? rel(i.ts) : ''); if (i.ts) s.title = fFull.format(i.ts) + ' Manila';
    mt.appendChild(s); c.appendChild(mt);
    return c;
  }

  function apply() {
    var v = query.toLowerCase();
    btns.forEach(function (b) {
      var on = b.getAttribute('data-t') === topic;
      b.classList.toggle('on', on); b.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
    visible = items.filter(function (i) {
      return (topic === 'all' || i.topics.indexOf(topic) > -1) &&
        (i.title + ' ' + i.source + ' ' + i.topics.join(' ')).toLowerCase().indexOf(v) > -1;
    });
    var list = $('list'); list.textContent = '';
    var order = ['Today', 'Yesterday', 'Earlier this week', 'Earlier'], groups = {};
    visible.forEach(function (i) { var l = i.ts ? label(i.ts) : 'Earlier'; (groups[l] = groups[l] || []).push(i); });
    order.forEach(function (l) {
      if (!groups[l]) return;
      var s = el('section'); s.appendChild(el('h2', 'dh', l));
      groups[l].forEach(function (i) { s.appendChild(card(i)); });
      list.appendChild(s);
    });
    var qEl = $('quiet'); qEl.textContent = '';
    var quiet = v ? [] : D.topics.filter(function (t) { return !counts[t.name] && (topic === 'all' || topic === t.name); });
    if (quiet.length) {
      qEl.appendChild(el('h2', 'dh', 'Quiet topics'));
      quiet.forEach(function (t) {
        var r = el('div', 'qt'); r.appendChild(el('span', 'tag', t.name));
        var msg = 'No matching local news in the last ' + t.window + ' days.';
        msg += (t.last && t.last.ts) ? ' Last item: ' + fShort.format(t.last.ts) + '.' : ' No earlier item on record.';
        r.appendChild(el('span', null, msg)); qEl.appendChild(r);
      });
    }
    $('none').hidden = !!(visible.length || quiet.length);
    try {
      var sp = new URLSearchParams();
      if (topic !== 'all') sp.set('t', topic);
      if (query) sp.set('q', query);
      var h = sp.toString();
      history.replaceState(null, '', h ? '#' + h : location.pathname + location.search);
    } catch (e) {}
  }

  $('q').addEventListener('input', function () { query = this.value.trim(); apply(); });
  document.addEventListener('keydown', function (e) {
    var tg = (document.activeElement && document.activeElement.tagName) || '';
    if (e.key === '/' && tg !== 'INPUT' && tg !== 'TEXTAREA') { e.preventDefault(); $('q').focus(); }
  });

  function copyText(txt, cb) {
    function fallback() {
      try {
        var ta = document.createElement('textarea'); ta.value = txt; ta.style.position = 'fixed'; ta.style.opacity = '0';
        document.body.appendChild(ta); ta.select(); var ok = document.execCommand('copy'); document.body.removeChild(ta); cb(ok);
      } catch (e) { cb(false); }
    }
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(txt).then(function () { cb(true); }, fallback);
    } else { fallback(); }
  }
  $('cp').addEventListener('click', function () {
    var b = this, n = visible.length;
    if (!n) { b.textContent = 'Nothing to copy'; setTimeout(function () { b.textContent = 'Copy brief'; }, 1800); return; }
    var lines = visible.map(function (i) {
      return '[' + i.topics.join(', ') + '] ' + i.title + ' - ' + i.source + (i.ts ? ', ' + fDate.format(i.ts) : '');
    });
    var txt = 'NEXUS by GIA brief - ' + fDate.format(NOW) + '\n\n' + lines.join('\n');
    copyText(txt, function (ok) {
      b.textContent = ok ? 'Copied ' + n + ' headlines' : 'Copy failed';
      setTimeout(function () { b.textContent = 'Copy brief'; }, 1800);
    });
  });

  var root = document.documentElement, tb = $('theme');
  function cur() {
    var t = root.getAttribute('data-theme'); if (t) return t;
    return window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
  function setLabel() { tb.textContent = cur() === 'dark' ? 'Light mode' : 'Dark mode'; }
  try { var st = localStorage.getItem('nexus-theme'); if (st) root.setAttribute('data-theme', st); } catch (e) {}
  tb.addEventListener('click', function () {
    var n = cur() === 'dark' ? 'light' : 'dark'; root.setAttribute('data-theme', n);
    try { localStorage.setItem('nexus-theme', n); } catch (e) {}
    setLabel();
  });
  setLabel();

  function tick() { NOW = Date.now(); renderTop(); apply(); }
  function check() {
    if (document.hidden || !window.fetch) return;
    fetch(location.pathname + location.search, { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.text() : ''; })
      .then(function (t) {
        var m = t.match(/<script type="application\/json" id="data">([\s\S]*?)<\/script>/);
        if (!m) return;
        var b = JSON.parse(m[1]).built;
        if (b && b > D.built) $('newver').hidden = false;
      })
      .catch(function () {});
  }
  $('reload').addEventListener('click', function () { location.reload(); });
  setInterval(tick, 60000);
  setInterval(check, 300000);
  document.addEventListener('visibilitychange', function () { if (!document.hidden) { tick(); check(); } });

  renderTop();
  apply();
})();
</script>
</body></html>"""

page = (
    TEMPLATE.replace("__TITLE__", html.escape(PAGE_TITLE))
    .replace("__REFRESH__", str(REFRESH_MINUTES))
    .replace("__DATA__", data_json)
)

os.makedirs("docs", exist_ok=True)
with open("docs/index.html", "w", encoding="utf-8") as f:
    f.write(page)
