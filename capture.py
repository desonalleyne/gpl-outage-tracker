#!/usr/bin/env python3
"""Capture GPL Facebook posts from an RSS bridge feed before they get deleted.

- Reads FEED_URL (RSS 2.0 or Atom) from the environment.
- Appends any post not seen before to data/posts.json. Nothing is ever removed,
  so posts survive after GPL deletes them from Facebook.
- Parses each post into structured fields (type, feeders, areas, times) used by the dashboard.

Standard library only, so the GitHub Action needs no installs.
"""
import html
import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
POSTS = ROOT / "data" / "posts.json"
# Manual fixes that survive every run: {"<post id>": {"areas": [...], "cause": "...", ...}}
OVERRIDES = ROOT / "corrections.json"

# Areas commonly named in GPL notices. Add to this list as new names show up.
AREAS = [
    "Georgetown", "Kitty", "Campbellville", "Sophia", "Turkeyen", "Liliendaal", "Bel Air",
    "Prashad Nagar", "Lamaha", "Albouystown", "Ruimveldt", "Festival City", "Kingston",
    "Alberttown", "Queenstown", "Lodge", "Meadow Brook", "Tucville", "Guyhoc", "Cummings Lodge",
    "East Bank Demerara", "East Bank", "Providence", "Eccles", "Diamond", "Grove", "Herstelling",
    "Covent Garden", "Agricola", "Houston", "Peter's Hall", "Soesdyke", "Timehri", "Linden",
    "East Coast Demerara", "East Coast", "Ogle", "Better Hope", "Lusignan", "Mon Repos",
    "Enmore", "Buxton", "Haslington", "Cove and John", "Mahaica", "Mahaicony", "Golden Grove",
    "West Demerara", "West Coast Demerara", "West Bank Demerara", "Vreed-en-Hoop", "Leonora",
    "Parika", "Wales", "Canal", "La Grange", "Uitvlugt", "Edinburgh", "Garden of Eden",
    "Berbice", "New Amsterdam", "Rose Hall", "Corriverton", "Skeldon", "Canje", "Fort Wellington",
    "Blairmont", "Essequibo", "Anna Regina", "Charity", "Wakenaam", "Leguan", "Bartica",
    # Seen in real GPL posts
    "Bounty Hall", "Best", "Vriesland", "Sarah Johanna", "Long Creek", "Nandy Park", "Coverden",
    "Farm", "Lamaha Park", "North Ruimveldt", "South Ruimveldt", "Mocha", "Essequibo Coast",
]
AREA_RE = re.compile(r"\b(" + "|".join(re.escape(a) for a in sorted(AREAS, key=len, reverse=True)) + r")\b", re.I)
FEEDER_RE = re.compile(r"\b(?:([A-Z][A-Za-z\-]+(?:\s(?:of|en|de|and)?\s?[A-Z][A-Za-z\-]+){0,2})\s+)?(?:Feeder\s*#?\s*|F)(\d{1,2})\b")
TIME_RE = re.compile(r"\b(\d{1,2})[:.]?(\d{2})\s*(hrs|hours|h\b|am|pm|a\.m\.|p\.m\.)", re.I)

PLANNED = re.compile(r"\b(planned|scheduled|will be (?:conducting|carrying out|undertaking)|maintenance|upgrade works?|installation)\b", re.I)
UNPLANNED = re.compile(r"\b(unplanned|trip(?:ped)?|fault|emergency|shutdown|shut down|load.?shedding|interruption|outage (?:is|was) experienced|currently experiencing)\b", re.I)
RESTORED = re.compile(r"\b(restored|repowered|re-?energi[sz]ed|reclosed|service restoration|power has (?:been )?(?:returned|restored)|has returned)\b", re.I)
UPDATE = re.compile(r"^\s*update\b", re.I)   # follow-up to an outage already posted
REGION = re.compile(r"\(([^)]{3,40})\)")       # GPL titles end with the region, e.g. "(WEST DEMERARA)"
NAME = r"[A-Z][\w'\-]*(?:\s(?:of|en|de|on)?\s?[A-Z][\w'\-]*)*"   # capitalised words only, e.g. "Garden of Eden"
RANGE = re.compile(r"\bfrom (" + NAME + r") to (" + NAME + r")")
SHEDDING = re.compile(r"load.?shedding|rotation(?:al)? basis|rotating outages?", re.I)
OFFLINE = re.compile(r"\b(trip(?:ped|s)?|went offline|gone offline|going offline|goes offline|is offline|taken offline|de-?energi[sz]ed|out of service)\b", re.I)

