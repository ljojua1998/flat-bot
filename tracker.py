"""Poll both sites, store new listings, group duplicates, notify matching users."""
import html
import logging
import time
from datetime import datetime, timedelta, timezone

import dedup
import districts
import sources
from db import now

log = logging.getLogger("tracker")

REQUEST_PAUSE = 1.5    # seconds between page requests
OVERLAP = timedelta(minutes=3)       # re-read a little before the previous poll
NEW_MAX_AGE = timedelta(hours=24)    # older unseen listings are bumps, not new
MAX_EXTRA_LINKS = 6    # duplicate links appended to one notification
SEND_PAUSE = 0.05      # stay well under Telegram's ~30 messages/second


class Tracker:
    def __init__(self, db, tg=None):
        self.db = db
        self.tg = tg
        self.unknown_urbans = set()

    # ---------- scraping ----------
    def poll(self):
        """One pass over every site and category. Returns number of new listings."""
        total = 0
        for source in sources.FETCHERS:
            for deal, kind in sources.MYHOME_CATS:
                try:
                    total += self._poll_category(source, deal, kind)
                except Exception as e:
                    log.warning("poll %s %s/%s failed: %s", source, deal, kind, e)
        return total

    def _poll_category(self, source, deal, kind):
        # Both sites list by last activity (new, renewed or paid bump), so
        # brand-new listings get pushed down by bumps. We page until we reach
        # activity older than the previous poll, and decide "new" by creation
        # date (ss) or by id above the id seen at warm-up (myhome).
        wm_key = f"wm:{source}:{deal}:{kind}"
        lp_key = f"lp:{source}:{deal}:{kind}"
        watermark = self.db.get_meta(wm_key)
        warmup = watermark is None
        last_poll = self.db.get_meta(lp_key)
        horizon = (datetime.fromisoformat(last_poll) - OVERLAP) if last_poll else None
        started = datetime.now(timezone.utc)

        fresh, top_id = [], 0
        max_pages = 5 if warmup else sources.MAX_PAGES[source]
        for page in range(1, max_pages + 1):
            items = sources.fetch(source, deal, kind, page)
            time.sleep(REQUEST_PAUSE)
            if not items:
                break
            known = self.db.known_ids(source, [i["id"] for i in items])
            fresh.extend(i for i in items if i["id"] not in known)
            top_id = max([top_id] + [i["id"] for i in items])
            activity = [i["activity"] for i in items if i["activity"]]
            if horizon is None or (activity and min(activity) < horizon):
                break
        else:
            if not warmup:
                log.warning("%s %s/%s: hit page limit %d before reaching last poll", source, deal, kind, max_pages)

        new_count = 0
        # oldest first so notifications arrive in posting order
        for item in sorted(fresh, key=lambda i: i["id"]):
            notify = not warmup and self._is_new(item, int(watermark or 0), started)
            self.ingest(item, notify)
            new_count += notify
        if warmup:
            self.db.set_meta(wm_key, top_id)
            log.info("warm-up %s %s/%s: stored %d listings silently", source, deal, kind, len(fresh))
        self.db.set_meta(lp_key, started.isoformat())
        return new_count

    @staticmethod
    def _is_new(item, watermark, now_utc):
        """Unseen listing that was actually created recently, not an old one bumped up."""
        if item.get("created"):
            return now_utc - item["created"] <= NEW_MAX_AGE
        return item["id"] > watermark

    # ---------- dedup + notify ----------
    def ingest(self, item, notify=True):
        item = dict(item)
        item["urban"] = districts.canonical(item["urban"])
        if item["urban"] and not districts.is_known(item["urban"]) and item["urban"] not in self.unknown_urbans:
            self.unknown_urbans.add(item["urban"])
            log.info("unknown neighbourhood name: %s", item["urban"])
        item["image_hash"] = dedup.image_hash(item.get("image"))
        item["first_seen"] = now()

        group_id = None
        if item["urban"]:
            for c in self.db.candidates(item["deal"], item["kind"], item["urban"]):
                if dedup.same_property(item, c):
                    group_id = c["group_id"]
                    break
        is_duplicate = group_id is not None
        if not is_duplicate:
            group_id = self.db.new_group()
        item["group_id"] = group_id
        self.db.add_listing(item)

        if notify and self.tg:
            self._notify(item, is_duplicate)
        return group_id, is_duplicate

    def _notify(self, item, is_duplicate):
        group_id = item["group_id"]
        notified = {r["chat_id"]: r for r in self.db.recipients(group_id)} if is_duplicate else {}
        for user in self.db.active_users():
            try:
                if user["chat_id"] in notified:
                    self._append_duplicate(user["chat_id"], notified[user["chat_id"]], item)
                elif matches(user["filters"], item):
                    text = format_listing(item, repost=is_duplicate)
                    msg = self.tg.send(user["chat_id"], text)
                    self.db.mark_sent(user["chat_id"], group_id, msg["message_id"], text)
                    time.sleep(SEND_PAUSE)
            except Exception as e:
                log.warning("notify %s failed: %s", user["chat_id"], e)
                if getattr(e, "code", None) == 403:   # user blocked the bot or left the group
                    self.db.update_user(user["chat_id"], active=0)

    def _append_duplicate(self, chat_id, sent_row, item):
        if sent_row["extra"] >= MAX_EXTRA_LINKS:
            return
        line = f'\n➕ იგივე ბინა: <a href="{item["url"]}">{SITE_NAMES[item["source"]]}</a> · {price_text(item)}'
        text = sent_row["text"] + line
        self.tg.edit(chat_id, sent_row["message_id"], text)
        self.db.update_sent(chat_id, item["group_id"], text)


