"""Build one merged XMLTV (picked_epg.xml) holding guide data for exactly the channels in epg_coverage.csv.

For every channel the builder collects candidate guide entries (the provider's own XMLTV plus public XMLTV files:
MJH Australia per city, epgshare01 UK/US/AU/Canada/Ireland/Plex/FanDuel/US-locals/...), matched by channel name
with region rules, hand-checked overrides, and picks the candidate that covers the most of the next 48 hours.
Usage: python -I -X utf8 build_epg.py <folder with provider.xml and *.xml.gz>
Writes picked_epg.xml into that folder and epg_mapping.csv next to this script.
"""
import csv
import difflib
import gzip
import hashlib
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

d = Path(sys.argv[1])
rows = list(csv.DictReader(open("epg_coverage.csv", encoding="utf-8-sig")))
opener = lambda p: gzip.open(p, "rb") if str(p).endswith(".gz") else open(p, "rb")

NOW = datetime.now(timezone.utc)
WIN_END = NOW + timedelta(hours=48)


def parse_t(s):
    """XMLTV time 'YYYYMMDDHHMMSS +ZZZZ' -> aware datetime."""
    s = s.strip()
    dt = datetime.strptime(s[:14], "%Y%m%d%H%M%S")
    if len(s) > 14:
        z = s[14:].strip()
        sign = -1 if z.startswith("-") else 1
        z = z.lstrip("+-")
        off = timedelta(hours=int(z[:2]), minutes=int(z[2:4])) * sign
    else:
        off = timedelta(0)
    return dt.replace(tzinfo=timezone(off))


def scan(path):
    """Return (channel names, programme counts, hours of programmes in the next 48h) for an XMLTV file."""
    names, counts, cov = {}, {}, {}
    with opener(path) as fh:
        for _, el in ET.iterparse(fh, events=("end",)):
            if el.tag == "channel":
                names[el.get("id")] = [dn.text for dn in el.findall("display-name") if dn.text]
                el.clear()
            elif el.tag == "programme":
                c = el.get("channel")
                counts[c] = counts.get(c, 0) + 1
                try:
                    a, b = max(parse_t(el.get("start")), NOW), min(parse_t(el.get("stop")), WIN_END)
                    if b > a:
                        cov[c] = cov.get(c, 0.0) + (b - a).total_seconds() / 3600
                except Exception:
                    pass
                el.clear()
    return names, counts, cov


idx = {}
for f in sorted(d.glob("*.xml.gz")):
    n, c, v = scan(f)
    idx[f.name.replace(".xml.gz", "")] = {"channels": n, "progs": c, "cov": v}
prov_names, prov_counts, prov_cov = scan(d / "provider.xml")


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").upper()
    s = re.sub(r"\((?:H|GT)\)|\[.*?\]", " ", s)
    s = re.sub(r"^(US|UK|AU|NOW|VIP|PRIME|SPORTS|PK|CA|NZ|CRB|SG)\s*:\s*", "", s)
    s = re.sub(r"\b(HD|SD|UHD|RAW|HEVC|4K|3840P|60FPS|FHD|LHD|H|TV)\b", " ", s)
    s = s.replace("&", " AND ").replace("+", " PLUS ")
    s = re.sub(r"\.(US|UK|AU|CA)\d*$", "", s)
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


AU_SRC = [k for k in idx if k.startswith("mjh_")]
byname = {}
for src, v in idx.items():
    if src == "us_locals1":
        continue
    for cid, names in v["channels"].items():
        if v["progs"].get(cid, 0) == 0:
            continue
        for n in set([cid] + names):
            nn = norm(n)
            if nn:
                byname.setdefault(nn, []).append((src, cid))
allnames = list(byname)
byname_ns = {}
for k, v in byname.items():
    byname_ns.setdefault(k.replace(" ", ""), []).extend(v)

pref = {"UK": ["uk1", "us_sports1", "us2"], "AU": AU_SRC + ["au1"], "US": ["us_sports1", "us2", "uk1"],
        "WT": ["uk1", "us_sports1", "au1", "us2"]}
# Guides from another country show another country's schedule, so only use sources that fit the channel's region.
ALLOWED = {"US": {"us2", "us_sports1", "us_locals1", "plex1", "fanduel1", "ca2"},
           "UK": {"uk1", "ie1", "us_sports1", "us2", "plex1"},
           "AU": set(AU_SRC) | {"au1", "nz1"}}


def region_ok(cat, cands):
    region = cat.split("|")[0].strip()
    if region in ALLOWED:
        return [x for x in cands if x[0] in ALLOWED[region]]
    return list(cands)