# Stated reason for an outage, checked in this order; the first match wins.
CAUSES = [
    ("Third-party contact", r"crane|excavator|vehicle|truck|contractor|heavy.?(?:duty )?equipment|kite|third.?party|vandal|sabotage|theft|struck (?:a|the) pole|contact with (?:the|our) (?:network|lines?)|threat"),
    ("Trees & vegetation", r"\btrees?\b|branch|vegetation|bush"),
    ("Weather", r"lightning|storm|heavy rain|high winds?|weather|flood"),
    ("High demand / overload", r"overload|high demand|peak demand|increased demand|demand"),
    ("Generation shortfall", r"generat(?:ion|ing|or)|\bunits?\b|\bengines?\b|power plant|capacity|megawatts?|\bMW\b|fuel"),
    ("Equipment failure", r"transformer|cable|insulator|breaker|pothead|\blugs?\b|conductor|\bpoles?\b|substation|battery|switch|relay|recloser|lines? (?:came )?down|downed lines?|broken|burnt|leak"),
    ("System shutdown", r"total shutdown|system (?:shutdown|collapse)|frequency|blackout across"),
    ("Planned works", r"maintenance|upgrade|installation|scheduled works?|planned works?|network works?|construction|relocat"),
]
CAUSES = [(name, re.compile(rx, re.I)) for name, rx in CAUSES]


def strip_html(s: str) -> str:
    s = re.sub(r"<br\s*/?>|</p>", "\n", s or "", flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"[ \t]+", " ", html.unescape(s)).strip()


def images(s: str):
    return re.findall(r'<img[^>]+src="([^"]+)"', s or "", flags=re.I)


def to_iso(s: str):
    if not s:
        return None
    try:
        return parsedate_to_datetime(s).astimezone(timezone.utc).isoformat()
    except Exception:
        pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
    except Exception:
        return s


def parse_feed(xml_bytes: bytes):
    root = ET.fromstring(xml_bytes)
    ns = {"a": "http://www.w3.org/2005/Atom", "c": "http://purl.org/rss/1.0/modules/content/"}
    out = []
    for it in root.iter("item"):  # RSS 2.0
        raw = (it.findtext("c:encoded", namespaces=ns) or it.findtext("description") or "")
        out.append({
            "id": it.findtext("guid") or it.findtext("link"),
            "link": it.findtext("link"),
            "title": strip_html(it.findtext("title") or ""),
            "published": to_iso(it.findtext("pubDate")),
            "text": strip_html(raw),
            "images": images(raw),
        })
    for e in root.iter("{http://www.w3.org/2005/Atom}entry"):  # Atom
        link = e.find("a:link", ns)
        raw = e.findtext("a:content", namespaces=ns) or e.findtext("a:summary", namespaces=ns) or ""
        out.append({
            "id": e.findtext("a:id", namespaces=ns) or (link.get("href") if link is not None else None),
            "link": link.get("href") if link is not None else None,
            "title": strip_html(e.findtext("a:title", namespaces=ns) or ""),
            "published": to_iso(e.findtext("a:published", namespaces=ns) or e.findtext("a:updated", namespaces=ns)),
            "text": strip_html(raw),
            "images": images(raw),
        })
    return [p for p in out if p["id"]]


def classify(text: str, title: str = ""):
    if RESTORED.search(title) or (RESTORED.search(text) and not PLANNED.search(text) and not OFFLINE.search(text)):
        return "restored"
    if UPDATE.search(title):
        return "update"
    if RESTORED.search(text) and not PLANNED.search(text):
        return "restored"
    if SHEDDING.search(text):
        return "load-shedding"
    if PLANNED.search(text):
        return "planned"
    if UNPLANNED.search(text) or OFFLINE.search(text):
        return "unplanned"
    return "other"


def cause(text: str, kind: str):
    order = sorted(CAUSES, key=lambda c: c[0] != "Planned works") if kind == "planned" else CAUSES
    for name, rx in order:
        if rx.search(text):
            return name
    return "No reason given"


