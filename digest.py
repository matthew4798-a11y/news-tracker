import feedparser
import html
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import quote

TOPICS = {
    "CARS": [
        '"CARS program" Philippines DTI BOI automotive',
        '"Comprehensive Automotive Resurgence Strategy"',
    ],
    "RACE": [
        '"RACE program" Philippines DTI automotive',
    ],
    "EVIS": [
        '"EVIS" Philippines electric vehicle incentive',
        '"Electric Vehicle Incentive Strategy" Philippines',
    ],
    "Taxes and tariffs": [
        "Philippines automotive excise tax",
        "Philippines vehicle tariff",
    ],
    "EV incentives (general)": [
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

def fetch(topic):
    url = f"https://news.google.com/rss/search?q={quote(topic)}+when:7d&hl=en-PH&gl=PH&ceid=PH:en"
    return feedparser.parse(url).entries

sections = []
seen = set()
for heading, queries in TOPICS.items():
    items = []
    for q in queries:
        for e in fetch(q)[:8]:
            if e.link not in seen:
                seen.add(e.link)
                items.append(
                    f'<li><a href="{html.escape(e.link)}">{html.escape(e.title)}</a> '
                    f'<small>{html.escape(e.get("published", ""))}</small></li>'
                )
    if not items:
        items = ["<li>No new items found.</li>"]
    sections.append(f"<h2>{html.escape(heading)}</h2><ul>{''.join(items)}</ul>")

now = datetime.now(ZoneInfo("Asia/Manila"))

page = f"""<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Policy News Digest</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 760px; margin: 2rem auto; padding: 0 1rem; line-height: 1.5; }}
  h2 {{ margin-top: 2rem; border-bottom: 1px solid #ddd; }}
  li {{ margin: .5rem 0; }} small {{ color: #666; display: block; }}
</style></head><body>
<h1>Policy News Digest</h1>
<p>Updated {now:%B %d, %Y %I:%M %p} (Manila time)</p>
{''.join(sections)}
</body></html>"""

os.makedirs("docs", exist_ok=True)
open("docs/index.html", "w", encoding="utf-8").write(page)
