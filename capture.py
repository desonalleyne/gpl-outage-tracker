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
import urllib.error
import urllib.parse
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
# Region-level names. These go in "region", never in "areas".
REGIONS = ["Georgetown", "East Bank Demerara", "East Coast Demerara", "West Demerara", "West Bank Demerara",
           "West Coast Demerara", "Essequibo Coast", "Essequibo Islands", "East Berbice", "West Berbice",
           "Berbice", "Corentyne", "Essequibo", "East Bank", "East Coast", "West Bank", "West Coast", "Demerara"]
REGION_SET = {r.lower() for r in REGIONS}
REGION_RE = re.compile(r"\b(" + "|".join(re.escape(r) for r in sorted(REGIONS, key=len, reverse=True)) + r")\b", re.I)
WHEN = re.compile(r"\b\d{1,2}[:.]?\d{2}\s*(?:hrs|hours|h\b|am|pm|a\.m\.|p\.m\.)|\b(?:mon|tue|wednes|thurs|fri|satur|sun)day\b|"
                  r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.? \d{1,2}\b|\b\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)|"
                  r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b|\btoday\b|\btomorrow\b|\btonight\b|\bthis (?:morning|afternoon|evening)\b", re.I)
RULES_VERSION = "2026-11-19a"   # bump when the rules change; every post records which version labelled it
# A post has to talk about power actually being off to count as an outage.
# Stops things like the smart-meter announcement ("installation", "upgrade") counting as planned outages.
OUTAGE = re.compile(r"interruption|outage|without (?:power|electricity|service|supply)|offline|\btrip|shut ?down|"
                    r"load.?shedding|power cut|de-?energi|disrupt|(?:switched|turned) off|no (?:power|electricity)|blackout", re.I)
NAME = r"[A-Z][\w'\-]*(?:\s(?:of|en|de|on)?\s?[A-Z][\w'\-]*)*"   # capitalised words only, e.g. "Garden of Eden"
BETWEEN = re.compile(r"\bbetween (" + NAME + r") (?:and|to) (" + NAME + r")")
BULLET = re.compile(r"^\s*[-•*]\s+(.+)$", re.M)
INLIST = re.compile(r"\b(?:customers|areas) in (" + NAME + r"(?:(?:,\s*|\s+and\s+)" + NAME + r")+)")
RANGE = re.compile(r"\bfrom (" + NAME + r") to (" + NAME + r")")
SHEDDING = re.compile(r"load.?shedding|rotation(?:al)? basis|rotating outages?", re.I)
OFFLINE = re.compile(r"\b(trip(?:ped|s)?|(?:went|gone|going|goes|is|taken|come|came|coming|comes) offline|de-?energi[sz]ed|out of service)\b", re.I)

