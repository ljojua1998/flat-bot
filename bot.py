"""Telegram conversation: users pick deal, type, neighbourhoods, price, area."""
import html
import logging
import re

import districts
from telegram import TelegramError

log = logging.getLogger("bot")

PLANS = {4: 10, 6: 15, 8: 20}   # neighbourhoods -> GEL / month (shown to users)
ALL_CITY = 999                  # max_urbans value that unlocks the whole city

HELP = (
    "ეს ბოტი myhome.ge-სა და ss.ge-ზე ახალ განცხადებებს ადევნებს თვალს და "
    "შენს ფილტრში მოხვედრილ ბინებს და ფართებს მაშინვე გიგზავნის.\n"
    "ერთი და იგივე ბინა, რომელიც რამდენიმე მაკლერმა დადო, ერთხელ მოგივა — "
    "დანარჩენი ლინკები იმავე შეტყობინებას დაემატება.\n\n"
    "/setup — ფილტრის დაყენება\n"
    "/filter — ჩემი ფილტრი\n"
    "/pause — შეტყობინებების გაჩერება\n"
    "/resume — გაგრძელება\n"
    "/plans — პაკეტები"
)


class Bot:
    def __init__(self, db, tg, admin_ids, default_max_urbans):
        self.db = db
        self.tg = tg
        self.admins = set(admin_ids)
        self.default_max = default_max_urbans

    # ---------- dispatch ----------
    def handle(self, update):
        try:
            if "callback_query" in update:
                self._callback(update["callback_query"])
            elif "message" in update and update["message"].get("text"):
                self._message(update["message"])
        except TelegramError as e:
            log.warning("telegram error: %s", e)

    def _user(self, chat):
        name = chat.get("title") or " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x)
        return self.db.ensure_user(chat["id"], name, self.default_max)

    def _message(self, msg):
        chat_id = msg["chat"]["id"]
        text = msg["text"].strip()
        user = self._user(msg["chat"])
        cmd = text.split()[0].split("@")[0].lower() if text.startswith("/") else None

        if cmd == "/start":
            self.tg.send(chat_id, "გამარჯობა! 👋\n\n" + HELP)
            if not user["filters"]:
                self.start_setup(chat_id)
        elif cmd == "/help":
            self.tg.send(chat_id, HELP)
        elif cmd == "/setup":
            self.start_setup(chat_id)
        elif cmd == "/filter":
            f = user["filters"]
            status = "✅ აქტიურია" if user["active"] else "⏸ გაჩერებულია"
            self.tg.send(chat_id, (describe(f) + f"\n\n{status}") if f else "ფილტრი ჯერ არ გაქვს. /setup")
        elif cmd == "/pause":
            self.db.update_user(chat_id, active=0)
            self.tg.send(chat_id, "⏸ შეტყობინებები გაჩერდა. გასაგრძელებლად: /resume")
        elif cmd == "/resume":
            if not user["filters"]:
                return self.start_setup(chat_id)
            self.db.update_user(chat_id, active=1)
            self.tg.send(chat_id, "▶️ შეტყობინებები გაგრძელდა.")
        elif cmd == "/plans":
            self.tg.send(chat_id, plans_text(user["max_urbans"]))
        elif cmd in ("/setplan", "/stats", "/users") and chat_id in self.admins:
            self._admin(chat_id, cmd, text.split()[1:])
        elif user["state"] in NUMBER_STEPS:
            self._got_number(chat_id, user, text)
        else:
            self.tg.send(chat_id, HELP)

    # ---------- setup flow ----------
    def start_setup(self, chat_id):
        self.db.update_user(chat_id, state="deal", draft={})
        self.tg.send(chat_id, f"{step_no({}, 'deal')} · რა გაინტერესებს?",
                     [[btn("🏷 ყიდვა", "deal:sale"), btn("🔑 ქირა", "deal:rent")]])

    def _callback(self, cq):
        chat_id = cq["message"]["chat"]["id"]
        mid = cq["message"]["message_id"]
        data = cq.get("data", "")
        user = self._user(cq["message"]["chat"])
        d = user["draft"]
        answer = None
        key, _, arg = data.partition(":")

        if key == "deal":
            d["deal"] = arg
            self.db.update_user(chat_id, state="kind", draft=d)
            self.tg.edit(chat_id, mid, f"{step_no(d, 'kind')} · რა ტიპის ქონება?", [
                [btn("🏢 ბინა", "kind:flat"), btn("🏬 კომერციული ფართი", "kind:commercial")],
                [btn("ორივე", "kind:both")]])
        elif key == "kind":
            d["kinds"] = ["flat", "commercial"] if arg == "both" else [arg]
            d.setdefault("urbans", [])
            self.db.update_user(chat_id, state="urbans", draft=d)
            self._show_raions(chat_id, mid, user["max_urbans"], d)
        elif key == "ray":
            self._show_urbans(chat_id, mid, int(arg), user["max_urbans"], d)
        elif key == "back":
            self._show_raions(chat_id, mid, user["max_urbans"], d)
        elif key == "urb":
            name = districts.URBANS[int(arg)]
            sel = d.setdefault("urbans", [])
            if name in sel:
                sel.remove(name)
            elif len(sel) >= user["max_urbans"]:
                answer = f"შენს პაკეტში მაქსიმუმ {user['max_urbans']} უბანია. /plans"
            else:
                sel.append(name)
            self.db.update_user(chat_id, draft=d)
            raion = next(i for i, r in enumerate(districts.RAION_NAMES) if name in districts.RAIONS[r])
            self._show_urbans(chat_id, mid, raion, user["max_urbans"], d)
        elif key == "city":
            d["all_city"] = not d.get("all_city")
            self.db.update_user(chat_id, draft=d)
            self._show_raions(chat_id, mid, user["max_urbans"], d)
        elif key == "urbdone":
            if not d.get("urbans") and not d.get("all_city"):
                answer = "აირჩიე ერთი უბანი მაინც"
            else:
                self._ask(chat_id, "currency", d, mid)
        elif key == "cur":
            if arg == "any":    # price doesn't matter: skip both price fields
                d.update(currency="usd", price_min=None, price_max=None)
                self._ask(chat_id, next_step(d, "price_max"), d, mid)
            else:
                d["currency"] = arg
                self._ask(chat_id, "price_min", d, mid)
        elif key == "skip" and arg == user["state"]:
            d[arg] = None
            self._ask(chat_id, next_step(d, arg), d, mid)
        elif key == "room" and user["state"] == "rooms":
            sel = d.setdefault("rooms", [])
            n = int(arg)
            sel.remove(n) if n in sel else sel.append(n)
            sel.sort()
            self._ask(chat_id, "rooms", d, mid)
        elif key == "roomsdone" and user["state"] == "rooms":
            self._ask(chat_id, next_step(d, "rooms"), d, mid)
        self.tg.answer(cq["id"], answer)

    def _show_raions(self, chat_id, mid, max_urbans, d):
        sel = set(d.get("urbans", []))
        rows = []
        for i, r in enumerate(districts.RAION_NAMES):
            n = len(sel & set(districts.RAIONS[r]))
            rows.append([btn(f"{r}" + (f"  ✅{n}" if n else ""), f"ray:{i}")])
        if max_urbans >= ALL_CITY:
            rows.append([btn(("✅ " if d.get("all_city") else "") + "მთელი თბილისი", "city:")])
        rows.append([btn(f"✔️ შემდეგი ({len(sel)}/{limit_text(max_urbans)})", "urbdone:")])
        self.tg.edit(chat_id, mid, f"{step_no(d, 'urbans')} · აირჩიე რაიონი, შემდეგ უბნები:", rows)

    def _show_urbans(self, chat_id, mid, raion_idx, max_urbans, d):
        raion = districts.RAION_NAMES[raion_idx]
        sel = set(d.get("urbans", []))
        names = districts.RAIONS[raion]
        rows, row = [], []
        for name in names:
            row.append(btn(("✅ " if name in sel else "") + name, f"urb:{districts.URBAN_INDEX[name]}"))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([btn("⬅️ რაიონები", "back:"),
                     btn(f"✔️ შემდეგი ({len(sel)}/{limit_text(max_urbans)})", "urbdone:")])
        self.tg.edit(chat_id, mid, f"{step_no(d, 'urbans')} · {raion} — მონიშნე უბნები:", rows)

    def _ask(self, chat_id, step, d, mid=None):
        """Show the question for `step`, editing the button message or sending a new one."""
        if step is None:
            return self._finish(chat_id, mid, d)
        self.db.update_user(chat_id, state=step, draft=d)
        no = step_no(d, step)
        if step == "currency":
            text = f"{no} · 💵 ფასი — რა ვალუტაში?"
            kb = [[btn("$ დოლარი", "cur:usd"), btn("₾ ლარი", "cur:gel")],
                  [btn("ფასს არ აქვს მნიშვნელობა", "cur:any")]]
        elif step == "rooms":
            sel = d.get("rooms") or []
            text = f"{no} · 🚪 რამდენოთახიანი? მონიშნე ერთი ან რამდენიმე:"
            kb = [[btn(("✅ " if n in sel else "") + room_label(n), f"room:{n}") for n in ROOM_CHOICES],
                  [btn("✔️ შემდეგი" if sel else "ნებისმიერი", "roomsdone:")]]
        else:
            title, example, skip = NUMBER_STEPS[step]
            if step.startswith("price"):
                example = PRICE_EXAMPLES[(d["deal"], d["currency"], step)]
                title = title.format(sym="₾" if d["currency"] == "gel" else "$")
            text = f"{no} · {title}\nჩაწერე რიცხვი, მაგ: {example}"
            kb = [[btn(skip, f"skip:{step}")]]
        if mid:
            self.tg.edit(chat_id, mid, text, kb)
        else:
            self.tg.send(chat_id, text, kb)

    def _got_number(self, chat_id, user, text):
        step, d = user["state"], user["draft"]
        n = parse_number(text, allow_negative=step.startswith("floor"))
        if n is None:
            return self.tg.send(chat_id, f"ვერ გავიგე 🙏 ჩაწერე მხოლოდ რიცხვი, მაგ: {self._example(step, d)}",
                                [[btn(NUMBER_STEPS[step][2], f"skip:{step}")]])
        lo_key = step.replace("_max", "_min")
        if step.endswith("_max") and d.get(lo_key) is not None and n < d[lo_key]:
            return self.tg.send(chat_id, f"მაქსიმუმი მინიმუმზე ({d[lo_key]:,.0f}) ნაკლები ვერ იქნება. თავიდან ჩაწერე:",
                                [[btn(NUMBER_STEPS[step][2], f"skip:{step}")]])
        d[step] = int(n) if step.startswith("floor") else n
        self._ask(chat_id, next_step(d, step), d)

    @staticmethod
    def _example(step, d):
        if step.startswith("price"):
            return PRICE_EXAMPLES[(d["deal"], d["currency"], step)]
        return NUMBER_STEPS[step][1]

    def _finish(self, chat_id, mid, d):
        self.db.update_user(chat_id, state=None, draft={}, filters=d, active=1)
        text = ("✅ მზადაა! ყველა ახალი უნიკალური განცხადება "
                "(მესაკუთრისაც და მაკლერისაც) ასე მოგივა:\n\n" + describe(d) +
                "\n\nშეცვლა: /setup · გაჩერება: /pause")
        if mid:
            self.tg.edit(chat_id, mid, text)
        else:
            self.tg.send(chat_id, text)

    # ---------- admin ----------
    def _admin(self, chat_id, cmd, args):
        nums = [int(d) for d in re.findall(r"\d+", " ".join(args))]   # tolerate "<123>" or "8:"
        if cmd == "/setplan" and len(nums) == 2:
            target, n = nums
            if not self.db.user(target):
                return self.tg.send(chat_id, f"❌ {target} ვერ ვიპოვე. /users")
            self.db.update_user(target, max_urbans=n)
            self.tg.send(chat_id, f"✅ {target}: {limit_text(n)} უბანი")
            try:
                self.tg.send(target, f"🎉 შენი პაკეტი განახლდა: {limit_text(n)} უბანი. /setup")
            except TelegramError:
                pass
        elif cmd == "/users":
            rows = self.db.q("SELECT chat_id, name, active, max_urbans FROM users ORDER BY created DESC LIMIT 50")
            self.tg.send(chat_id, "\n".join(
                f"{'✅' if r['active'] else '⏸'} <code>{r['chat_id']}</code> {html.escape(r['name'] or '')} · {limit_text(r['max_urbans'])}"
                for r in rows) or "ცარიელია")
        elif cmd == "/stats":
            s = self.db.q("""SELECT (SELECT COUNT(*) FROM listings) l, (SELECT COUNT(*) FROM groups) g,
                             (SELECT COUNT(*) FROM users WHERE active=1) u, (SELECT COUNT(*) FROM sent) s""")[0]
            self.tg.send(chat_id, f"განცხადებები: {s['l']}\nუნიკალური ბინები: {s['g']}\n"
                                  f"აქტიური მომხმარებლები: {s['u']}\nგაგზავნილი: {s['s']}")
        else:
            self.tg.send(chat_id, "გამოყენება: <code>/setplan 1810261069 8</code>\n"
                                  "(chat_id, მერე უბნების რაოდენობა: 4, 6, 8 ან 999 = მთელი ქალაქი)\n/users · /stats")