def extract(post):
    text = f"{post.get('title','')}\n{post.get('text','')}"
    feeders = sorted({(f"{m.group(1)} F{int(m.group(2))}" if m.group(1) else f"F{int(m.group(2))}")
                      for m in FEEDER_RE.finditer(text)})
    canon = {a.lower(): a for a in AREAS}
    areas = {canon[m.group(1).lower()] for m in AREA_RE.finditer(text)}
    for a, b in RANGE.findall(post.get("text", "")):   # "from Best to Vriesland"
        areas.update({a.strip(), b.strip()})
    areas = sorted(areas)
    rm = REGION.search(post.get("title", ""))
    region = rm.group(1).strip().title() if rm else None
    times = [f"{int(h):02d}:{mm} {u.lower()}" for h, mm, u in TIME_RE.findall(text)]
    t = classify(text, post.get("title", ""))
    return {"type": t, "region": region,
            "cause": cause(text, t) if t in ("unplanned", "planned", "load-shedding") else None,
            "feeder_offline": bool(OFFLINE.search(text)),
            "feeders": feeders, "areas": areas, "times": times}


def flag_suspected_shedding(archive):
    """Guess which 'feeder offline' posts are really load shedding.

    A post is suspected when GPL calls it unplanned or just says a feeder went
    offline, gives no reason, and at least one of these is true:
      - peak  : posted between 17:00 and 22:59 Guyana time, when demand is highest
      - rotation: another reasonless outage post for DIFFERENT feeders/areas on the
                  same grid within 3 hours (feeders being cycled off in turn)
    The reasons it was flagged are stored so you can check whether the guess is right.
    """
    def ts(p):
        try:
            return datetime.fromisoformat(p.get("published") or p["captured_at"])
        except Exception:
            return None
    def where(p):   # what went off: feeder numbers if given, else the areas, else the region
        return frozenset(p.get("feeders") or p.get("areas") or ([p["region"]] if p.get("region") else []))
    def grid(p):    # Essequibo, Berbice islands etc. are separate from the main Demerara-Berbice grid
        r = (p.get("region") or "") + " " + " ".join(p.get("areas") or [])
        return "isolated" if re.search(r"Essequibo|Anna Regina|Charity|Bartica|Wakenaam|Leguan|Lethem|Mahdia", r, re.I) else "DBIS"
    cand = [p for p in archive if p.get("type") == "unplanned" and p.get("cause") == "No reason given" and ts(p)]
    for p in archive:
        p["suspected_shedding"] = []
    for p in cand:
        t = ts(p)
        why = []
        if 17 <= (t.astimezone(timezone.utc).hour - 4) % 24 <= 22:
            why.append("peak hours")
        mine = where(p)
        for q in cand:
            if q is p:
                continue
            if (grid(q) == grid(p) and abs((ts(q) - t).total_seconds()) <= 3 * 3600
                    and where(q) and mine and where(q) != mine):
                why.append("rotation")
                break
        p["suspected_shedding"] = why


def main():
    url = os.environ.get("FEED_URL")
    if not url:
        sys.exit("Set FEED_URL (a repository secret) to your RSS bridge feed URL.")
    req = urllib.request.Request(url, headers={"User-Agent": "gpl-outage-tracker/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        items = parse_feed(r.read())

    archive = json.loads(POSTS.read_text()) if POSTS.exists() else []
    seen = {p["id"]: p for p in archive}
    now = datetime.now(timezone.utc).isoformat()
    new = 0
    for p in items:
        if p["id"] in seen:
            old = seen[p["id"]]
            # Keep the longest text ever seen in case a post is edited down later.
            if len(p["text"]) > len(old.get("text", "")):
                old["text"] = p["text"]
            continue
        p["captured_at"] = now
        archive.append(p)
        seen[p["id"]] = p
        new += 1

    fixes = json.loads(OVERRIDES.read_text()) if OVERRIDES.exists() else {}
    for p in archive:
        p.update(extract(p))
        p.update(fixes.get(p["id"], {}))
    flag_suspected_shedding(archive)

    # Posts no longer in the feed were probably deleted from Facebook. Mark, never remove.
    # Only judge posts newer than the oldest feed item; older ones just scrolled out of the feed.
    current = {p["id"] for p in items}
    dates = [p["published"] for p in items if p.get("published")]
    oldest = min(dates) if dates else None
    for p in archive:
        if oldest and (p.get("published") or "") > oldest and p["id"] not in current:
            p["deleted_from_source"] = True

    archive.sort(key=lambda p: p.get("published") or p.get("captured_at") or "", reverse=True)
    POSTS.parent.mkdir(parents=True, exist_ok=True)
    POSTS.write_text(json.dumps(archive, indent=1, ensure_ascii=False))
    print(f"feed items: {len(items)}  new: {new}  archive total: {len(archive)}")


if __name__ == "__main__":
    main()