# Stated reason for an outage, checked in this order; the first match wins.
CAUSES = [
    ("System disturbance", r"system disturbance|disturbance (?:on|in) the system|system fault|grid disturbance"),
    ("Third-party contact", r"crane|excavator|vehicle|truck|contractor|heavy.?(?:duty )?equipment|kite|third.?party|vandal|sabotage|theft|struck (?:a|the) pole|contact with (?:the|our) (?:network|lines?)|threat"),
    ("Trees & vegetation", r"\btrees?\b|branch|vegetation|bush"),
    ("Weather", r"lightning|storm|heavy rain|high winds?|weather|flood"),
    ("High demand / overload", r"overload|high demand|peak demand|increased demand|demand"),
    ("Generation shortfall", r"generat(?:ion|ing|or)|\bunits?\b|\bengines?\b|power plant|capacity|megawatts?|\bMW\b|fuel"),
    ("Equipment failure", r"transformer|cable|insulator|breaker|pothead|\blugs?\b|conductor|\bpoles?\b|battery|switch|relay|recloser|lines? (?:came )?down|downed lines?|broken|burnt|leak"),
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
    if not OUTAGE.search(text):
        return "other"
    if PLANNED.search(text):
        # A planned outage has to say when. An announcement about upgrades or
        # installations with no date or time is just a notice.
        return "planned" if WHEN.search(text) else "other"
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
    # Areas come from the body only (the title just repeats the region).
    # "from Bounty Hall to Charity" is kept as one stretch, since it covers the villages in between.
    body = post.get("text", "")
    canon = {a.lower(): a for a in AREAS}
    areas, taken = [], []
    for m in RANGE.finditer(body):
        stretch = f"{m.group(1).strip()} to {m.group(2).strip()}"
        if stretch not in areas:
            areas.append(stretch)
        taken.append(m.span())
    # "between Canefield and Williamsburg" / "between Canefield to Kortberaadt"
    for m in BETWEEN.finditer(body):
        stretch = f"{m.group(1).strip()} to {m.group(2).strip()}"
        if stretch not in areas:
            areas.append(stretch)
        taken.append(m.span())
    # bullet lists: "- Canje to Auchlyne"
    for m in BULLET.finditer(body):
        item = m.group(1).strip().rstrip(".;,")
        if 2 < len(item) <= 60 and item not in areas:
            areas.append(item)
        taken.append(m.span())
    # "customers in Albert Street, Robb Street, Alberttown, Bourda and Cummingsburg that ..."
    for m in INLIST.finditer(body):
        for item in re.split(r",\s*|\s+and\s+", m.group(1)):
            item = item.strip()
            if item and item[0].isupper() and item.lower() not in REGION_SET and item not in areas:
                areas.append(item)
        taken.append(m.span())
    for m in AREA_RE.finditer(body):
        if any(a <= m.start() < b for a, b in taken) or m.group(1).lower() in REGION_SET:
            continue
        name = canon[m.group(1).lower()]
        if name not in areas:
            areas.append(name)
    region = None
    for inside in REGION.findall(post.get("title", "")):   # skip "(GPL)"
        if inside.strip().upper() in ("GPL", "GPL INC", "INC"):
            continue
        hit = REGION_RE.search(inside)
        region = REGIONS[[r.lower() for r in REGIONS].index(hit.group(1).lower())] if hit else inside.strip().title()
        break
    if not region:
        hit = REGION_RE.search(body)
        if hit:
            region = REGIONS[[r.lower() for r in REGIONS].index(hit.group(1).lower())]
    times = [f"{int(h):02d}:{mm} {u.lower()}" for h, mm, u in TIME_RE.findall(text)]
    t = classify(text, post.get("title", ""))
    return {"type": t, "region": region, "rules": RULES_VERSION,
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


GONE = re.compile(r"no longer available|content isn.t available|page isn.t available|"
                  r"link you followed may be broken|may have been removed|only shared it with a small group", re.I)
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
      "Accept-Language": "en-US,en;q=0.9"}


def fetch(url):
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read(600_000).decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, e.read(200_000).decode("utf-8", "ignore")


def check_links(archive, days=7, limit=25):
    """Ask Facebook whether recent posts still exist, and mark the ones that are gone.

    The feed alone can't tell us this: rss.app keeps serving posts it already saw,
    even after GPL deletes them. So each run looks the post up through Facebook's
    embed page, which works without logging in and says "no longer available" for
    deleted posts. If that's inconclusive it tries the plain mobile page.
    A post is only marked deleted when Facebook clearly says so. What came back is
    kept in "link_check" so you can see why.
    """
    now = datetime.now(timezone.utc)
    todo = []
    for p in archive:
        if p.get("deleted_from_source") or not p.get("link"):
            continue
        try:
            age = (now - datetime.fromisoformat(p.get("published") or p["captured_at"])).days
        except Exception:
            continue
        if age <= days:
            todo.append(p)
    for p in todo[:limit]:
        www = p["link"].replace("://m.facebook.com", "://www.facebook.com")
        tries = [("embed", "https://www.facebook.com/plugins/post.php?href=" + urllib.parse.quote(www, safe="")),
                 ("mobile", www.replace("://www.", "://mbasic."))]
        notes = []
        for name, url in tries:
            try:
                status, page = fetch(url)
            except Exception as e:
                notes.append(f"{name}: error {type(e).__name__}")
                continue
            if GONE.search(page) or status in (404, 410):
                p["deleted_from_source"] = True
                p["deleted_seen_at"] = now.isoformat()
                notes.append(f"{name}: gone ({status})")
                break
            snippet = re.sub(r"\W+", " ", (p.get("text") or "")[:60]).strip().split(" ")[:5]
            plain = re.sub(r"\W+", " ", html.unescape(page))
            if snippet and " ".join(snippet) in plain:
                notes.append(f"{name}: live ({status})")
                break
            notes.append(f"{name}: unclear ({status}, {'login page' if 'login' in page.lower() else 'no post text'})")
        p["link_check"] = "; ".join(notes)


FB_PAGE = os.environ.get("FB_PAGE", "GuyanaPowerAndLight")
PLUGIN = ("https://www.facebook.com/plugins/page.php?href=https%3A%2F%2Fwww.facebook.com%2F{page}"
          "&tabs=timeline&width=500&height=5000&small_header=true&hide_cover=true")


def parse_facebook(page: str):
    """Pull posts out of Facebook's embeddable page widget.

    The widget's HTML carries the posts as JSON objects like
    {"message": "...", "createdTime": 1795140786, ...}. Each one is decoded as it's found.
    """
    dec = json.JSONDecoder()
    posts, seen = [], set()
    for m in re.finditer(r'"message":', page):
        obj = None
        # walk back to the "{" that opens this post's object
        for start in range(m.start() - 1, max(-1, m.start() - 3000), -1):
            if page[start] != "{":
                continue
            try:
                cand, _ = dec.raw_decode(page, start)
            except ValueError:
                continue
            if isinstance(cand, dict) and "message" in cand and "createdTime" in cand:
                obj = cand
                break
        if not obj:
            continue
        msg, ts = obj.get("message") or "", obj.get("createdTime")
        if not msg or not isinstance(ts, (int, float)) or ts in seen:
            continue
        seen.add(ts)
        link = next((obj[k] for k in ("url", "permalink", "permalinkURL", "postURL", "link")
                     if isinstance(obj.get(k), str)), None)
        first = msg.strip().split("\n", 1)[0].strip()
        posts.append({
            "id": f"fb:{FB_PAGE}:{int(ts)}",
            "fb_time": int(ts),
            "link": link or f"https://www.facebook.com/{FB_PAGE}",
            "title": first[:200],
            "published": datetime.fromtimestamp(ts, timezone.utc).isoformat(),
            "text": msg.strip(),
            "images": [u for u in [obj.get("photoURL")] + (obj.get("albumPhotoURLs") or []) if u],
            "reactions": obj.get("reactionCount"), "comments": obj.get("commentCount"),
            "shares": obj.get("shareCount"),
        })
    return posts


def fetch_items():
    """Facebook widget by default; set FEED_URL to use an RSS feed instead."""
    url = os.environ.get("FEED_URL")
    if url:
        req = urllib.request.Request(url, headers={"User-Agent": "gpl-outage-tracker/1.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return "rss", parse_feed(r.read())
    status, page = fetch(PLUGIN.format(page=FB_PAGE))
    items = parse_facebook(page)
    if not items:
        (ROOT / "last_fetch.html").write_text(page)
        sys.exit(f"No posts found on the Facebook widget (HTTP {status}). Saved the page to last_fetch.html.")
    return "facebook", items


def norm(t):
    return re.sub(r"\W+", " ", (t or "").lower()).strip()[:80]


def main():
    source, items = fetch_items()

    archive = json.loads(POSTS.read_text()) if POSTS.exists() else []
    seen = {p["id"]: p for p in archive}
    now = datetime.now(timezone.utc).isoformat()
    new = 0
    # Posts saved from rss.app have different ids. Match them up by text and time
    # so the same post isn't saved twice.
    def ptime(p):
        try:
            return datetime.fromisoformat(p.get("published") or p["captured_at"]).timestamp()
        except Exception:
            return None
    for p in items:
        if p["id"] in seen or source != "facebook":
            continue
        for old in archive:
            if "fb_time" in old or norm(old.get("text")) != norm(p["text"]):
                continue
            t = ptime(old)
            if t is None or abs(t - p["fb_time"]) <= 6 * 3600:
                seen.pop(old["id"], None)
                old["rss_id"] = old["id"]
                old.update({k: p[k] for k in ("id", "fb_time", "published", "title")})
                if "/posts/" not in (old.get("link") or ""):
                    old["link"] = p["link"]
                seen[old["id"]] = old
                break
    for p in items:
        if p["id"] in seen:
            old = seen[p["id"]]
            # Keep the longest text ever seen in case a post is edited down later.
            if len(p["text"]) > len(old.get("text", "")):
                old["text"] = p["text"]
            for k in ("reactions", "comments", "shares"):
                if p.get(k) is not None:
                    old[k] = p[k]
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

    # A post that's missing from the page while older posts are still showing was deleted.
    # It has to be missing on two runs in a row, so one bad fetch can't mark anything.
    # Nothing is ever removed from the archive.
    current = {p["id"] for p in items}
    dates = [p["published"] for p in items if p.get("published")]
    oldest = min(dates) if dates else None
    if oldest and len(items) >= 3:
        for p in archive:
            if (p.get("published") or "") <= oldest:
                continue
            if p["id"] in current:
                p.pop("missing_runs", None)
                if p.get("deleted_from_source") and source == "facebook":
                    p["deleted_from_source"] = False          # it came back
                continue
            p["missing_runs"] = p.get("missing_runs", 0) + 1
            if p["missing_runs"] >= 2 and not p.get("deleted_from_source"):
                p["deleted_from_source"] = True
                p["deleted_seen_at"] = now

    if source == "rss":
        check_links(archive)

    archive.sort(key=lambda p: p.get("published") or p.get("captured_at") or "", reverse=True)
    POSTS.parent.mkdir(parents=True, exist_ok=True)
    POSTS.write_text(json.dumps(archive, indent=1, ensure_ascii=False))
    gone = sum(1 for p in archive if p.get("deleted_from_source"))
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M')}  source: {source}  posts on page: {len(items)}  "
          f"new: {new}  deleted so far: {gone}  archive total: {len(archive)}")


if __name__ == "__main__":
    main()
