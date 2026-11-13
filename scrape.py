#!/usr/bin/env python3
"""
Personal news monitor (free MVP).

What it does, each time it runs:
  1. Visits the pages listed in SOURCES (politely, one at a time).
  2. Pulls out the links and list/table items on each page.
  3. Remembers what it has seen before, so it can tell what is NEW.
  4. Flags items that contain your KEYWORDS.
  5. Writes a simple web page (docs/index.html) and a spreadsheet (docs/matches.csv).

If a page cannot be read, the page it writes says so. It never fails silently.
"""
import csv, hashlib, html, json, re, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib import robotparser
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# ----------------------------------------------------------------- settings
KEYWORDS = ["AI", "artificial intelligence", "science", "innovation"]

SOURCES = [
    {"id": "aph-calendar", "name": "Parliament: events calendar",
     "url": "https://www.aph.gov.au/news_and_events/events_calendar"},
    {"id": "aph-hearings", "name": "Parliament: watch, read, listen (hearings)",
     "url": "https://www.aph.gov.au/News_and_Events/Watch_Read_Listen"},
    {"id": "aph-tabled", "name": "Parliament: tabled documents",
     "url": "https://www.aph.gov.au/Parliamentary_Business/Tabled_Documents"},
    {"id": "industry-foi", "name": "Industry department: FOI information",
     "url": "https://www.industry.gov.au/accessing-information/freedom-information-disclosure"},
    # This is the page that lists the actual 2026 disclosure log entries.
    {"id": "industry-foi-2026", "name": "Industry department: FOI disclosure log 2026",
     "url": "https://www.industry.gov.au/accessing-information/freedom-information-disclosure/2026"},
]

# Put your own contact details here so site owners can reach you if needed.
USER_AGENT = "PersonalNewsMonitor/0.1 (personal research project; contact: you@example.com)"
KEEP_DAYS = 60          # forget items older than this
NEW_DAYS = 7            # show a NEW badge for this long
PAUSE_SECONDS = 5       # wait between requests
OUT = Path("docs")
# --------------------------------------------------------------------------

SKIP_HREF = ("#", "mailto:", "tel:", "javascript:")
BOILERPLATE = ("skip to", "back to top", "privacy", "accessibility", "contact us",
               "was this page", "sign in", "log in", "site map", "cookie")


def now():
    return datetime.now(timezone.utc)


def clean(s):
    return re.sub(r"\s+", " ", s or "").strip()


def keyword_hits(text):
    hits = []
    for k in KEYWORDS:
        flags = 0 if len(k) <= 3 else re.I   # short words like AI must be capitals
        if re.search(r"(?<![A-Za-z])" + re.escape(k) + r"(?![A-Za-z])", text, flags):
            hits.append(k)
    return hits


def allowed_by_robots(url):
    p = urlparse(url)
    rp = robotparser.RobotFileParser()
    try:
        r = requests.get(f"{p.scheme}://{p.netloc}/robots.txt",
                         headers={"User-Agent": USER_AGENT}, timeout=20)
        if r.status_code >= 400:
            return True            # no robots.txt: nothing forbids us
        rp.parse(r.text.splitlines())
        return rp.can_fetch(USER_AGENT, url)
    except requests.RequestException:
        return True


def extract_items(soup, base_url):
    for t in soup(["script", "style", "noscript", "nav", "header", "footer", "form"]):
        t.decompose()
    items, seen = [], set()

    def add(title, desc, url):
        key = hashlib.sha1(f"{url}|{title}".encode()).hexdigest()[:16]
        if key in seen:
            return
        seen.add(key)
        items.append({"key": key, "title": title, "desc": desc, "url": url})

    for a in soup.find_all("a", href=True):
        title = clean(a.get_text())
        href = a["href"].strip()
        if len(title) < 20 or href.lower().startswith(SKIP_HREF):
            continue
        if any(b in title.lower() for b in BOILERPLATE):
            continue
        parent = a.find_parent(["li", "tr", "article", "p"])
        context = clean(parent.get_text()) if parent else ""
        desc = context[:300] if len(context) > len(title) + 10 else ""
        add(title, desc, urljoin(base_url, href))

    # Rows and list items with no link (some calendars are plain text).
    for tag in soup.find_all(["tr", "li"]):
        if tag.find("a"):
            continue
        text = clean(tag.get_text())
        if 30 <= len(text) <= 400 and not any(b in text.lower() for b in BOILERPLATE):
            add(text[:140], text if len(text) > 140 else "", base_url)
    return items