loc = idx["us_locals1"]
locids = [i for i in loc["channels"] if loc["progs"].get(i, 0) > 0]
mjh_ok = {(s, i) for s in AU_SRC for i in idx[s]["channels"] if idx[s]["progs"].get(i, 0) > 0}
STATE = {"NSW": "nsw", "VIC": "vic", "QLD": "qld", "SA": "sa", "WA": "wa", "GOLD-COAST": "qld",
         "LISMORE": "lismore", "NEWCASTLE": "newcastle"}
KIND = {"9GEM": "gem", "9GO!": "go", "9LIFE": "life", "9RUSH": "rush", "CHANNEL 9": "channel-9"}

# Hand-checked matches: (regex on the channel name, source, guide id).
OVERRIDES = [
    (r"NESN PLUS", "us2", "NESN+.New.England.Sports.Network.Plus.us2"),
    (r"\bLFC TV", "uk1", "LFCTV.HD.uk"),
    (r"MANCHESTER UNITED", "uk1", "MUTV.HD.uk"),
    (r"FOX SPORTS YES HD", "us2", "Yes.Network.us2"),
    (r"^(US: )?SPIKE HD", "us2", "Paramount.Network.HD.us2"),
    (r"SKY SPORTS PL ", "ie1", "Sky.Sports.Premier.League.HD.ie"),
    (r"NBC GOLF", "us2", "Golf.Channel.HD.us2"),
    (r"DATELINE 24/7", "plex1", "plex.tv.Dateline.plex"),
    (r"FAMILY FEUD STEVE HARVEY", "plex1", "plex.tv.Family.Feud.plex"),
    (r"LAW & CRIME", "plex1", "plex.tv.Law.&.Crime.plex"),
    (r"NBC LX HOME", "plex1", "plex.tv.LX.Home.Streaming.plex"),
    (r"REELZ", "us2", "ReelzChannel.HD.us2"),
    (r"BLOOMBERG (HD|TV\+)", "au1", "BloombergTelevision.au"),
    (r"ABC KIDS NATIONAL", "mjh_au_all", "mjh-abc-kids"),
    (r"CHANNEL ?7TWO SYDNEY", "au1", "7TWOSydney.au"),
    (r"(^|: )CBS HD$", "us_locals1", "WCBS-DT.us_locals1"),
    (r"(^|: )ABC \(EAST\)", "us_locals1", "WABC-DT.us_locals1"),
    (r"WILLOW CRICKET EXTRA", "us2", "Willow.2.Xtra.us2"),
]
ALLOW_FUZZY = re.compile(r"BEIN SPORTS AU [123]|PREMIER SPORTS [12] IRELAND|ANGLERS PARADIS|ACOUNTRY PRACTICE|ESPN U HD|"
                         r"FOX SPORTS (CAROLINAS|TENNESSEE) HD|TENNIS CHANNEL PLUS 2|PICKLEBALL")
DENY_FUZZY = re.compile(r"SKY SPORTS [12] .*3840")


def special(name):
    """Single hand-made answers: overrides, New York ABC, Nine by state, US locals by call sign."""
    up = unicodedata.normalize("NFKD", name).upper()
    out = []
    for rx, osrc, oid in OVERRIDES:
        if re.search(rx, up) and osrc in idx and idx[osrc]["progs"].get(oid, 0) > 0:
            out.append((osrc, oid))
    if re.search(r"\bABC HD \[NEW YORK\]", up) and loc["progs"].get("WABC-DT.us_locals1", 0) > 0:
        out.append(("us_locals1", "WABC-DT.us_locals1"))
    m = re.search(r"9NOW (9GEM|9GO!|9LIFE|9RUSH|CHANNEL 9) \(([A-Z-]+)\)", up)
    if m:
        st = STATE.get(m.group(2), "")
        if m.group(1) != "CHANNEL 9" and st in ("lismore", "newcastle"):
            st = "nsw"
        extra = ["mjh-channel-9-gold-coast"] if (m.group(1) == "CHANNEL 9" and m.group(2) == "GOLD-COAST") else []
        for cand in extra + [f"mjh-{KIND[m.group(1)]}-{st}", f"mjh-channel-9-{st}"]:
            for s in AU_SRC:
                if (s, cand) in mjh_ok:
                    out.append((s, cand))
    m = re.search(r"\b([KW][A-Z]{2,3})\b", up)
    if m and m.group(1) != "WWE":
        cs = m.group(1)
        c = [i for i in locids if i.startswith(cs + "-DT.") or i.startswith(cs + "-CD.") or i.startswith(cs + ".")]
        c = [i for i in c if "_." not in i] or c
        if c:
            out.append(("us_locals1", sorted(c)[0]))
    return out


