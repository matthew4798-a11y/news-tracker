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
    --bg:#f5f5f5; --cd:#ffffff; --tx:#0b0b0b; --mu:#5f5f5f; --ln:#e3e3e3; --hv:#efefef; --bar:#cfcfcf;
    --rd:#eb0a1e; --rdt:#c8081a; --tagbg:#0b0b0b; --tagtx:#ffffff;
    --warnbg:#fff0c2; --warntx:#6b4300; --warnln:#f0d58a;
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      --bg:#121212; --cd:#1b1b1b; --tx:#f5f5f5; --mu:#a0a0a0; --ln:#2c2c2c; --hv:#242424; --bar:#3a3a3a;
      --rdt:#ff5a67; --tagbg:#ffffff; --tagtx:#0b0b0b;
      --warnbg:#3a2a00; --warntx:#ffd48a; --warnln:#5a4200;
    }
  }
  :root[data-theme="dark"] {
    --bg:#121212; --cd:#1b1b1b; --tx:#f5f5f5; --mu:#a0a0a0; --ln:#2c2c2c; --hv:#242424; --bar:#3a3a3a;
    --rdt:#ff5a67; --tagbg:#ffffff; --tagtx:#0b0b0b;
    --warnbg:#3a2a00; --warntx:#ffd48a; --warnln:#5a4200;
  }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--tx); line-height:1.5;
         font-family:"Segoe UI", system-ui, -apple-system, Roboto, sans-serif; }
  .wrap { max-width:1120px; margin:0 auto; padding:0 1rem; }
  button { font:inherit; cursor:pointer; }
  :focus-visible { outline:2px solid var(--rd); outline-offset:2px; }

  .top { background:#0b0b0b; border-top:4px solid var(--rd); color:#fff; padding:.85rem 0; }
  .hd { display:flex; align-items:center; justify-content:space-between; gap:.75rem; flex-wrap:wrap; }
  .brand { display:flex; align-items:center; gap:.75rem; }
  .mark { width:38px; height:38px; border-radius:50%; background:var(--rd); color:#fff;
          display:flex; align-items:center; justify-content:center; font-weight:600; font-size:1.1rem; }
  h1 { margin:0; font-size:1.35rem; letter-spacing:.12em; font-weight:600; line-height:1.1; }
  h1 .by { letter-spacing:.02em; font-weight:400; font-size:.9rem; color:#cfcfcf; margin-left:.35rem; }
  .brand p { margin:.15rem 0 0; font-size:.8rem; color:#bdbdbd; }
  .hr { display:flex; align-items:center; gap:.7rem; font-size:.8rem; color:#d6d6d6; flex-wrap:wrap; }
  .live { display:flex; align-items:center; gap:.45rem; }
  .pd { width:8px; height:8px; border-radius:50%; background:var(--rd); display:inline-block; animation:pl 1.8s ease-in-out infinite; }
  .pd.warn { background:#f5a623; animation:none; }
  @keyframes pl { 0%,100% { opacity:1; } 50% { opacity:.3; } }
  .ghost { background:transparent; border:1px solid #666; color:#fff; border-radius:999px;
           padding:.4rem .9rem; min-height:36px; font-size:.8rem; }
  .ghost:hover { border-color:#fff; }

  .clocks { background:#151515; color:#fff; border-top:1px solid #2a2a2a; padding:.55rem 0; }
  .cw { display:flex; flex-wrap:wrap; align-items:center; gap:.5rem 1.5rem; }
  .cg { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:.75rem; flex:1 1 520px; }
  .ck { border-bottom:2px solid #333; padding:.1rem 0 .4rem; }
  .ck.base { border-bottom-color:var(--rd); }
  .cn { display:flex; justify-content:space-between; align-items:center; gap:.4rem; font-size:.72rem; color:#bdbdbd; }
  .cn b { font-weight:600; color:#fff; font-size:.78rem; letter-spacing:.08em; text-transform:uppercase; }
  .cn em { font-style:normal; margin-left:.4rem; }
  .ct { font-size:1.45rem; font-weight:500; line-height:1.2; font-variant-numeric:tabular-nums; }
  .ct small { font-size:.72rem; font-weight:400; color:#bdbdbd; margin-left:.25rem; }
  .cs { font-size:.72rem; color:#a5a5a5; }
  .od { display:inline-block; width:8px; height:8px; border-radius:50%; border:1.5px solid #888; flex:none; }
  .od.open { background:#fff; border-color:#fff; }
  .cx { display:flex; align-items:center; gap:.75rem; font-size:.72rem; color:#bdbdbd; }
  .cx .leg { display:flex; align-items:center; gap:.4rem; }
  .mini { background:transparent; border:1px solid #666; color:#fff; border-radius:999px;
          padding:.2rem .7rem; min-height:30px; font-size:.75rem; }
  .mini:hover { border-color:#fff; }

  .stale { background:var(--warnbg); color:var(--warntx); border-bottom:1px solid var(--warnln);
           padding:.65rem 0; font-size:.9rem; }
  .stale[hidden] { display:none; }
  .newver { background:var(--tx); color:var(--cd); padding:.5rem 0; font-size:.9rem; }
  .newver[hidden] { display:none; }
  .nv { background:var(--rd); color:#fff; border:0; border-radius:999px; padding:.3rem .9rem; margin-left:.5rem; min-height:32px; }

  .shell { display:grid; grid-template-columns:220px minmax(0,1fr); gap:1.25rem; padding-top:1rem; padding-bottom:1rem; }
  .side { align-self:start; position:sticky; top:.75rem; background:var(--cd); border:1px solid var(--ln); padding:.75rem .5rem; }
  .gl { font-size:.72rem; color:var(--mu); margin:.9rem .5rem .25rem; }
  .gl:first-child { margin-top:0; }
  .tp { display:flex; justify-content:space-between; align-items:center; gap:.4rem; width:100%; text-align:left;
        padding:.45rem .6rem; border:0; background:transparent; color:var(--tx); border-radius:6px; font-size:.9rem; min-height:36px; }
  .tp:hover { background:var(--hv); }
  .tp b { font-weight:400; font-size:.8rem; color:var(--mu); }
  .tp.on, .tp.on:hover { background:var(--rd); color:#fff; }
  .tp.on b { color:#fff; }
  .ud { display:inline-block; width:6px; height:6px; border-radius:50%; background:var(--rd); margin-right:.4rem; vertical-align:1px; }
  .tp.on .ud { background:#fff; }

  .main { min-width:0; }
  .tools { position:sticky; top:0; z-index:5; background:var(--bg); padding:.5rem 0; display:flex; gap:.5rem; flex-wrap:wrap; align-items:center; }
  .q { flex:1; min-width:160px; padding:.65rem .9rem; font:inherit; background:var(--cd); color:var(--tx);
       border:1px solid var(--ln); border-radius:8px; }
  .seg { display:flex; border:1px solid var(--ln); border-radius:8px; overflow:hidden; }
  .seg button { border:0; background:var(--cd); color:var(--mu); font-size:.8rem; padding:0 .75rem; min-height:42px; }
  .seg button:hover { background:var(--hv); }
  .seg button.on, .seg button.on:hover { background:var(--tx); color:var(--cd); }
  .mk { background:var(--cd); border:1px solid var(--ln); border-radius:8px; min-height:42px; padding:0 .8rem; font-size:.8rem; color:var(--tx); }
  .mk:hover { background:var(--hv); }
  .sum { font-size:.82rem; color:var(--mu); margin:.25rem 0 .6rem; }

  .hp { display:grid; grid-template-columns:minmax(0,1.7fr) minmax(0,1fr); gap:.75rem; margin-bottom:.4rem; }
  .hero { background:#0b0b0b; color:#fff; border-top:3px solid var(--rd); border-left:1px solid #2a2a2a; border-right:1px solid #2a2a2a; border-bottom:1px solid #2a2a2a;
          padding:.9rem 1rem; display:flex; flex-direction:column; justify-content:space-between; min-height:140px; }
  .hl { font-size:.72rem; color:#ff6b76; margin-bottom:.4rem; }
  .ht { display:block; color:#fff; font-size:1.2rem; font-weight:600; line-height:1.35; text-decoration:none; }
  a.ht:hover { text-decoration:underline; }
  .hm { display:flex; flex-wrap:wrap; gap:.4rem; align-items:center; margin-top:.8rem; font-size:.8rem; color:#cfcfcf; }
  .hero .tag { background:#fff; color:#0b0b0b; }
  .hero .ab { margin-left:auto; background:transparent; border-color:#666; color:#fff; }
  .hero .ab:hover { border-color:#fff; }
  .hero .ab.on { background:var(--rd); border-color:var(--rd); }
  .pulse { background:var(--cd); border:1px solid var(--ln); padding:.7rem .8rem; }
  .pt { font-size:.78rem; color:var(--mu); display:flex; justify-content:space-between; gap:.4rem; }
  .bars { display:flex; align-items:flex-end; gap:5px; height:74px; margin-top:.5rem; }
  .bc { flex:1; display:flex; flex-direction:column; align-items:center; justify-content:flex-end; gap:3px; font-size:.7rem; color:var(--mu); }
  .bc i { display:block; width:100%; background:var(--bar); }
  .bc.today i { background:var(--rd); }
  .days { display:flex; gap:5px; margin-top:4px; }
  .days span { flex:1; text-align:center; font-size:.68rem; color:var(--mu); }

  .dh { display:flex; align-items:center; gap:.6rem; font-size:.8rem; font-weight:500; color:var(--mu); margin:1rem 0 .5rem; }
  .dh:after { content:""; flex:1; height:1px; background:var(--ln); }
  .cd { display:flex; gap:.7rem; align-items:flex-start; background:var(--cd); border:1px solid var(--ln); border-left:3px solid var(--ln);
        padding:.65rem .75rem .65rem .85rem; margin-bottom:.45rem; }
  .cd.un { border-left-color:var(--rd); }
  .cd:hover { border-color:var(--tx); }
  .cd.un:hover { border-left-color:var(--rd); }
  .av { flex:none; width:30px; height:30px; border-radius:50%; background:var(--hv); color:var(--tx);
        display:flex; align-items:center; justify-content:center; font-size:.7rem; font-weight:600; }
  .cb { flex:1; min-width:0; }
  .row { display:flex; flex-wrap:wrap; gap:.35rem; align-items:center; font-size:.72rem; color:var(--mu); }
  .tag { font-size:.7rem; background:var(--tagbg); color:var(--tagtx); padding:.05rem .6rem; border-radius:999px; }
  .ttl { display:block; margin-top:.2rem; font-size:.95rem; font-weight:600; line-height:1.4; color:var(--tx); text-decoration:none; }
  .ttl:hover { color:var(--rdt); text-decoration:underline; }
  .cm { font-size:.78rem; color:var(--mu); margin-top:.1rem; }
  body.cmp .av, body.cmp .cm { display:none; }
  body.cmp .cd { padding:.4rem .7rem .4rem .8rem; margin-bottom:.3rem; }
  .ab { flex:none; width:32px; height:32px; border-radius:50%; border:1px solid var(--ln); background:var(--cd); color:var(--tx);
        display:flex; align-items:center; justify-content:center; padding:0; font-size:1rem; line-height:1; }
  .ab:hover { border-color:var(--tx); }
  .ab.on, .ab.on:hover { background:var(--rd); border-color:var(--rd); color:#fff; }
  .qt { display:flex; align-items:center; gap:.6rem; flex-wrap:wrap; padding:.55rem .75rem; margin-bottom:.5rem;
        border:1px dashed var(--ln); color:var(--mu); font-size:.85rem; }
  .qt .tag { background:var(--ln); color:var(--tx); }
  .none { text-align:center; color:var(--mu); padding:1.2rem 0; }
  .none[hidden] { display:none; }
  .tray { position:sticky; bottom:.75rem; z-index:6; display:flex; align-items:center; justify-content:space-between; gap:.5rem; flex-wrap:wrap;
          background:var(--tx); color:var(--cd); padding:.6rem .8rem; margin-top:.8rem; font-size:.88rem; }
  .tray[hidden] { display:none; }
  .tray div { display:flex; gap:.4rem; }
  .tray button { background:transparent; border:1px solid currentColor; color:inherit; border-radius:999px; min-height:34px; padding:0 .9rem; font-size:.8rem; }
  .tray .go { background:var(--rd); border-color:var(--rd); color:#fff; }
  footer { background:#0b0b0b; color:#b5b5b5; font-size:.8rem; text-align:center; padding:1rem 0; margin-top:1.5rem; }

  @media (max-width:820px) {
    .shell { grid-template-columns:1fr; gap:.5rem; }
    .side { position:static; display:flex; gap:.4rem; overflow-x:auto; padding:.5rem; }
    .gl { display:none; }
    .tp { flex:none; width:auto; white-space:nowrap; gap:.6rem; border:1px solid var(--ln); border-radius:999px; }
    .hp { grid-template-columns:1fr; }
    .cg { grid-template-columns:repeat(2,minmax(0,1fr)); }
    .cx .leg { display:none; }
  }
  @media (prefers-reduced-motion: reduce) { .pd { animation:none; } }
  @media print {
    .top .hr, .clocks, .side, .tools, .tray, .stale, .newver, .pulse, .ab { display:none !important; }
    body { background:#fff; color:#000; } .top, footer { background:#fff; color:#000; border-color:#000; }
    h1 .by, .brand p { color:#333; } .shell { display:block; } .cd { break-inside:avoid; }
    .hero { background:#fff; color:#000; } .ht { color:#000; } .hl, .hm { color:#333; }
  }
</style></head>
<body>
<header class="top"><div class="wrap hd">
  <div class="brand">
    <div class="mark" aria-hidden="true">N</div>
    <div><h1>NEXUS<span class="by">by GIA</span></h1><p id="sub">Government and Industry Affairs &middot; monitoring 24/7</p></div>
  </div>
  <div class="hr"><span class="live" id="live"><span class="pd" id="pd"></span><span id="livetxt"></span></span><button class="ghost" id="theme" type="button">Dark mode</button></div>
</div></header>
<div class="clocks"><div class="wrap cw">
  <div class="cg" id="clocks" role="group" aria-label="World clocks"></div>
  <div class="cx"><span class="leg"><span class="od open" aria-hidden="true"></span>Office hours, Mon to Fri, 9 AM to 6 PM</span><button class="mini" id="fmt" type="button">Use 24h</button></div>
</div></div>
<div class="stale" id="stale" role="status" hidden><div class="wrap" id="staletxt"></div></div>
<div class="newver" id="newver" role="status" hidden><div class="wrap">New headlines are available.<button class="nv" id="reload" type="button">Refresh now</button></div></div>
<div class="wrap shell">
  <nav class="side" id="side" aria-label="Topics"></nav>
  <main class="main" id="main">
    <div class="tools">
      <input class="q" id="q" type="search" placeholder="Search headlines, sources, topics (press / to focus)" aria-label="Search headlines, sources, topics">
      <div class="seg" role="group" aria-label="Density"><button id="dc" type="button">Comfortable</button><button id="dk" type="button">Compact</button></div>
      <button class="mk" id="mk" type="button">Mark all read</button>
    </div>
    <p class="sum" id="sum"></p>
    <div class="hp"><div class="hero" id="hero"></div><div class="pulse" id="pulse"></div></div>
    <div id="feed"></div>
    <div id="quiet"></div>
    <p class="none" id="none" hidden>No matching headlines. Try another topic or search term.</p>
    <div class="tray" id="tray" hidden><span id="tn"></span><div><button id="clr" type="button">Clear</button><button class="go" id="cpy" type="button">Copy brief</button></div></div>
  </main>
</div>
<footer><div class="wrap">Headlines are collected automatically from Philippine news sources and link to the original publishers.</div></footer>
<script type="application/json" id="data">__DATA__</script>
<script>
(function () {
  var D = JSON.parse(document.getElementById('data').textContent);
  var TZ = 'Asia/Manila', HOUR = 3600000, DAY = 86400000, NOW = Date.now();
  var TIER = { core: 'Core programs', agencies: 'Agencies', company: 'Company', energy: 'Energy and climate' };
  var MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  function $(id) { return document.getElementById(id); }
  function el(tag, cls, txt) { var e = document.createElement(tag); if (cls) e.className = cls; if (txt != null) e.textContent = txt; return e; }
  function safe(u) { return /^https?:\/\//i.test(u) ? u : '#'; }
  function pad2(n) { return (n < 10 ? '0' : '') + n; }
  function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }
  var fKey = new Intl.DateTimeFormat('en-CA', { timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit' });
  var fDate = new Intl.DateTimeFormat('en-US', { timeZone: TZ, month: 'short', day: 'numeric', year: 'numeric' });
  var fShort = new Intl.DateTimeFormat('en-US', { timeZone: TZ, month: 'short', day: 'numeric' });
  var fFull = new Intl.DateTimeFormat('en-US', { timeZone: TZ, month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' });
  var fWd = new Intl.DateTimeFormat('en-US', { timeZone: TZ, weekday: 'short' });
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
  function dayIdx(ms) { return Math.round((Date.parse(fKey.format(NOW)) - Date.parse(fKey.format(ms))) / DAY); }
  function ini(s) {
    var w = (s || '').split(/\s+/).filter(Boolean);
    if (!w.length) return '?';
    return (w.length === 1 ? w[0].slice(0, 2) : w[0][0] + w[1][0]).toUpperCase();
  }

  var items = D.items.slice().sort(function (a, b) { return (b.ts || 0) - (a.ts || 0); });
  var present = {}, counts = {};
  items.forEach(function (i) { present[i.link] = 1; i.topics.forEach(function (t) { counts[t] = (counts[t] || 0) + 1; }); });
  var topic = 'all', query = '', brief = {}, visible = [];
  var compact = lsGet('nexus-density') === 'k';

  try {
    var p = new URLSearchParams(location.hash.slice(1));
    var t0 = p.get('t');
    if (t0 && D.topics.some(function (x) { return x.name === t0; })) topic = t0;
    query = (p.get('q') || '').trim();
  } catch (e) {}
  $('q').value = query;

  var read = {}, rawRead = lsGet('nexus-read');
  function saveRead() { lsSet('nexus-read', JSON.stringify(Object.keys(read).filter(function (k) { return present[k]; }).slice(-800))); }
  if (rawRead) { try { JSON.parse(rawRead).forEach(function (k) { read[k] = 1; }); } catch (e) {} }
  else { items.forEach(function (i) { if (!(i.ts && NOW - i.ts < DAY)) read[i.link] = 1; }); saveRead(); }

  function byTopic() { return items.filter(function (i) { return topic === 'all' || i.topics.indexOf(topic) > -1; }); }
  function vis() {
    var v = query.toLowerCase();
    return byTopic().filter(function (i) { return (i.title + ' ' + i.source + ' ' + i.topics.join(' ')).toLowerCase().indexOf(v) > -1; });
  }
  function addBtn(i) {
    var on = !!brief[i.link], b = el('button', 'ab' + (on ? ' on' : ''), on ? '✓' : '+');
    b.type = 'button'; b.setAttribute('data-add', i.link); b.setAttribute('aria-pressed', on ? 'true' : 'false');
    b.setAttribute('aria-label', (on ? 'Remove from brief: ' : 'Add to brief: ') + i.title);
    return b;
  }

  /* ---------- world clocks ---------- */
  var CITIES = [
    { n: 'Manila', c: 'Philippines', tz: 'Asia/Manila' },
    { n: 'Tokyo', c: 'Japan', tz: 'Asia/Tokyo' },
    { n: 'Bangkok', c: 'Thailand', tz: 'Asia/Bangkok' },
    { n: 'Singapore', c: 'Singapore', tz: 'Asia/Singapore' }
  ];
  var h24 = lsGet('nexus-24h') === '1';
  var cells = CITIES.map(function (c, idx) {
    var box = el('div', 'ck' + (idx === 0 ? ' base' : '')), cn = el('div', 'cn'), left = el('span');
    left.appendChild(el('b', null, c.n)); left.appendChild(el('em', null, c.c));
    var od = el('span', 'od'); od.setAttribute('role', 'img');
    cn.appendChild(left); cn.appendChild(od); box.appendChild(cn);
    var ct = el('div', 'ct'), hm = el('span'), sc = el('small');
    ct.appendChild(hm); ct.appendChild(sc); box.appendChild(ct);
    var cs = el('div', 'cs'), dt = el('span'), of = el('span');
    cs.appendChild(dt); cs.appendChild(document.createTextNode(' · ')); cs.appendChild(of); box.appendChild(cs);
    $('clocks').appendChild(box);
    return {
      hm: hm, sc: sc, dt: dt, of: of, od: od,
      f: new Intl.DateTimeFormat('en-US', { timeZone: c.tz, hourCycle: 'h23', weekday: 'short', year: 'numeric', month: 'numeric', day: 'numeric', hour: 'numeric', minute: 'numeric', second: 'numeric' })
    };
  });
  function parts(f, ms) { var o = {}; f.formatToParts(ms).forEach(function (x) { o[x.type] = x.value; }); return o; }
  function offMin(o, ms) { return Math.round((Date.UTC(+o.year, +o.month - 1, +o.day, (+o.hour) % 24, +o.minute, +o.second) - Math.floor(ms / 1000) * 1000) / 60000); }
  function span(mins) { var h = Math.floor(mins / 60), m = mins % 60; return h + (m ? ':' + pad2(m) : '') + 'h'; }
  function clockTick() {
    var ms = Date.now(), base = null;
    cells.forEach(function (c, idx) {
      var o = parts(c.f, ms), hr = (+o.hour) % 24, mi = pad2(+o.minute), se = pad2(+o.second), off = offMin(o, ms);
      if (idx === 0) base = off;
      c.hm.textContent = h24 ? pad2(hr) + ':' + mi : ((hr % 12) || 12) + ':' + mi;
      c.sc.textContent = ':' + se + (h24 ? '' : ' ' + (hr < 12 ? 'AM' : 'PM'));
      c.dt.textContent = o.weekday + ', ' + MON[+o.month - 1] + ' ' + o.day;
      var diff = off - base;
      c.of.textContent = idx === 0 ? 'Base time' : diff === 0 ? 'Same time as Manila' : (diff > 0 ? '+' : '−') + span(Math.abs(diff)) + ' from Manila';
      var open = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'].indexOf(o.weekday) > -1 && hr >= 9 && hr < 18;
      c.od.className = 'od' + (open ? ' open' : '');
      c.od.setAttribute('aria-label', open ? 'Within office hours' : 'Outside office hours');
      c.od.title = open ? 'Within office hours' : 'Outside office hours';
    });
  }
  function setFmt() { $('fmt').textContent = h24 ? 'Use 12h' : 'Use 24h'; $('fmt').setAttribute('aria-label', h24 ? 'Switch to 12-hour time' : 'Switch to 24-hour time'); }
  $('fmt').addEventListener('click', function () { h24 = !h24; lsSet('nexus-24h', h24 ? '1' : '0'); setFmt(); clockTick(); });
  setFmt(); clockTick();
  setInterval(clockTick, 1000);

  /* ---------- header status ---------- */
  function renderTop() {
    var stale = NOW - D.built > D.stale * HOUR;
    $('livetxt').textContent = (stale ? 'Delayed' : 'Live') + ' · refreshed ' + rel(D.built);
    $('pd').className = 'pd' + (stale ? ' warn' : '');
    $('live').title = 'Updated ' + fFull.format(D.built) + ' Manila';
    $('stale').hidden = !stale;
    if (stale) $('staletxt').textContent = 'This page may be out of date. The last refresh was more than ' + D.stale + ' hours ago, so reload the page or check again shortly.';
    $('sub').textContent = 'Government and Industry Affairs · monitoring 24/7 · refreshes about every ' + D.refresh + ' minutes';
  }

  /* ---------- sidebar, summary, hero, pulse ---------- */
  function sbtn(key, name, list) {
    var u = 0; list.forEach(function (i) { if (!read[i.link]) u++; });
    var b = el('button', 'tp' + (topic === key ? ' on' : '')); b.type = 'button';
    b.setAttribute('data-t', key); b.setAttribute('aria-pressed', topic === key ? 'true' : 'false');
    var s = el('span'); if (u) s.appendChild(el('i', 'ud'));
    s.appendChild(document.createTextNode(name)); b.appendChild(s); b.appendChild(el('b', null, String(list.length)));
    return b;
  }
  function rSide() {
    var s = $('side'); s.textContent = ''; s.appendChild(sbtn('all', 'All topics', items));
    var prev = null;
    D.topics.forEach(function (t) {
      if (t.tier !== prev) { s.appendChild(el('div', 'gl', TIER[t.tier] || t.tier)); prev = t.tier; }
      s.appendChild(sbtn(t.name, t.name, items.filter(function (i) { return i.topics.indexOf(t.name) > -1; })));
    });
  }
  function rSummary(v) {
    var n24 = 0, un = 0;
    v.forEach(function (i) { if (i.ts && NOW - i.ts < DAY) n24++; if (!read[i.link]) un++; });
    $('sum').textContent = v.length + (v.length === 1 ? ' headline' : ' headlines') + ' · ' + n24 + ' in the last 24 hours · ' + un + ' unread';
  }
  function rHero(v) {
    var h = $('hero'); h.textContent = '';
    if (!v.length) {
      var d0 = el('div'); d0.appendChild(el('div', 'hl', 'Top story')); d0.appendChild(el('div', 'ht', 'Nothing here yet. This topic is quiet.')); h.appendChild(d0); return;
    }
    var t = v[0], d = el('div');
    d.appendChild(el('div', 'hl', 'Top story' + (t.ts ? ' · ' + rel(t.ts) : '')));
    var a = el('a', 'ht', t.title); a.href = safe(t.link); a.target = '_blank'; a.rel = 'noopener'; a.setAttribute('data-k', t.link);
    d.appendChild(a); h.appendChild(d);
    var m = el('div', 'hm'); t.topics.forEach(function (x) { m.appendChild(el('span', 'tag', x)); });
    if (t.source) m.appendChild(el('span', null, t.source));
    m.appendChild(addBtn(t)); h.appendChild(m);
  }
  function rPulse(list) {
    var c = [0, 0, 0, 0, 0, 0, 0];
    list.forEach(function (i) { if (!i.ts) return; var d = dayIdx(i.ts); if (d >= 0 && d < 7) c[d]++; });
    var mx = Math.max.apply(null, c.concat([1])), p = $('pulse'); p.textContent = '';
    var t = el('div', 'pt'); t.appendChild(el('span', null, 'Headlines by day')); t.appendChild(el('span', null, 'Last 7 days')); p.appendChild(t);
    var bars = el('div', 'bars'), days = el('div', 'days');
    for (var d = 6; d >= 0; d--) {
      var bc = el('div', 'bc' + (d === 0 ? ' today' : '')); bc.appendChild(el('span', null, String(c[d])));
      var bi = el('i'); bi.style.height = (3 + Math.round(c[d] / mx * 36)) + 'px'; bc.appendChild(bi); bars.appendChild(bc);
      days.appendChild(el('span', null, fWd.format(NOW - d * DAY)));
    }
    p.appendChild(bars); p.appendChild(days);
  }

  /* ---------- feed ---------- */
  function card(i) {
    var c = el('article', 'cd' + (read[i.link] ? '' : ' un'));
    var av = el('div', 'av', ini(i.source)); av.setAttribute('aria-hidden', 'true'); c.appendChild(av);
    var cb = el('div', 'cb'), row = el('div', 'row');
    i.topics.forEach(function (t) { row.appendChild(el('span', 'tag', t)); });
    var s = el('span', null, i.ts ? rel(i.ts) : ''); if (i.ts) s.title = fFull.format(i.ts) + ' Manila';
    row.appendChild(s); cb.appendChild(row);
    var a = el('a', 'ttl', i.title); a.href = safe(i.link); a.target = '_blank'; a.rel = 'noopener'; a.setAttribute('data-k', i.link);
    cb.appendChild(a);
    if (i.source) cb.appendChild(el('div', 'cm', i.source));
    c.appendChild(cb); c.appendChild(addBtn(i));
    return c;
  }
  function rFeed(v) {
    var f = $('feed'); f.textContent = '';
    var rest = v.slice(1), order = ['Today', 'Yesterday', 'Earlier this week', 'Earlier'], groups = {};
    rest.forEach(function (i) { var l = i.ts ? label(i.ts) : 'Earlier'; (groups[l] = groups[l] || []).push(i); });
    order.forEach(function (l) {
      if (!groups[l]) return;
      var s = el('section'); s.appendChild(el('h2', 'dh', l));
      groups[l].forEach(function (i) { s.appendChild(card(i)); });
      f.appendChild(s);
    });
  }
  function rQuiet() {
    var q = $('quiet'); q.textContent = '';
    if (query) return 0;
    var qs = D.topics.filter(function (t) { return !counts[t.name] && (topic === 'all' || topic === t.name); });
    if (!qs.length) return 0;
    q.appendChild(el('h2', 'dh', 'Quiet topics'));
    qs.forEach(function (t) {
      var r = el('div', 'qt'); r.appendChild(el('span', 'tag', t.name));
      var msg = 'No matching local news in the last ' + t.window + ' days.';
      msg += (t.last && t.last.ts) ? ' Last item: ' + fShort.format(t.last.ts) + '.' : ' No earlier item on record.';
      r.appendChild(el('span', null, msg)); q.appendChild(r);
    });
    return qs.length;
  }
  function rTray() {
    var n = Object.keys(brief).length;
    $('tray').hidden = !n;
    $('tn').textContent = n + (n === 1 ? ' story' : ' stories') + ' in your brief';
  }
  function setDensity() {
    document.body.classList.toggle('cmp', compact);
    $('dc').className = compact ? '' : 'on'; $('dk').className = compact ? 'on' : '';
    $('dc').setAttribute('aria-pressed', compact ? 'false' : 'true'); $('dk').setAttribute('aria-pressed', compact ? 'true' : 'false');
  }
  function render() {
    visible = vis();
    rSide(); rSummary(visible); rHero(visible); rPulse(byTopic()); rFeed(visible);
    var q = rQuiet(); rTray();
    $('none').hidden = !!(visible.length || q);
    try {
      var sp = new URLSearchParams();
      if (topic !== 'all') sp.set('t', topic);
      if (query) sp.set('q', query);
      var h = sp.toString();
      history.replaceState(null, '', h ? '#' + h : location.pathname + location.search);
    } catch (e) {}
  }

  /* ---------- interactions ---------- */
  $('side').addEventListener('click', function (e) {
    var b = e.target.closest('.tp'); if (!b) return;
    topic = b.getAttribute('data-t'); render();
  });
  $('main').addEventListener('click', function (e) {
    var a = e.target.closest('[data-add]');
    if (a) { var k = a.getAttribute('data-add'); if (brief[k]) delete brief[k]; else brief[k] = 1; render(); return; }
    var t = e.target.closest('a[data-k]');
    if (t) { read[t.getAttribute('data-k')] = 1; saveRead(); setTimeout(render, 0); }
  });
  $('q').addEventListener('input', function () { query = this.value.trim(); render(); });
  document.addEventListener('keydown', function (e) {
    var tg = (document.activeElement && document.activeElement.tagName) || '';
    if (e.key === '/' && tg !== 'INPUT' && tg !== 'TEXTAREA') { e.preventDefault(); $('q').focus(); }
  });
  $('dc').addEventListener('click', function () { compact = false; lsSet('nexus-density', 'c'); setDensity(); });
  $('dk').addEventListener('click', function () { compact = true; lsSet('nexus-density', 'k'); setDensity(); });
  $('mk').addEventListener('click', function () { visible.forEach(function (i) { read[i.link] = 1; }); saveRead(); render(); });
  $('clr').addEventListener('click', function () { brief = {}; render(); });

  function copyText(txt, cb) {
    function fallback() {
      try {
        var ta = document.createElement('textarea'); ta.value = txt; ta.style.position = 'fixed'; ta.style.opacity = '0';
        document.body.appendChild(ta); ta.select(); var ok = document.execCommand('copy'); document.body.removeChild(ta); cb(ok);
      } catch (e) { cb(false); }
    }
    if (navigator.clipboard && window.isSecureContext) { navigator.clipboard.writeText(txt).then(function () { cb(true); }, fallback); }
    else { fallback(); }
  }
  $('cpy').addEventListener('click', function () {
    var b = this, picked = items.filter(function (i) { return brief[i.link]; }), n = picked.length;
    if (!n) return;
    var lines = picked.map(function (i) { return '[' + i.topics.join(', ') + '] ' + i.title + ' - ' + i.source + (i.ts ? ', ' + fDate.format(i.ts) : ''); });
    var txt = 'NEXUS by GIA brief - ' + fDate.format(NOW) + '\n\n' + lines.join('\n');
    copyText(txt, function (ok) {
      b.textContent = ok ? 'Copied ' + n + (n === 1 ? ' headline' : ' headlines') : 'Copy failed';
      setTimeout(function () { b.textContent = 'Copy brief'; }, 1800);
    });
  });

  var root = document.documentElement, tb = $('theme');
  function cur() {
    var t = root.getAttribute('data-theme'); if (t) return t;
    return window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
  function setLabel() { tb.textContent = cur() === 'dark' ? 'Light mode' : 'Dark mode'; }
  var st = lsGet('nexus-theme'); if (st) root.setAttribute('data-theme', st);
  tb.addEventListener('click', function () {
    var n = cur() === 'dark' ? 'light' : 'dark'; root.setAttribute('data-theme', n); lsSet('nexus-theme', n); setLabel();
  });
  setLabel();

  function tick() { NOW = Date.now(); renderTop(); render(); }
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
  document.addEventListener('visibilitychange', function () { if (!document.hidden) { tick(); clockTick(); check(); } });

  setDensity(); renderTop(); render();
})();
</script>
</body></html>"""

page = (
    TEMPLATE.replace("__TITLE__", html.escape(PAGE_TITLE))
    .replace("__DATA__", data_json)
)

os.makedirs("docs", exist_ok=True)
with open("docs/index.html", "w", encoding="utf-8") as f:
    f.write(page)