def btn(text, data):
    return {"text": text, "callback_data": data}


def limit_text(n):
    return "მთელი ქალაქი" if n >= ALL_CITY else str(n)


def plans_text(current):
    lines = ["<b>პაკეტები (თვეში)</b>"]
    lines += [f"• {n} უბნამდე — {p} ₾" for n, p in PLANS.items()]
    lines.append(f"\nშენი ამჟამინდელი ლიმიტი: {limit_text(current)} უბანი")
    lines.append("გასააქტიურებლად მოგვწერე.")
    return "\n".join(lines)


# ---------- setup steps after the neighbourhoods ----------
# Each range is asked as two separate questions (from, to), each with a skip button.
STEPS = ["deal", "kind", "urbans", "currency", "price_min", "price_max",
         "area_min", "area_max", "rooms", "floor_min", "floor_max"]
NUMBER_STEPS = {   # step -> (question, example, skip button)
    "price_min": ("💵 ფასი — მინიმუმი ({sym})?", "", "მინიმუმის გარეშე"),
    "price_max": ("💵 ფასი — მაქსიმუმი ({sym})?", "", "მაქსიმუმის გარეშე"),
    "area_min": ("📐 ფართი — მინიმუმი (მ²)?", "45", "მინიმუმის გარეშე"),
    "area_max": ("📐 ფართი — მაქსიმუმი (მ²)?", "80", "მაქსიმუმის გარეშე"),
    "floor_min": ("🏢 სართული — საიდან?", "2", "ნებისმიერი"),
    "floor_max": ("🏢 სართული — სანამდე?", "10", "ნებისმიერი"),
}
PRICE_EXAMPLES = {
    ("sale", "usd", "price_min"): "80000", ("sale", "usd", "price_max"): "120000",
    ("sale", "gel", "price_min"): "220000", ("sale", "gel", "price_max"): "320000",
    ("rent", "usd", "price_min"): "400", ("rent", "usd", "price_max"): "700",
    ("rent", "gel", "price_min"): "1000", ("rent", "gel", "price_max"): "1800",
}
ROOM_CHOICES = [1, 2, 3, 4, 5]   # 5 means 5 or more