def load_state():
    f = OUT / "data.json"
    if f.exists():
        return json.loads(f.read_text())
    return {"items": {}, "sources": {}}


def check_source(src, state, stamp):
    health = state["sources"].get(src["id"], {})
    health.update({"name": src["name"], "url": src["url"], "checked": stamp})
    state["sources"][src["id"]] = health
    try:
        if not allowed_by_robots(src["url"]):
            health.update(status="problem", message="The site's robots.txt asks automated tools not to read this page, so it was skipped.")
            return
        r = requests.get(src["url"], headers={"User-Agent": USER_AGENT}, timeout=30)
        if r.status_code != 200:
            health.update(status="problem", message=f"The site answered with error {r.status_code} (it may be blocking automated visits).")
            return
        items = extract_items(BeautifulSoup(r.text, "html.parser"), src["url"])
    except requests.RequestException as e:
        health.update(status="problem", message=f"Could not reach the page: {type(e).__name__}.")
        return

    if not items:
        health.update(status="problem", message="The page loaded but no items were found. It may need JavaScript, or its layout changed.")
        return

    first_run = not any(i["source"] == src["id"] for i in state["items"].values())
    new_count = 0
    for it in items:
        k = f'{src["id"]}:{it["key"]}'
        if k not in state["items"]:
            state["items"][k] = {"source": src["id"], "title": it["title"], "desc": it["desc"],
                                 "url": it["url"], "first_seen": stamp, "baseline": first_run,
                                 "matches": keyword_hits(it["title"] + " " + it["desc"])}
            new_count += 1
    health.update(status="ok", message=f"{len(items)} items on the page, {0 if first_run else new_count} new this check.", count=len(items))
    if new_count and not first_run:
        health["last_new"] = stamp


