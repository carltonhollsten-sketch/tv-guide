"""Builds site/whatson.html + site/whatson.json: upcoming games you care about, in US Eastern time, with the Sparkle channels carrying them.

Fixtures come from ESPN's public scoreboards. Channels: a game is "confirmed" when a picked channel's guide listing names one of the teams
near the start time, otherwise the usual channels for that sport are shown as "may be on". Needs (in the working folder) picked_channels.csv
(written from the PICKED_CHANNELS secret by cloud_build.py), clean_mapping.csv (clean_names.py), epg_mapping.csv and src/picked_epg.xml.
"""
import csv
import html
import json
import re
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ET_TZ = ZoneInfo("America/New_York")
NOW = datetime.now(timezone.utc)
DAYS = 7
API = "https://site.api.espn.com/apis/site/v2/sports/"
DUR = {"NFL": 3.5, "NBA": 2.7, "NHL": 2.7, "EPL": 2.0, "AFL": 2.7, "F1": 2.0, "Cricket": 8}
USUAL = {
    "NFL": ["NFL Network", "NFL RedZone", "ESPN", "ESPN USA", "ESPN 2", "Fox Sports 1", "CBS", "ESPN 1"],
    "NBA": ["ESPN", "ESPN USA", "ESPN 2", "ESPN 1", "Fox Sports 1"],
    "NHL": ["NESN", "NESN Boston", "NHL Network", "ESPN", "ESPN 2"],
    "EPL": ["Sky Sports Premier League", "Sky Sports Main Event", "Premier League", "Premier League TV", "TNT Sports 1", "beIN Sports 1",
            "Fox Soccer Plus", "NBC Sports Network"],
    "F1": ["Sky Sports F1", "Formula 1 Channel (FM1TV)", "ESPN 2", "ESPN", "Fox Sports 503"],
    "AFL": ["Fox Sports 502", "Fox Sports 503", "Fox Sports 501", "7mate Melbourne", "Channel7 Sydney", "7Now"],
    "Cricket": ["Fox Cricket (AU)", "Willow Cricket (US)", "Willow Cricket Extra (US)", "Sky Sports Cricket (UK)", "Star Sports 1 English",
                "Sky Sport 1 (NZ)"],
}
LEAGUES = {"NFL": "football/nfl", "NBA": "basketball/nba", "NHL": "hockey/nhl", "EPL": "soccer/eng.1",
           "AFL": "australian-football/afl", "F1": "racing/f1"}
TEAM_FILTER = {"NBA": "celtics", "NHL": "bruins"}


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def norm(s):
    s = unicodedata.normalize("NFKD", s)
    return re.sub(r"\s+", " ", re.sub(r"[^\x20-\x7E]", "", s)).strip()


def parse(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)


events = {}


def add(sport, key, title, start, league, teams, detail=""):
    if start < NOW - timedelta(hours=DUR[sport] + 0.5) or start > NOW + timedelta(days=DAYS):
        return
    events[(sport, key)] = {"sport": sport, "title": title, "start": start, "league": league, "teams": teams, "detail": detail}


for sport, path in LEAGUES.items():
    for d in range(-1, DAYS + 1):
        day = (NOW + timedelta(days=d)).strftime("%Y%m%d")
        try:
            data = get(f"{API}{path}/scoreboard?dates={day}")
        except Exception as e:
            print(f"  {sport} {day}: {type(e).__name__}")
            continue
        for ev in data.get("events", []):
            if sport == "F1":
                for c in ev.get("competitions", []):
                    kind = (c.get("type") or {}).get("text") or (c.get("type") or {}).get("abbreviation") or "Session"
                    add(sport, f'{ev["id"]}-{c.get("id")}', f'{ev.get("shortName") or ev["name"]}: {kind}', parse(c["date"]),
                        "Formula 1", ["grand prix", "formula"], "")
                continue
            comp = ev["competitions"][0]
            teams = [t["team"] for t in comp["competitors"]]
            names = [t.get("displayName", "") for t in teams]
            if sport in TEAM_FILTER and not any(TEAM_FILTER[sport] in n.lower() for n in names):
                continue
            keys = []
            for t in teams:
                for k in (t.get("nickname"), t.get("shortDisplayName"), t.get("name")):
                    if k and len(k) > 3 and k.lower() not in keys:
                        keys.append(k.lower())
            away = next((t for t in comp["competitors"] if t.get("homeAway") == "away"), comp["competitors"][0])["team"]
            home = next((t for t in comp["competitors"] if t.get("homeAway") == "home"), comp["competitors"][-1])["team"]
            add(sport, ev["id"], f'{away["displayName"]} at {home["displayName"]}' if sport != "EPL" else f'{home["displayName"]} v {away["displayName"]}',
                parse(ev["date"]), {"NFL": "NFL", "NBA": "NBA", "NHL": "NHL", "EPL": "Premier League", "AFL": "AFL"}[sport], keys)

