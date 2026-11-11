"""Proposes clean display names/categories and hides lower-quality duplicates.

Reads picked_channels.csv, writes clean_mapping.csv (Category, Channel are the ORIGINAL provider values, which
make_playlist.ps1 keeps matching on; NewCategory/NewName are what Sparkle will show; Action = keep|hide).
Usage: python -I -X utf8 clean_names.py
"""
import csv
import re
from pathlib import Path

HERE = Path(__file__).parent
rows = list(csv.DictReader(open(HERE / "picked_channels.csv", encoding="utf-8-sig")))

ACRO = {"ESPN", "NFL", "NHL", "TNT", "UFC", "WWE", "DAZN", "NBC", "CBS", "ABC", "BBC", "SBS", "TSN", "HBO", "AFN", "IPL",
        "FM1TV", "MUTV", "LFC", "NESN", "ION", "TV", "USA", "UK", "AU", "NZ", "SG", "CRB", "SEC", "PL", "HSN", "LOL!", "SNL",
        "MASSN", "PBTV", "FITE", "FUBO", "GINX", "BTV", "KABC", "KCBS", "KCNC", "KDFW", "KDVR", "KNBC", "KTTV", "KTVT", "KUSA",
        "KXAS", "WBBM", "WBTS", "WCBS", "WCVB", "WFAA", "WFLD", "WFXT", "WLS", "WMAQ", "WNBC", "WNYW", "WSBK", "NSW", "VIC",
        "QLD", "SA", "WA", "NITV", "LX", "LA", "II", "NZ"}
SPECIAL = {"BEIN": "beIN", "9NOW": "9Now", "9GO!": "9Go!", "9GEM": "9Gem", "9LIFE": "9Life", "9RUSH": "9Rush", "7FLIX": "7flix",
           "7MATE": "7mate", "7TWO": "7two", "7BRAVO": "7Bravo", "7NOW": "7Now", "REDBULL": "Red Bull", "FOX": "Fox",
           "PREMIER": "Premier", "EUROSPORT": "Eurosport", "SUPERSPORTS": "SuperSport", "YOUTUBE": "YouTube", "REDZONE": "RedZone"}
FIXES = [(r"\bSPRTS\b", "SPORTS"), (r"\bPHILADELFIA\b", "PHILADELPHIA"), (r"\bMAIN EVENTS\b", "MAIN EVENT"),
         (r"\bSKY SPORTS PL\b", "SKY SPORTS PREMIER LEAGUE"), (r"\bSKY SPORTS \+", "SKY SPORTS+"), (r"\bSKY SPORTS\+ ", "SKY SPORTS+ "),
         (r"\bTNT SPORT\b", "TNT SPORTS"), (r"\bTNT SPORTS EVENT\b", "TNT SPORTS EVENT"), (r"\bSKY SPORT\b", "SKY SPORT"),
         (r"\bBEIN SPORTS AU\b", "BEIN SPORTS"), (r"\bBEIN SPORTS 1 ENGLISH\b", "BEIN SPORTS 1")]
JUNK = r"\b(RAW|HD|UHD|HEVC|4K|3840P|60FPS|VIP|SD|HDR)\b|\(H\)"
PREFIX = re.compile(r"^(NOW|UK|US|AU|NZ|SG|CRB|SPORTS|TV|PRIME|VIP)\s*:\s*", re.I)


def titled(s):
    out = []
    s = re.sub(r"^9NOW\s+(?=\S)", "", s)
    for w in s.split():
        lead, trail = re.match(r"^\(?", w).group(0), re.search(r"\)?$", w).group(0)
        w = w[len(lead):len(w) - len(trail)] or w
        u = w.upper()
        if u in ("TVSN", "EWTN", "LC", "TV+"):
            out.append(lead + (u if u != "LC" else "LC") + trail)
            continue
        if lead or trail:
            out.append(lead + (u if (u in ACRO or re.fullmatch(r"[KW][A-Z]{3}", u)) else w.capitalize()) + trail)
            continue
        if u in SPECIAL:
            out.append(SPECIAL[u])
        elif u in ACRO:
            out.append(u)
        elif re.fullmatch(r"\(?[A-Z]{3,5}\)?", u) and u.strip("()") in ACRO:
            out.append(u)
        else:
            out.append(w.capitalize() if not any(ch.isdigit() for ch in w) else w.upper() if w.upper() in ACRO else w.capitalize())
    s = " ".join(out)
    return re.sub(r"\bSky Sports\+", "Sky Sports+", s)