def render(state):
    t = now()
    updated = t.strftime("%a %d %b %Y, %H:%M UTC")
    new_cut = (t - timedelta(days=NEW_DAYS)).isoformat()
    names = {i: s["name"] for i, s in state["sources"].items()}
    rows = sorted(state["items"].values(), key=lambda x: x["first_seen"], reverse=True)
    matched = [r for r in rows if r["matches"]]
    e = html.escape

    def mark(text, hits):
        out = e(text)
        for k in hits:
            out = re.sub(r"(?<![A-Za-z])(" + re.escape(e(k)) + r")(?![A-Za-z])", r"<mark>\1</mark>", out,
                         flags=0 if len(k) <= 3 else re.I)
        return out

    def card(r):
        badge = '<span class="new">New</span>' if (r["first_seen"] >= new_cut and not r["baseline"]) else ""
        tags = "".join(f'<span class="tag">{e(k)}</span>' for k in r["matches"])
        desc = f'<p>{mark(r["desc"], r["matches"])}</p>' if r["desc"] else ""
        d = r["first_seen"][:10]
        return (f'<article data-k="{e("|".join(r["matches"]))}"><div class="meta">{badge}'
                f'<span>{e(names.get(r["source"], r["source"]))}</span><span>Found {d}</span></div>'
                f'<h3><a href="{e(r["url"])}">{mark(r["title"], r["matches"])}</a></h3>{desc}'
                f'<div class="tags">{tags}</div></article>')

    problems = [s for s in state["sources"].values() if s.get("status") != "ok"]
    warn = ""
    if problems:
        warn = '<section class="warn"><b>Needs attention</b><ul>' + "".join(
            f'<li><b>{e(s["name"])}</b>: {e(s["message"])}</li>' for s in problems) + "</ul></section>"
    chips = '<button class="chip on" data-f="">All</button>' + "".join(
        f'<button class="chip" data-f="{e(k)}">{e(k)}</button>' for k in KEYWORDS)
    src_rows = "".join(
        f'<tr><td><a href="{e(s["url"])}">{e(s["name"])}</a></td><td>{"Working" if s.get("status")=="ok" else "Needs attention"}</td>'
        f'<td>{e(s.get("message",""))}</td><td>{e(s.get("last_new","")[:10] or "none yet")}</td></tr>'
        for s in state["sources"].values())
    other = [r for r in rows if not r["matches"]][:100]
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>My news monitor</title>
<style>
:root{{--bg:#f2f6f6;--s:#fff;--ink:#0f1c24;--mut:#4d5a66;--line:#d3d9df;--acc:#0b6e6e;--mark:#fbe38a;--warn:#fdeae6;--warnink:#7a1d10}}
@media (prefers-color-scheme:dark){{:root{{color-scheme:dark;--bg:#0f1a1a;--s:#172424;--ink:#e6eeee;--mut:#9ab0b0;--line:#2b4040;--acc:#5cc4c4;--mark:#6b5a1c;--warn:#3a1f1a;--warnink:#ffb4a5}}}}
body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,sans-serif}}
main{{max-width:820px;margin:0 auto;padding:24px 16px 64px}}
h1{{margin:0 0 4px}}h3{{margin:6px 0;font-size:1.1rem}}a{{color:var(--acc)}}
.sub{{color:var(--mut);margin:0 0 16px}}
article,.warn,table{{background:var(--s);border:1px solid var(--line);border-radius:8px}}
article{{padding:12px 14px;margin-bottom:10px}}.warn{{padding:12px 14px;margin-bottom:16px;background:var(--warn);color:var(--warnink)}}
.meta{{display:flex;gap:12px;flex-wrap:wrap;color:var(--mut);font-size:.88rem}}
.new{{background:var(--acc);color:var(--bg);border-radius:4px;padding:0 6px;font-weight:600}}
.tag{{display:inline-block;border:1px solid var(--acc);color:var(--acc);border-radius:99px;padding:0 10px;margin:4px 6px 0 0;font-size:.85rem}}
mark{{background:var(--mark);color:inherit}}p{{margin:4px 0;color:var(--mut)}}
.bar{{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px;align-items:center}}
.chip{{font:inherit;min-height:40px;padding:0 14px;border-radius:99px;border:1px solid var(--line);background:var(--s);color:var(--ink);cursor:pointer}}
.chip.on{{background:var(--ink);color:var(--bg)}}
table{{width:100%;border-collapse:collapse;font-size:.92rem}}td,th{{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
details{{margin:20px 0}}summary{{cursor:pointer;font-weight:600;min-height:40px}}
</style></head><body><main>
<h1>My news monitor</h1><p class="sub">Last checked {updated}. {len(matched)} items match your keywords.</p>
{warn}
<div class="bar">{chips}<a href="matches.csv" style="margin-left:auto">Download CSV</a></div>
<section id="list">{"".join(card(r) for r in matched) or "<p>No matches yet.</p>"}</section>
<details><summary>Everything else found ({len(other)} most recent)</summary>{"".join(card(r) for r in other)}</details>
<h2>Sources</h2><table><tr><th>Source</th><th>Status</th><th>Detail</th><th>Last new item</th></tr>{src_rows}</table>
</main><script>
document.querySelectorAll('.chip').forEach(function(b){{b.onclick=function(){{
document.querySelectorAll('.chip').forEach(function(x){{x.classList.remove('on')}});b.classList.add('on');
var f=b.getAttribute('data-f');
document.querySelectorAll('#list article').forEach(function(a){{
a.style.display=(!f||a.getAttribute('data-k').split('|').indexOf(f)>-1)?'':'none'}})}}}});
</script></body></html>"""
    (OUT / "index.html").write_text(page, encoding="utf-8")
    with open(OUT / "matches.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["found", "source", "title", "keywords", "link"])
        for r in matched:
            w.writerow([r["first_seen"][:10], names.get(r["source"], r["source"]), r["title"],
                        "; ".join(r["matches"]), r["url"]])


def main():
    OUT.mkdir(exist_ok=True)
    state = load_state()
    stamp = now().isoformat()
    for n, src in enumerate(SOURCES):
        if n:
            time.sleep(PAUSE_SECONDS)
        check_source(src, state, stamp)
    cutoff = (now() - timedelta(days=KEEP_DAYS)).isoformat()
    state["items"] = {k: v for k, v in state["items"].items() if v["first_seen"] >= cutoff}
    state["updated"] = stamp
    (OUT / "data.json").write_text(json.dumps(state, indent=1), encoding="utf-8")
    render(state)
    print("Done.")


if __name__ == "__main__":
    main()
