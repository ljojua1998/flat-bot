"""Offline tests: no network, no Telegram. Run: python test_offline.py"""
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import bot as botmod
import dedup
import districts
import sources
import tracker as trackermod
from bot import Bot, parse_number, describe
from db import DB
from tracker import Tracker, matches

TB = timezone(timedelta(hours=4))
FAILS = []


def check(cond, label):
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        FAILS.append(label)


class FakeTG:
    def __init__(self):
        self.n = 0
        self.log = []
        self.blocked = set()

    def send(self, chat, text, keyboard=None, preview=True):
        if chat in self.blocked:
            raise botmod.TelegramError("Forbidden: bot was blocked by the user", 403)
        self.n += 1
        self.log.append(("send", chat, text, keyboard))
        return {"message_id": self.n}

    def edit(self, chat, mid, text, keyboard=None, preview=True):
        self.log.append(("edit", chat, text, keyboard))

    def answer(self, cid, text=None):
        if text:
            self.log.append(("toast", None, text, None))

    def sends(self, chat=None):
        return [e for e in self.log if e[0] == "send" and (chat is None or e[1] == chat)]


def listing(**kw):
    base = dict(source="ss", id=1, url="https://home.ss.ge/x", deal="sale", kind="flat",
                title="იყიდება 3 ოთახიანი ბინა საბურთალოზე", urban="საბურთალო", district=None,
                address="", price_usd=150000.0, price_gel=405000.0, area=90.0, rooms=3, floor=5,
                total_floors=12, owner="unknown", image=None, similarity=None,
                created=None, activity=None, image_hash=None)
    base.update(kw)
    return base


def test_parse_number():
    print("parse_number")
    check(parse_number("80000") == 80000, "plain number")
    check(parse_number("80 000") == 80000 and parse_number("80,000") == 80000, "spaces and commas")
    check(parse_number("80k") == 80000 and parse_number("80 ათასი") == 80000, "k / ათასი")
    check(parse_number("$80000") == 80000 and parse_number("45 მ²") == 45, "symbols around")
    check(parse_number("50000-90000") is None, "two numbers rejected")
    check(parse_number("ბლა") is None, "garbage rejected")
    check(parse_number("-1") is None and parse_number("-1", allow_negative=True) == -1, "negative only for floors")


def test_matches():
    print("filters")
    f = {"deal": "sale", "kinds": ["flat"], "urbans": ["საბურთალო"], "price_min": 100000,
         "price_max": 200000, "currency": "usd", "area_min": 50, "area_max": 120}
    check(matches(f, listing()), "in range matches")
    check(matches(f, listing(owner="agent")), "agent listings match too")
    check(not matches(f, listing(price_usd=250000)), "too expensive")
    check(not matches(f, listing(price_usd=None)), "no price excluded when price filter set")
    check(not matches(f, listing(urban="ვაკე")), "other neighbourhood")
    check(not matches(f, listing(deal="rent")), "other deal")
    check(not matches(f, listing(kind="commercial")), "other kind")
    check(not matches(f, listing(area=30)), "too small")
    g = dict(f, currency="gel", price_min=300000, price_max=500000)
    check(matches(g, listing()), "lari filter uses price_gel")
    c = dict(f, all_city=True, urbans=[])
    check(matches(c, listing(urban="ვაკე")), "whole city")
    check(not matches(None, listing()), "no filter")
    r = dict(f, rooms=[2, 5])
    check(not matches(r, listing(rooms=3)) and matches(r, listing(rooms=2)), "rooms filter")
    check(matches(r, listing(rooms=7)), "5 means 5+")
    check(matches(dict(r, kinds=["commercial"]), listing(kind="commercial", rooms=None)),
          "rooms ignored for commercial")
    fl = dict(f, floor_min=2, floor_max=None)
    check(not matches(fl, listing(floor=1)) and matches(fl, listing(floor=12)), "floor from")
    check(not matches(dict(f, floor_min=None, floor_max=0), listing(floor=1)), "floor up to 0 (basement)")
    check(not matches(fl, listing(floor=None)), "unknown floor excluded when floor filter set")


def test_dedup():
    print("dedup")
    a = listing(image_hash="0f81bececf939f5b")
    check(dedup.same_property(a, listing(id=2, image_hash="0f81bececf939f5a")), "same photo")
    check(dedup.same_property(a, listing(id=2, price_usd=145000)), "same floor/area/price, no photo")
    check(not dedup.same_property(a, listing(id=2, floor=6, image_hash="0f81bececf939f5b")),
          "same photo but different floor (developer render)")
    check(not dedup.same_property(a, listing(id=2, rooms=2)), "different rooms")
    check(not dedup.same_property(a, listing(id=2, price_usd=200000)), "attrs only, price far")
    d1, d2 = listing(owner="developer"), listing(id=2, owner="developer", price_usd=152000)
    check(not dedup.same_property(d1, d2), "developer units need a photo match")
    check(dedup.same_property(listing(similarity="g1", floor=None), listing(id=2, similarity="g1", floor=None)),
          "ss similarityGroup")