def room_label(n):
    return f"{n}+" if n == ROOM_CHOICES[-1] else str(n)


def _steps_for(d):
    # rooms only make sense for flats
    return [s for s in STEPS if s != "rooms" or "flat" in d.get("kinds", ["flat"])]


def next_step(d, step):
    steps = _steps_for(d)
    i = steps.index(step) + 1
    return steps[i] if i < len(steps) else None


def step_no(d, step):
    """'4/7' style progress; price, area and floor pairs count as one question each."""
    groups = []
    for s in _steps_for(d):
        g = s.split("_")[0].replace("currency", "price")
        if g not in groups:
            groups.append(g)
    return f"{groups.index(step.split('_')[0].replace('currency', 'price')) + 1}/{len(groups)}"


def parse_number(text, allow_negative=False):
    """'80000', '80 000', '80,000', '80k', '$80000', '45 მ²' -> float; anything else -> None."""
    t = text.lower().replace(" ", "").replace(",", "")
    m = re.search(r"(-?)(\d+(?:\.\d+)?)(k|ათას)?", t)
    if not m or (m.group(1) and not allow_negative):
        return None
    if re.search(r"\d", t[m.end():]):   # two numbers, e.g. "50000-90000"
        return None
    n = float(m.group(2)) * (1000 if m.group(3) else 1)
    return -n if m.group(1) else n


