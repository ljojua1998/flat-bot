"""Entry point.

    python main.py            run bot + tracker (needs TELEGRAM_BOT_TOKEN in .env)
    python main.py --once     one scrape pass without Telegram, prints what it found
"""
import logging
import os
import sys
import threading
import time

from bot import Bot
from db import DB
from telegram import Telegram
from tracker import Tracker

HERE = os.path.dirname(os.path.abspath(__file__))


def load_env(path=os.path.join(HERE, ".env")):
    if os.path.exists(path):
        for line in open(path, encoding="utf8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


def tracker_loop(tracker, interval):
    log = logging.getLogger("tracker")
    while True:
        started = time.time()
        try:
            n = tracker.poll()
            log.info("poll done in %.0fs, %d new", time.time() - started, n)
        except Exception:
            log.exception("poll crashed")
        time.sleep(max(30, interval - (time.time() - started)))


def bot_loop(bot, tg):
    log = logging.getLogger("bot")
    offset = None
    while True:
        try:
            for upd in tg.updates(offset):
                offset = upd["update_id"] + 1
                bot.handle(upd)
        except Exception as e:
            log.warning("getUpdates failed: %s", e)
            time.sleep(5)


def main():
    load_env()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout),
                                  logging.FileHandler(os.path.join(HERE, "tracker.log"), encoding="utf8")])
    db = DB(os.environ.get("DB_PATH", os.path.join(HERE, "data.sqlite3")))

    if "--once" in sys.argv:
        t = Tracker(db)
        print("new listings:", t.poll())
        print(db.q("SELECT COUNT(*) listings, COUNT(DISTINCT group_id) properties FROM listings")[0])
        return

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        sys.exit("TELEGRAM_BOT_TOKEN is missing - put it in .env")
    tg = Telegram(token)
    admins = [int(x) for x in os.environ.get("ADMIN_CHAT_IDS", "").replace(" ", "").split(",") if x]
    bot = Bot(db, tg, admins, int(os.environ.get("DEFAULT_MAX_URBANS", 4)))
    tracker = Tracker(db, tg)
    interval = int(os.environ.get("POLL_SECONDS", 240))
    threading.Thread(target=tracker_loop, args=(tracker, interval), daemon=True).start()
    me = tg.call("getMe")
    logging.info("bot @%s running, polling every %ss", me["username"], interval)
    bot_loop(bot, tg)


if __name__ == "__main__":
    main()