def test_bot():
    print("bot conversation")
    db = DB(os.path.join(tempfile.mkdtemp(), "t.sqlite3"))
    tg = FakeTG()
    b = Bot(db, tg, admin_ids=[1], default_max_urbans=4)
    chat = {"id": 10, "first_name": "Nino"}

    def msg(text, c=chat):
        b.handle({"message": {"chat": c, "text": text}})

    def cb(data, c=chat):
        b.handle({"callback_query": {"id": "q", "data": data, "message": {"chat": c, "message_id": 5}}})

    msg("/start")
    check(db.user(10)["state"] == "deal", "/start opens setup for new user")
    cb("deal:sale"); cb("kind:flat"); cb("ray:0")
    for name in ["ვაკე", "საბურთალო", "ვაშლიჯვარი", "ვეძისი", "ბაგები"]:
        cb(f"urb:{districts.URBAN_INDEX[name]}")
    check(len(db.user(10)["draft"]["urbans"]) == 4, "plan limit 4 enforced")
    check(any("მაქსიმუმ 4" in e[2] for e in tg.log if e[0] == "toast"), "limit toast shown")
    cb(f"urb:{districts.URBAN_INDEX['ვაკე']}")
    check("ვაკე" not in db.user(10)["draft"]["urbans"], "tap again unselects")
    cb("urbdone:")
    check(db.user(10)["state"] == "currency", "asks currency after neighbourhoods")
    cb("cur:usd")
    msg("ბლა")
    check(db.user(10)["state"] == "price_min", "garbage price keeps asking")
    msg("100000")
    msg("90000")
    check(db.user(10)["state"] == "price_max", "max below min rejected")
    msg("200 000")
    cb("skip:area_min")
    msg("120")
    check(db.user(10)["state"] == "rooms", "rooms asked for flats")
    cb("room:2"); cb("room:3"); cb("room:5"); cb("room:5")
    check(db.user(10)["draft"]["rooms"] == [2, 3], "room toggles")
    cb("roomsdone:")
    msg("2")
    cb("skip:floor_max")
    u = db.user(10)
    f = u["filters"]
    check(u["active"] == 1 and u["state"] is None and f["price_min"] == 100000 and f["price_max"] == 200000
          and f["area_min"] is None and f["area_max"] == 120 and f["rooms"] == [2, 3]
          and f["floor_min"] == 2 and f["floor_max"] is None, "filter saved and active")
    check("2, 3" in describe(f) and "2-დან" in describe(f), "describe shows rooms and floor")
    check("ოთახები" not in describe(dict(f, kinds=["commercial"])), "no rooms line for commercial")
    check("owner_only" not in u["filters"], "no owner-only option")
    msg("/pause"); check(db.user(10)["active"] == 0, "/pause")
    msg("/resume"); check(db.user(10)["active"] == 1, "/resume")
    msg("/filter"); check("საბურთალო" in tg.sends(10)[-1][2], "/filter shows neighbourhoods")

    # empty neighbourhood selection is refused
    msg("/setup"); cb("deal:rent"); cb("kind:both")
    db.update_user(10, draft={"deal": "rent", "kinds": ["flat", "commercial"], "urbans": []})
    cb("urbdone:")
    check(db.user(10)["state"] != "currency", "cannot continue with zero neighbourhoods")
    db.update_user(10, draft={"deal": "rent", "kinds": ["commercial"], "urbans": ["ვაკე"]})
    cb("urbdone:"); cb("cur:any"); cb("skip:area_min"); cb("skip:area_max")
    check(db.user(10)["state"] == "floor_min", "commercial skips rooms; 'price any' skips price")
    cb("skip:floor_max")
    check(db.user(10)["state"] == "floor_min", "stale skip button ignored")

    # admin
    msg("/setplan 10 999", c={"id": 1, "first_name": "Admin"})
    check(db.user(10)["max_urbans"] == 999, "admin /setplan")
    msg("/setplan 10 4", c={"id": 77, "first_name": "Hacker"})
    check(db.user(10)["max_urbans"] == 999, "non-admin cannot /setplan")
    msg("/stats", c={"id": 1, "first_name": "Admin"})
    check("განცხადებები" in tg.sends(1)[-1][2], "admin /stats")

    # group chat
    group = {"id": -100123, "title": "ბინები ჯგუფი", "type": "supergroup"}
    msg("/setup@my_bot", c=group)
    check(db.user(-100123)["state"] == "deal", "works in a group with @botname suffix")


