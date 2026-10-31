"""Fetch the newest listings from myhome.ge and ss.ge and normalise them.

Both sites render their search results server-side into a Next.js
__NEXT_DATA__ blob, so one HTML request per page gives structured data.
"""
import json
import re
import time
import urllib.request
from datetime import datetime, timedelta, timezone

TBILISI = timezone(timedelta(hours=4))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
NEXT_RE = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)

# (deal, kind) -> site specific query parts. deal: sale|rent, kind: flat|commercial
MYHOME_CATS = {
    ("sale", "flat"): "deal_types=1&real_estate_types=1",
    ("rent", "flat"): "deal_types=2&real_estate_types=1",
    ("sale", "commercial"): "deal_types=1&real_estate_types=5",
    ("rent", "commercial"): "deal_types=2&real_estate_types=5",
}
SS_CATS = {
    ("sale", "flat"): "bina/iyideba",
    ("rent", "flat"): "bina/qiravdeba",
    ("sale", "commercial"): "komerciuli/iyideba",
    ("rent", "commercial"): "komerciuli/qiravdeba",
}
MYHOME_OWNER = {"physical": "owner", "agent": "agent", "broker": "agent",
                "premier_agent": "agent", "developer": "developer"}


def _next_data(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ka"})
    with urllib.request.urlopen(req, timeout=30) as r:
        html = r.read().decode("utf8")
    m = NEXT_RE.search(html)
    if not m:
        raise RuntimeError(f"no __NEXT_DATA__ in {url}")
    return json.loads(m.group(1))["props"]["pageProps"]


def _num(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _int(v):
    n = _num(v)
    return int(n) if n is not None else None


def fetch_myhome(deal, kind, page=1):
    url = f"https://www.myhome.ge/udzravi-qoneba/?cities=1&sort=6&page={page}&{MYHOME_CATS[(deal, kind)]}"
    pp = _next_data(url)
    rows = []
    for q in pp["dehydratedState"]["queries"]:
        if q["queryKey"][0] == "statements":
            rows = q["state"]["data"]["data"]["data"]
    out = []
    for x in rows:
        price = x.get("price") or {}
        img = next((i["thumb"] for i in x.get("images") or [] if i.get("is_main")), None)
        if not img and x.get("images"):
            img = x["images"][0]["thumb"]
        out.append({
            "source": "myhome", "id": int(x["id"]),
            "url": f"https://www.myhome.ge/pr/{x['id']}/",
            "deal": deal, "kind": kind,
            "title": x.get("dynamic_title") or "",
            "urban": x.get("urban_name"), "district": x.get("district_name"),
            "address": x.get("address") or "",
            "price_usd": _num((price.get("2") or {}).get("price_total")),
            "price_gel": _num((price.get("1") or {}).get("price_total")),
            "area": _num(x.get("area")),
            "rooms": _int(x.get("room")),
            "floor": _int(x.get("floor")), "total_floors": _int(x.get("total_floors")),
            "owner": MYHOME_OWNER.get((x.get("user_type") or {}).get("type"), "unknown"),
            "image": img, "similarity": None,
            "lat": _num(x.get("lat")), "lng": _num(x.get("lng")),
            "created": None,
            "activity": _ts(x.get("last_updated")),
        })
    return out


def fetch_ss(deal, kind, page=1):
    url = f"https://home.ss.ge/ka/udzravi-qoneba/l/{SS_CATS[(deal, kind)]}?cityIdList=95&order=1&page={page}"
    pp = _next_data(url)
    rows = (pp.get("applicationList") or {}).get("realStateItemModel") or []
    out = []
    for x in rows:
        a = x.get("address") or {}
        p = x.get("price") or {}
        imgs = x.get("appImages") or []
        img = next((i["fileName"] for i in imgs if i.get("isMain")), imgs[0]["fileName"] if imgs else None)
        street = " ".join(str(s) for s in (a.get("streetTitle"), a.get("streetNumber")) if s and str(s) != "0")
        out.append({
            "source": "ss", "id": int(x["applicationId"]),
            "url": f"https://home.ss.ge/ka/udzravi-qoneba/{x['detailUrl']}",
            "deal": deal, "kind": kind,
            "title": x.get("title") or "",
            "urban": a.get("subdistrictTitle"), "district": a.get("districtTitle"),
            "address": street,
            "price_usd": _num(p.get("priceUsd")), "price_gel": _num(p.get("priceGeo")),
            "area": _num(x.get("totalArea")),
            "rooms": _rooms_from_title(x.get("title")),
            "floor": _int(x.get("floorNumber")), "total_floors": _int(x.get("totalAmountOfFloor")),
            "owner": "unknown",
            "image": img, "similarity": x.get("similarityGroup"),
            "lat": None, "lng": None,
            "created": _ts(x.get("createDate")),
            "activity": _ts(x.get("orderDate")),
        })
    return out


def _ts(value):
    """Site timestamp -> aware datetime. Naive values are Tbilisi time (UTC+4)."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(re.sub(r"(\.\d{6})\d+", r"\1", value.replace(" ", "T")))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=TBILISI)


def _rooms_from_title(title):
    m = re.search(r"(\d+)\s*ოთახ", title or "")
    return int(m.group(1)) if m else None


FETCHERS = {"myhome": fetch_myhome, "ss": fetch_ss}
# How deep we may page. myhome refuses page 6+ for anonymous visitors.
MAX_PAGES = {"myhome": 5, "ss": 25}


def fetch(source, deal, kind, page=1, retries=2):
    for attempt in range(retries + 1):
        try:
            return FETCHERS[source](deal, kind, page)
        except Exception:
            if attempt == retries:
                raise
            time.sleep(5 * (attempt + 1))
