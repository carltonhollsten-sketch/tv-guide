"""Builds the merged guide (site/picked_epg.xml) from scratch; runs on GitHub Actions or on the laptop.

Needs IPTV_USER / IPTV_PASS / PICKED_CHANNELS in the environment (GitHub secrets). Downloads the public guide sources and the provider's
own guide, works out each channel's provider guide id from picked_channels.csv (matched by name, like make_playlist.ps1),
then runs build_epg.py. Fails (so nothing gets published) if the result looks broken.
"""
import csv
import gzip
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

HOST = "http://tv.business-cloud-8.ru"
SRC = Path("src")
SITE = Path("site")
SRC.mkdir(exist_ok=True)
SITE.mkdir(exist_ok=True)
user, pw = os.environ["IPTV_USER"], os.environ["IPTV_PASS"]

SOURCES = {f"{n.lower()}.xml.gz": f"https://epgshare01.online/epgshare01/epg_ripper_{n}.xml.gz"
           for n in ["US2", "UK1", "AU1", "US_SPORTS1", "US_LOCALS1", "PLEX1", "FANDUEL1", "CA2", "IE1", "NZ1", "HK1", "IN1",
                     "SG1", "MY1", "ZA1", "DIRECTVSPORTS1"]}
SOURCES["mjh_au_all.xml.gz"] = "https://i.mjh.nz/au/all/epg.xml.gz"
for c in ["Sydney", "Melbourne", "Brisbane", "Perth", "Adelaide"]:
    SOURCES[f"mjh_{c.lower()}.xml.gz"] = f"https://i.mjh.nz/au/{c}/epg.xml.gz"


def fetch(url, dest, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=300) as r, open(dest, "wb") as f:
                shutil.copyfileobj(r, f)
            return True
        except Exception as e:
            print(f"  retry {i + 1} {dest.name}: {type(e).__name__}", flush=True)
    return False


def norm(s):
    s = unicodedata.normalize("NFKD", s)
    s = re.sub(r"[^\x20-\x7E]", "", s)
    return re.sub(r"\s+", " ", s).strip()


for name, url in SOURCES.items():
    ok = fetch(url, SRC / name)
    print(f"{name}: {'ok' if ok else 'FAILED (skipped)'}", flush=True)
    if not ok:
        (SRC / name).unlink(missing_ok=True)

q = f"username={urllib.parse.quote(user)}&password={urllib.parse.quote(pw)}"
if not fetch(f"{HOST}/xmltv.php?{q}", SRC / "provider.xml"):
    sys.exit("provider guide download failed")


def api(action):
    with urllib.request.urlopen(f"{HOST}/player_api.php?{q}&action={action}", timeout=300) as r:
        return json.load(r)


cats = {str(c["category_id"]): norm(c["category_name"]) for c in api("get_live_categories")}
streams = api("get_live_streams")
# the channel list is a repository secret (PICKED_CHANNELS: CSV with Category,Channel), not a file in this public repo
import io
want = {f'{r["Category"]}\t{norm(r["Channel"])}' for r in csv.DictReader(io.StringIO(os.environ["PICKED_CHANNELS"].lstrip("﻿")))}

text = (SRC / "provider.xml").read_text(encoding="utf-8", errors="ignore")
progs = Counter(re.findall(r'<programme [^>]*?channel="([^"]*)"', text))
ids = set(re.findall(r'<channel id="([^"]*)"', text))
del text

rows, used = [], set()
for s in streams:
    k = f'{cats.get(str(s["category_id"]), "")}\t{norm(s["name"])}'
    if k in want and k not in used:
        used.add(k)
        e = s.get("epg_channel_id") or ""
        rows.append({"Cat": cats[str(s["category_id"])], "Name": s["name"], "EpgId": e, "HasId": str(bool(e)),
                     "InXml": str(e in ids), "Progs": str(progs.get(e, 0))})
rows.sort(key=lambda r: (r["Cat"], r["Name"]))
print(f"matched {len(rows)} of {len(want)} channels", flush=True)
if len(rows) < len(want) * 0.95:
    sys.exit("too few channels matched; not publishing")
with open("epg_coverage.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=["Cat", "Name", "EpgId", "HasId", "InXml", "Progs"], quoting=csv.QUOTE_ALL)
    w.writeheader()
    w.writerows(rows)

out = subprocess.run([sys.executable, "-X", "utf8", "build_epg.py", str(SRC)], capture_output=True, text=True)
print(out.stdout[:1500])
if out.returncode:
    sys.exit(out.stderr[-1500:])
m = re.search(r"with any guide in next 48h (\d+) \((\d+)%\)", out.stdout)
if not m or int(m.group(2)) < 50:
    sys.exit("guide coverage looks too low; not publishing")
shutil.copy(SRC / "picked_epg.xml", SITE / "picked_epg.xml")
(SITE / "index.html").write_text("<!doctype html><title>guide</title><a href='picked_epg.xml'>picked_epg.xml</a>")
print("built", (SITE / "picked_epg.xml").stat().st_size // 1024, "KB")
