"""Minimal Telegram Bot API client (no third-party dependencies)."""
import json
import urllib.error
import urllib.request


class Telegram:
    def __init__(self, token):
        self.base = f"https://api.telegram.org/bot{token}/"

    def call(self, method, _timeout=35, **params):
        params = {k: v for k, v in params.items() if v is not None}
        for k, v in params.items():
            if isinstance(v, (dict, list)):
                params[k] = json.dumps(v, ensure_ascii=False)
        data = json.dumps(params).encode("utf8")
        req = urllib.request.Request(self.base + method, data=data,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=_timeout) as r:
                res = json.loads(r.read())
        except urllib.error.HTTPError as e:
            res = json.loads(e.read() or b"{}")
        if not res.get("ok"):
            raise TelegramError(res.get("description", "unknown error"), res.get("error_code"))
        return res["result"]

    def send(self, chat_id, text, keyboard=None, preview=True):
        return self.call("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML",
                         reply_markup={"inline_keyboard": keyboard} if keyboard else None,
                         link_preview_options={"is_disabled": not preview})

    def edit(self, chat_id, message_id, text, keyboard=None, preview=True):
        try:
            return self.call("editMessageText", chat_id=chat_id, message_id=message_id, text=text,
                             parse_mode="HTML",
                             reply_markup={"inline_keyboard": keyboard} if keyboard else None,
                             link_preview_options={"is_disabled": not preview})
        except TelegramError as e:
            if "message is not modified" not in str(e):
                raise

    def answer(self, callback_id, text=None):
        try:
            self.call("answerCallbackQuery", callback_query_id=callback_id, text=text)
        except TelegramError:
            pass

    def updates(self, offset):
        return self.call("getUpdates", _timeout=60, timeout=40, offset=offset, allowed_updates=["message", "callback_query"])


class TelegramError(Exception):
    def __init__(self, msg, code=None):
        super().__init__(msg)
        self.code = code