try:
    hdr = get("https://site.web.api.espn.com/apis/v2/scoreboard/header?sport=cricket&lang=en&region=us")
    for lg in hdr["sports"][0]["leagues"]:
        name = lg["name"]
        for ev in lg.get("events", []):
            blob = f'{name} {ev.get("name", "")}'
            if re.search(r"Australia|Sheffield|Big Bash|BBL|Ashes|Marsh|World Cup|T20 World|ICC", blob, re.I):
                teams = [w for w in re.split(r"\s+v\s+|\s+vs\s+", ev["name"]) if w]
                add("Cricket", ev["id"], ev["name"], parse(ev["date"]), re.sub(r"\s*20\d\d(/\d\d)?$", "", name), [t.lower() for t in teams])
except Exception as e:
    print("  cricket:", type(e).__name__)
print("fixtures:", {s: sum(1 for k in events if k[0] == s) for s in LEAGUES | {"Cricket": 0}}, flush=True)

# ---- channels: clean names of the playlist, and what the guide says is on them
clean = {}
for m in csv.DictReader(open("clean_mapping.csv", encoding="utf-8-sig")):
    if m["Action"] == "keep":
        clean[f'{m["Category"]}\t{norm(m["Channel"])}'] = (m["NewName"], m["NewCategory"])
pk_name = {}
for m in csv.DictReader(open("epg_mapping.csv", encoding="utf-8-sig")):
    c = clean.get(f'{m["Cat"]}\t{norm(m["Name"])}')
    if c:
        pk_name[m["PkId"]] = c
have = {}
for nm, cat in clean.values():
    have.setdefault(nm.lower(), []).append(f"{cat} › {nm}")