def name_candidates(nm, cat):
    """All public-source guide entries that match this channel by name (region-filtered)."""
    n = norm(nm)
    variants = [n]
    if "SKY SPORTS" in n:
        variants.append(n.replace("SKY SPORTS", "SKYSP"))
    m_tsn = re.match(r"TSN SPORT (\d)", n)
    if m_tsn:
        variants.append("TSN " + m_tsn.group(1))
    if n.startswith("FOX SPORTS "):
        rest = n[len("FOX SPORTS "):]
        variants += ["FANDUEL SPORTS NETWORK " + rest, "FANDUEL SPORTS " + rest]
    found = []
    for v in variants:
        for key in (v, v.replace(" ", "")):
            found += byname.get(key, []) + byname_ns.get(key, [])
    if not found:
        up = unicodedata.normalize("NFKD", nm).upper()
        for v in variants:
            close = difflib.get_close_matches(v, allnames, n=1, cutoff=0.86)
            if close:
                sc = difflib.SequenceMatcher(None, v, close[0]).ratio()
                # Fuzzy matches caused wrong guides (NBC Sports -> CBS Sports, TSN -> TNT ...): accept only
                # very close ones or ones checked by hand.
                if (sc >= 0.95 and not DENY_FUZZY.search(up)) or ALLOW_FUZZY.search(up):
                    found += byname[close[0]]
                break
    return region_ok(cat, found)


def rank(cat, src):
    order = pref.get(cat.split("|")[0].strip(), [])
    return order.index(src) if src in order else 99


res = []
for r in rows:
    cat, nm, pid = r["Cat"], r["Name"], r["EpgId"]
    cands = []  # (hours covered in next 48h, -rank, source, id)
    if pid and prov_counts.get(pid, 0) > 0:
        cands.append((prov_cov.get(pid, 0.0), 0, "provider", pid))
    seen = set()
    for src, cid in region_ok(cat, special(nm)) + name_candidates(nm, cat):
        if (src, cid) in seen:
            continue
        seen.add((src, cid))
        cands.append((idx[src]["cov"].get(cid, 0.0), -rank(cat, src), src, cid))
    best = max(cands, key=lambda c: (round(c[0]), c[1])) if cands else None
    res.append({"Cat": cat, "Name": nm, "ProviderEpgId": pid, "ProviderProgs": r["Progs"],
                "Source": best[2] if best else "", "GuideId": best[3] if best else "",
                "Hours48": round(best[0], 1) if best else 0,
                # kept so older scripts still work: ExtSource/ExtId = chosen source when it is not the provider
                "ExtSource": best[2] if best and best[2] != "provider" else "",
                "ExtId": best[3] if best and best[2] != "provider" else "", "Score": 1})

want = {}
for i, r in enumerate(res):
    # stable across runs (cloud and laptop) so the playlist's tvg-ids keep matching the published guide
    r["PkId"] = "pk" + hashlib.md5(f'{r["Cat"]}|{r["Name"]}'.encode("utf-8")).hexdigest()[:8]
    if r["GuideId"] and r["Hours48"] > 0:
        want.setdefault((r["Source"], r["GuideId"]), []).append(r["PkId"])

paths = {"provider": d / "provider.xml"}
for f in d.glob("*.xml.gz"):
    paths[f.name.replace(".xml.gz", "")] = f
root = ET.Element("tv", {"generator-info-name": "picked-channels merge"})
for r in res:
    ch = ET.SubElement(root, "channel", {"id": r["PkId"]})
    ET.SubElement(ch, "display-name").text = r["Name"]
nprog = 0
for src in set(s for s, _ in want):
    with opener(paths[src]) as fh:
        for _, el in ET.iterparse(fh, events=("end",)):
            if el.tag == "programme":
                for pk in want.get((src, el.get("channel")), []):
                    el2 = ET.fromstring(ET.tostring(el))
                    el2.set("channel", pk)
                    root.append(el2)
                    nprog += 1
            if el.tag in ("programme", "channel"):
                el.clear()
ET.ElementTree(root).write(d / "picked_epg.xml", encoding="utf-8", xml_declaration=True)
with open("epg_mapping.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(res[0]))
    w.writeheader()
    w.writerows(res)

good = [r for r in res if r["Hours48"] >= 12]
anyd = [r for r in res if r["Hours48"] > 0]
print(f"channels {len(res)}  with any guide in next 48h {len(anyd)} ({round(100 * len(anyd) / len(res))}%)  "
      f"with >=12h {len(good)} ({round(100 * len(good) / len(res))}%)  programmes {nprog}  "
      f"file {round((d / 'picked_epg.xml').stat().st_size / 1e6, 1)} MB")
g = defaultdict(lambda: [0, 0, 0])
for r in res:
    g[r["Cat"]][0] += 1
    g[r["Cat"]][1] += int(r["Hours48"] > 0)
    g[r["Cat"]][2] += int(r["Hours48"] >= 12)
for c, (t, p, q) in g.items():
    print(f"{p:4d}/{t:4d} any, {q:4d} >=12h  {c}")