def describe(f):
    deal = {"sale": "ყიდვა", "rent": "ქირა"}[f["deal"]]
    kinds = " + ".join({"flat": "ბინა", "commercial": "კომერციული ფართი"}[k] for k in f["kinds"])
    where = "მთელი თბილისი" if f.get("all_city") else ", ".join(f["urbans"])
    sym = "₾" if f.get("currency") == "gel" else "$"
    lines = [f"<b>შენი ფილტრი</b>", f"🏷 {deal} · {kinds}", f"📍 {html.escape(where)}",
             f"💵 {range_text(f.get('price_min'), f.get('price_max'), sym)}",
             f"📐 {range_text(f.get('area_min'), f.get('area_max'), '', ' მ²')}"]
    if "flat" in f["kinds"]:
        rooms = ", ".join(room_label(n) for n in f.get("rooms") or []) or "ნებისმიერი"
        lines.append(f"🚪 ოთახები: {rooms}")
    lines.append(f"🏢 სართული: {range_text(f.get('floor_min'), f.get('floor_max'))}")
    return "\n".join(lines)


def range_text(lo, hi, pre="", post=""):
    fmt = lambda v: f"{pre}{v:,.0f}{post}"
    if lo is not None and hi is not None:
        return f"{fmt(lo)} – {fmt(hi)}"
    if hi is not None:
        return f"{fmt(hi)}-მდე"
    if lo is not None:
        return f"{fmt(lo)}-დან"
    return "ნებისმიერი"