def core(ch):
    """Return (display name, quality flags)"""
    prefix = PREFIX.match(ch)
    s = PREFIX.sub("", ch).strip()
    flags = set(m.upper() for m in re.findall(JUNK, s, flags=re.I) if m)
    flags |= {"HD" for _ in [0] if "(H)" in s}
    s = re.sub(JUNK, " ", s, flags=re.I)
    s = s.upper()
    for pat, rep in FIXES:
        s = re.sub(pat, rep, s)
    s = re.sub(r"\[([^\]]*)\]", r"(\1)", s)
    s = re.sub(r"\s+", " ", s).strip(" -/")
    return titled(s), flags, (prefix.group(1).upper() if prefix else "")


def new_category(cat, ch, name):
    c = cat.upper()
    n = ch.upper()
    if c.startswith("UK|"):
        return "UK Sports"
    if c.startswith("WT|"):
        return "Cricket"
    if "9NOW" in c:
        return "AU Free-to-Air"
    if "PRIME" in c:
        return "AU Sports" if "BEIN" in n else "AU Free-to-Air"
    if "AUSTRALIA" in c:
        return "AU Sports" if re.search(r"BEIN|ESPN|FOX SPORTS|FOX CRICKET", n) else "AU Free-to-Air"
    if "HBO" in c:
        return "US HBO Max"
    if "PEACOCK NETWORK" in c:
        return "US Peacock"
    if re.search(r"^US\| (ABC|CBS|FOX|NBC)", c):
        return "US Sports" if re.search(r"SPORTS NETWORK|FOX SPORTS|NBC GOLF", n) else "US Locals"
    if "PEACOCK RAW" in c:
        return "US Sports" if re.search(r"NFL|NBC SPORTS|PREMIER LEAGUE|WWE|TEAM USA", n) else "US Entertainment"
    if c.startswith("US|") and "TENNIS" in n and "SKY" not in n:
        return "Tennis"
    if "NFL PPV" in c or "NHL PPV" in c:
        return "US Sports"
    if "TV RAW" in c:
        return "US Sports"
    if "SPORT HD/RAW" in c:
        return "US Shopping" if re.search(r"\bHSN\b|SHOP LC", n) else "US Sports"
    return cat


def rank(flags):
    """lower = better. Plain/HD first, then hevc, raw, 4K."""
    if flags & {"UHD", "4K", "3840P"}:
        return 3
    if "RAW" in flags and "HD" not in flags:
        return 2
    if "HEVC" in flags:
        return 2
    if "RAW" in flags:
        return 1
    return 0


recs = []
for r in rows:
    cat, ch = r["Category"], r["Channel"]
    name, flags, pre = core(ch)
    ncat = new_category(cat, ch, name)
    # keep the country tag only where the category doesn't already say it
    if ncat == "Cricket" and pre in ("UK", "AU", "US", "NZ", "SG", "CRB") and not re.search(r"\((UK|AU|US|NZ|SG|CRB)\)$", name):
        name = f"{name} ({pre})" if pre != "CRB" else f"{name} (Caribbean)"
    recs.append({**r, "NewCategory": ncat, "NewName": name, "_flags": flags, "_rank": rank(flags), "_pre": pre})

# hide lower-quality duplicates of the same channel inside one new category
best = {}
for i, r in enumerate(recs):
    k = (r["NewCategory"], r["NewName"].lower())
    if k not in best or (r["_rank"], i) < (recs[best[k]]["_rank"], best[k]):
        best[k] = i
for i, r in enumerate(recs):
    k = (r["NewCategory"], r["NewName"].lower())
    r["Action"] = "keep" if best[k] == i else "hide"
    if not r["Category"].startswith("WT|") and "CRICKET" in r["Channel"].upper():  # all cricket lives in the Cricket category
        r["Action"], r["Reason"] = "hide", "listed in Cricket"
    if r["NewName"] == "NFL RedZone" and r["Action"] == "hide":  # keep the RAW feeds as a backup to the HD one
        r["Action"], r["NewName"] = "keep", "NFL RedZone (RAW)"
    r["Reason"] = "" if r["Action"] == "keep" else f"duplicate of: {recs[best[k]]['Channel']}"

with open(HERE / "clean_mapping.csv", "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.writer(fh)
    w.writerow(["Category", "Channel", "NewCategory", "NewName", "Action", "Reason"])
    for r in recs:
        w.writerow([r["Category"], r["Channel"], r["NewCategory"], r["NewName"], r["Action"], r["Reason"]])

kept = [r for r in recs if r["Action"] == "keep"]
print(f"{len(recs)} channels -> {len(kept)} kept, {len(recs) - len(kept)} hidden as duplicates")
for cat in sorted({r['NewCategory'] for r in kept}):
    names = [r["NewName"] for r in kept if r["NewCategory"] == cat]
    print(f"\n== {cat} ({len(names)})\n" + " | ".join(names))
