import json
import sqlite3
import threading
from datetime import datetime, timedelta

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    source TEXT, id INTEGER, deal TEXT, kind TEXT, urban TEXT,
    price_usd REAL, price_gel REAL, area REAL, rooms INTEGER,
    floor INTEGER, total_floors INTEGER, owner TEXT, image_hash TEXT,
    similarity TEXT, url TEXT, title TEXT, address TEXT,
    group_id INTEGER, first_seen TEXT,
    PRIMARY KEY (source, id)
);
CREATE INDEX IF NOT EXISTS listings_lookup ON listings (deal, kind, urban, first_seen);
CREATE TABLE IF NOT EXISTS groups (id INTEGER PRIMARY KEY AUTOINCREMENT, created TEXT);
CREATE TABLE IF NOT EXISTS users (
    chat_id INTEGER PRIMARY KEY, name TEXT,
    state TEXT, draft TEXT, filters TEXT,
    active INTEGER DEFAULT 0, max_urbans INTEGER, created TEXT
);
CREATE TABLE IF NOT EXISTS sent (
    chat_id INTEGER, group_id INTEGER, message_id INTEGER, text TEXT, extra INTEGER DEFAULT 0,
    PRIMARY KEY (chat_id, group_id)
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

LISTING_COLS = ["source", "id", "deal", "kind", "urban", "price_usd", "price_gel", "area", "rooms",
                "floor", "total_floors", "owner", "image_hash", "similarity", "url", "title",
                "address", "group_id", "first_seen"]


def now():
    return datetime.now().isoformat(timespec="seconds")


class DB:
    def __init__(self, path):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.executescript(SCHEMA)

    def q(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args)]

    def x(self, sql, args=()):
        with self.lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur

    # meta
    def get_meta(self, key, default=None):
        rows = self.q("SELECT value FROM meta WHERE key=?", (key,))
        return rows[0]["value"] if rows else default

    def set_meta(self, key, value):
        self.x("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, str(value)))

    # listings
    def known_ids(self, source, ids):
        if not ids:
            return set()
        marks = ",".join("?" * len(ids))
        rows = self.q(f"SELECT id FROM listings WHERE source=? AND id IN ({marks})", (source, *ids))
        return {r["id"] for r in rows}

    def candidates(self, deal, kind, urban, days=90):
        since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
        return self.q("SELECT * FROM listings WHERE deal=? AND kind=? AND urban=? AND first_seen>=?",
                      (deal, kind, urban, since))

    def new_group(self):
        return self.x("INSERT INTO groups (created) VALUES (?)", (now(),)).lastrowid

    def add_listing(self, item):
        row = [item.get(c) for c in LISTING_COLS]
        self.x(f"INSERT OR IGNORE INTO listings ({','.join(LISTING_COLS)}) VALUES ({','.join('?' * len(row))})", row)

    def group_listings(self, group_id):
        return self.q("SELECT * FROM listings WHERE group_id=? ORDER BY first_seen", (group_id,))

    # users
    def user(self, chat_id):
        rows = self.q("SELECT * FROM users WHERE chat_id=?", (chat_id,))
        if not rows:
            return None
        u = rows[0]
        u["filters"] = json.loads(u["filters"]) if u["filters"] else None
        u["draft"] = json.loads(u["draft"]) if u["draft"] else {}
        return u

    def ensure_user(self, chat_id, name, max_urbans):
        self.x("INSERT OR IGNORE INTO users (chat_id, name, max_urbans, created) VALUES (?,?,?,?)",
               (chat_id, name, max_urbans, now()))
        return self.user(chat_id)

    def update_user(self, chat_id, **fields):
        for k in ("filters", "draft"):
            if k in fields and not isinstance(fields[k], (str, type(None))):
                fields[k] = json.dumps(fields[k], ensure_ascii=False)
        sets = ",".join(f"{k}=?" for k in fields)
        self.x(f"UPDATE users SET {sets} WHERE chat_id=?", (*fields.values(), chat_id))

    def active_users(self):
        rows = self.q("SELECT chat_id FROM users WHERE active=1 AND filters IS NOT NULL")
        return [self.user(r["chat_id"]) for r in rows]

    # sent messages
    def sent(self, chat_id, group_id):
        rows = self.q("SELECT * FROM sent WHERE chat_id=? AND group_id=?", (chat_id, group_id))
        return rows[0] if rows else None

    def recipients(self, group_id):
        return self.q("SELECT * FROM sent WHERE group_id=?", (group_id,))

    def mark_sent(self, chat_id, group_id, message_id, text):
        self.x("INSERT OR REPLACE INTO sent (chat_id, group_id, message_id, text) VALUES (?,?,?,?)",
               (chat_id, group_id, message_id, text))

    def update_sent(self, chat_id, group_id, text):
        self.x("UPDATE sent SET text=?, extra=extra+1 WHERE chat_id=? AND group_id=?", (text, chat_id, group_id))