def test_tracker_bumps():
    print("tracker: bumps, new listings, duplicates")
    db = DB(os.path.join(tempfile.mkdtemp(), "t.sqlite3"))
    tg = FakeTG()
    trackermod.REQUEST_PAUSE = 0
    dedup.image_hash = lambda url: url and url[-16:]   # photo url tail = fake hash
    now = datetime.now(timezone.utc)

    pages = {}

    def fake_fetch(source, deal, kind, page=1, retries=2):
        if (source, deal, kind) != ("ss", "sale", "flat"):
            return []
        return pages.get(page, [])

    sources.fetch = fake_fetch
    t = Tracker(db, tg)

    old = [listing(id=1000 + i, urban="ვაკე", floor=i, price_usd=90000 + i, image=f"x{i:015x}0",
                   created=now - timedelta(days=30), activity=now - timedelta(minutes=10 + i))
           for i in range(16)]
    pages = {1: old}
    t.poll()   # warm-up
    check(len(tg.log) == 0, "warm-up sends nothing")
    check(db.get_meta("wm:ss:sale:flat") is not None, "watermark stored")

    db.ensure_user(10, "Nino", 4)
    db.update_user(10, active=1, filters={"deal": "sale", "kinds": ["flat"], "urbans": ["ვაკე", "საბურთალო"],
                                          "price_min": None, "price_max": 300000, "currency": "usd"})
    db.set_meta("lp:ss:sale:flat", (now - timedelta(minutes=4)).isoformat())

    # 2 pages of paid bumps (old, unseen) push a brand-new listing to page 3
    bumps = [listing(id=500 + i, urban="ვაკე", floor=20 + i, price_usd=80000, image=f"b{i:015x}0",
                     created=now - timedelta(days=60), activity=now - timedelta(seconds=30)) for i in range(32)]
    new = listing(id=5000, urban="საბურთალო", image="n00000000000000a1", created=now - timedelta(minutes=2),
                  activity=now - timedelta(minutes=2))
    pages = {1: bumps[:16], 2: bumps[16:], 3: [new] + old[:15]}
    t.poll()
    sent = tg.sends(10)
    check(len(sent) == 1 and "5000" in str(sent[0]) or (len(sent) == 1 and "საბურთალო" in sent[0][2]),
          "new listing on page 3 found despite bumps")
    check(not any("ვაკე" in e[2] for e in sent), "bumped old listings not sent")

    # broker reposts the same flat (same photo) on the other site
    tg.log.clear()
    dup = listing(source="myhome", id=777, url="https://www.myhome.ge/pr/777/", urban="საბურთალო",
                  image="n00000000000000a1", price_usd=147000, owner="agent", created=None,
                  activity=now)
    db.set_meta("wm:myhome:sale:flat", 100)
    db.set_meta("lp:myhome:sale:flat", (now - timedelta(minutes=4)).isoformat())

    def fake_fetch2(source, deal, kind, page=1, retries=2):
        return [dup] if (source, deal, kind, page) == ("myhome", "sale", "flat", 1) else []
    sources.fetch = fake_fetch2
    t.poll()
    check(not tg.sends(10), "duplicate does not create a new message")
    edits = [e for e in tg.log if e[0] == "edit"]
    check(len(edits) == 1 and "myhome.ge/pr/777" in edits[0][2], "duplicate link appended to original")

    # a blocked user must not break delivery to others
    tg.log.clear()
    tg.blocked.add(10)
    db.ensure_user(11, "Giorgi", 4)
    db.update_user(11, active=1, filters={"deal": "sale", "kinds": ["flat"], "urbans": ["საბურთალო"],
                                          "currency": "usd"})
    t.ingest(listing(id=6000, floor=9, image="v00000000000000a1"), notify=True)
    check(len(tg.sends(11)) == 1, "other users still notified when one blocked the bot")
    check(db.user(10)["active"] == 0, "user who blocked the bot is deactivated")

    # restart: new Tracker instance keeps state
    t2 = Tracker(db, tg)
    check(db.get_meta("wm:ss:sale:flat") is not None and t2.db.known_ids("ss", [5000]) == {5000},
          "state survives restart")


def test_is_new():
    print("freshness")
    now = datetime.now(timezone.utc)
    check(Tracker._is_new({"created": now - timedelta(hours=1), "id": 1}, 10, now), "ss created 1h ago")
    check(not Tracker._is_new({"created": now - timedelta(days=3), "id": 99}, 10, now), "ss bumped 3 days old")
    check(Tracker._is_new({"created": None, "id": 11}, 10, now), "myhome id above watermark")
    check(not Tracker._is_new({"created": None, "id": 9}, 10, now), "myhome id below watermark")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf8")
    test_parse_number()
    test_matches()
    test_dedup()
    test_is_new()
    test_bot()
    test_tracker_bumps()
    print(f"\n{'ALL PASSED' if not FAILS else f'{len(FAILS)} FAILED'}")
    sys.exit(1 if FAILS else 0)