# ---------- filters ----------
def matches(f, item):
    if not f:
        return False
    if item["deal"] != f["deal"] or item["kind"] not in f["kinds"]:
        return False
    if not f.get("all_city") and item["urban"] not in f["urbans"]:
        return False
    price = item["price_usd"] if f.get("currency", "usd") == "usd" else item["price_gel"]
    if f.get("price_min") or f.get("price_max"):
        if not price:
            return False
        if f.get("price_min") and price < f["price_min"]:
            return False
        if f.get("price_max") and price > f["price_max"]:
            return False
    if f.get("area_min") or f.get("area_max"):
        if not item["area"]:
            return False
        if f.get("area_min") and item["area"] < f["area_min"]:
            return False
        if f.get("area_max") and item["area"] > f["area_max"]:
            return False
    if f.get("rooms") and item["kind"] == "flat":   # 5 stands for 5 or more
        if not item.get("rooms") or min(item["rooms"], 5) not in f["rooms"]:
            return False
    lo, hi = f.get("floor_min"), f.get("floor_max")
    if lo is not None or hi is not None:
        if item.get("floor") is None:
            return False
        if lo is not None and item["floor"] < lo:
            return False
        if hi is not None and item["floor"] > hi:
            return False
    return True


# ---------- formatting ----------
SITE_NAMES = {"myhome": "myhome.ge", "ss": "ss.ge"}
OWNER_TEXT = {"owner": "მესაკუთრე", "agent": "აგენტი", "developer": "დეველოპერი"}


def price_text(item):
    parts = []
    if item.get("price_usd"):
        parts.append(f"${item['price_usd']:,.0f}")
    if item.get("price_gel"):
        parts.append(f"₾{item['price_gel']:,.0f}")
    return " / ".join(parts) or "ფასი შეთანხმებით"


def format_listing(item, repost=False):
    e = html.escape
    head = "🔁 ხელახლა დადებული ბინა (საიტზე უკვე იყო)" if repost else "🏠 ახალი განცხადება"
    lines = [f"<b>{head}</b>", e(item.get("title") or "")]
    loc = ", ".join(x for x in (item.get("urban"), item.get("address")) if x)
    if loc:
        lines.append(f"📍 {e(loc)}")
    p = f"💵 {price_text(item)}"
    if item.get("price_usd") and item.get("area"):
        p += f" · ${item['price_usd'] / item['area']:,.0f}/მ²"
    lines.append(p)
    facts = []
    if item.get("area"):
        facts.append(f"{item['area']:g} მ²")
    if item.get("rooms"):
        facts.append(f"{item['rooms']} ოთახი")
    if item.get("floor") is not None:
        facts.append(f"სართ. {item['floor']}" + (f"/{item['total_floors']}" if item.get("total_floors") else ""))
    if facts:
        lines.append("📐 " + " · ".join(facts))
    if item.get("owner") in OWNER_TEXT:
        lines.append(f"👤 {OWNER_TEXT[item['owner']]}")
    lines.append(f'🔗 <a href="{item["url"]}">{SITE_NAMES[item["source"]]}</a>')
    return "\n".join(lines)