want_ids = set(pk_name)
progs = {}
srcxml = Path("src/picked_epg.xml") if Path("src/picked_epg.xml").exists() else Path("picked_epg.xml")
horizon = NOW + timedelta(days=DAYS + 1)
for _, el in ET.iterparse(srcxml, events=("end",)):
    if el.tag != "programme":
        continue
    cid = el.get("channel")
    if cid in want_ids:
        st = datetime.strptime(el.get("start")[:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        off = el.get("start")[15:20]
        if off and off[0] in "+-":
            st -= timedelta(hours=int(off[1:3]), minutes=int(off[3:5])) * (1 if off[0] == "+" else -1)
        if NOW - timedelta(hours=10) < st < horizon:
            text = (el.findtext("title") or "") + " " + (el.findtext("desc") or "")
            progs.setdefault(cid, []).append((st, text.lower()))
    el.clear()

out = []
for (sport, _), e in sorted(events.items(), key=lambda kv: kv[1]["start"]):
    confirmed = []
    for cid, lst in progs.items():
        name, cat = pk_name[cid]
        for st, text in lst:
            if abs((st - e["start"]).total_seconds()) <= 2700 and any(t in text for t in e["teams"]):
                label = f"{cat} › {name}"
                if label not in confirmed:
                    confirmed.append(label)
                break
    may = []
    for nm in USUAL.get(sport, []):
        for label in have.get(nm.lower(), []):
            if label not in confirmed and label not in may:
                may.append(label)
    out.append({"sport": sport, "league": e["league"], "title": e["title"], "start": e["start"].isoformat(),
                "mins": int(DUR[sport] * 60), "confirmed": confirmed[:4], "may": may[:5]})

Path("site").mkdir(exist_ok=True)
Path("site/whatson.json").write_text(json.dumps({"updated": NOW.isoformat(), "events": out}, ensure_ascii=False), encoding="utf-8")


def fmt(d):
    return d.astimezone(ET_TZ).strftime("%I:%M %p").lstrip("0")


COL = {"NFL": "#4f8cff", "NBA": "#2ecc71", "NHL": "#9aa7b5", "EPL": "#b57bff", "AFL": "#ff9f43", "F1": "#ff5c5c", "Cricket": "#ffd43b"}
rows = []
for e in out:
    st = datetime.fromisoformat(e["start"])
    rows.append(
        f'<li class="g" data-start="{e["start"]}" data-mins="{e["mins"]}" data-day="{st.astimezone(ET_TZ):%Y-%m-%d}">'
        f'<div class="t">{fmt(st)}<span class="live">LIVE</span></div><div class="m">'
        f'<span class="tag" style="background:{COL[e["sport"]]}">{html.escape(e["sport"])}</span> <b>{html.escape(e["title"])}</b>'
        f'<div class="lg">{html.escape(e["league"])}</div>'
        + "".join(f'<div class="ch ok">✓ {html.escape(c)}</div>' for c in e["confirmed"])
        + (f'<div class="ch may">May be on: {html.escape(" · ".join(e["may"]))}</div>' if e["may"] and not e["confirmed"] else "")
        + ("" if e["confirmed"] or e["may"] else '<div class="ch may">No matching channel in your list</div>')
        + "</div></li>")
page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>What's on</title><style>
:root{{--bg:#0e1116;--fg:#f2f4f8;--mut:#8b95a5;--card:#171c24;--ok:#4cd68a}}
@media (prefers-color-scheme:light){{:root{{--bg:#f4f6fa;--fg:#14181f;--mut:#5b6573;--card:#fff;--ok:#12803f}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:18px/1.35 system-ui,sans-serif}}main{{max-width:820px;margin:0 auto;padding:16px}}
h1{{font-size:1.5em;margin:.2em 0}}.sub{{color:var(--mut);font-size:.8em;margin-bottom:12px}}h2{{font-size:1em;color:var(--mut);margin:22px 0 8px;text-transform:uppercase;letter-spacing:.06em}}
ul{{list-style:none;margin:0;padding:0}}li.g{{display:flex;gap:14px;background:var(--card);border-radius:12px;padding:12px 14px;margin:0 0 8px}}
.t{{min-width:76px;font-weight:700;font-variant-numeric:tabular-nums}}.live{{display:none;margin-top:4px;font-size:.62em;background:#e0242b;color:#fff;border-radius:4px;padding:1px 6px;width:max-content}}
li.now .live{{display:block}}li.now{{outline:2px solid #e0242b}}li.over{{display:none}}.tag{{color:#fff;font-size:.65em;font-weight:700;border-radius:4px;padding:1px 6px;vertical-align:middle}}
.lg,.may{{color:var(--mut);font-size:.8em}}.ok{{color:var(--ok);font-weight:600;font-size:.9em}}.m{{min-width:0}}</style></head><body><main>
<h1>What's on</h1><div class="sub">US Eastern time &middot; updated {NOW.astimezone(ET_TZ):%a %b %d, %I:%M %p} &middot; in Sparkle press Left for the list, then pick the channel</div>
<div id="list"></div><ul id="src" hidden>{''.join(rows)}</ul>
<script>
var days={{}},src=[].slice.call(document.querySelectorAll('li.g'));
function ds(k){{var d=new Date(k+'T12:00:00'),t=new Date(),k0=t.toLocaleDateString('en-CA',{{timeZone:'America/New_York'}});
 var tm=new Date(t.getTime()+864e5).toLocaleDateString('en-CA',{{timeZone:'America/New_York'}});
 return k==k0?'Today':k==tm?'Tomorrow':d.toLocaleDateString('en-US',{{weekday:'long',month:'short',day:'numeric'}});}}
function draw(){{var now=Date.now(),by={{}};src.forEach(function(li){{var s=+new Date(li.dataset.start),e=s+li.dataset.mins*6e4;
 li.className='g'+(now>=s&&now<e?' now':'')+(now>=e?' over':'');if(now<e)(by[li.dataset.day]=by[li.dataset.day]||[]).push(li)}});
 var out=document.getElementById('list');out.innerHTML='';Object.keys(by).sort().forEach(function(k){{var h=document.createElement('h2');h.textContent=ds(k);
 var u=document.createElement('ul');by[k].forEach(function(l){{u.appendChild(l.cloneNode(true))}});out.appendChild(h);out.appendChild(u)}});
 if(!out.children.length)out.textContent='Nothing coming up in the next few days.'}}
draw();setInterval(draw,60000);
</script></main></body></html>"""
Path("site/whatson.html").write_text(page, encoding="utf-8")
print("whatson:", len(out), "games;", sum(1 for e in out if e["confirmed"]), "with a confirmed channel")
