import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from clothbot import bot as bot_module
from clothbot.bot import Bot
from clothbot.db import Database
from clothbot.services import Wardrobe

from .helpers import fake_processor


class FakeTelegram:
    def __init__(self):
        self.calls = []
        self._mid = 100

    async def call(self, method, files=None, **params):
        self.calls.append((method, params))
        self._mid += 1
        return {"message_id": self._mid}

    async def download(self, file_id):
        return b"jpeg-bytes"

    async def send(self, chat_id, text, markup=None, **kw):
        return await self.call("sendMessage", chat_id=chat_id, text=text, reply_markup=markup)

    async def send_photo(self, chat_id, photo, caption="", markup=None, filename="p.jpg"):
        return await self.call("sendPhoto", chat_id=chat_id, caption=caption, reply_markup=markup)

    async def edit_caption(self, chat_id, message_id, caption, markup=None):
        return await self.call("editMessageCaption", caption=caption, reply_markup=markup)

    async def edit_text(self, chat_id, message_id, text, markup=None):
        return await self.call("editMessageText", text=text, reply_markup=markup)

    async def answer(self, callback_id, text=None):
        return await self.call("answerCallbackQuery", text=text)

    def last(self, method):
        return next(p for m, p in reversed(self.calls) if m == method)

    def buttons(self, method):
        markup = self.last(method)["reply_markup"] or {}
        return [b for row in markup.get("inline_keyboard", []) for b in row]


def msg(text=None, uid=5, **extra):
    m = {"message_id": 1, "chat": {"id": uid}, "from": {"id": uid, "first_name": "Оля"}}
    if text is not None:
        m["text"] = text
    m.update(extra)
    return {"message": m}


def cb(data, uid=5):
    return {"callback_query": {"id": "q", "from": {"id": uid}, "data": data,
                               "message": {"message_id": 9, "chat": {"id": uid}, "caption": "x"}}}


class BotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db = Database(root / "t.db")
        self.w = Wardrobe(self.db, root / "media", fake_processor())
        self.tg = FakeTelegram()
        self.bot = Bot(self.tg, self.w, "https://example.com")
        self.patches = [mock.patch.object(bot_module, "preview_jpeg", lambda p: b"jpg"),
                        mock.patch.object(Bot, "render", lambda self, o: b"jpg")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def run_(self, upd):
        asyncio.run(self.bot.handle(upd))

    def upload(self, sub):
        from clothbot import catalog
        self.run_(msg(photo=[{"file_id": "small"}, {"file_id": "big"}]))
        datas = [b["callback_data"] for b in self.tg.buttons("sendPhoto") if "callback_data" in b]
        item_id = int(datas[0].split(":")[1])
        cat = catalog.CATEGORY_OF_SUB[sub]
        self.assertIn(f"c:{item_id}:{cat}", datas)
        self.run_(cb(f"c:{item_id}:{cat}"))
        self.run_(cb(f"s:{item_id}:{sub}"))
        return item_id

    def test_start_has_webapp_button(self):
        self.run_(msg("/start"))
        btns = self.tg.buttons("sendMessage")
        self.assertEqual(btns[-1]["web_app"]["url"], "https://example.com/app/")

    def test_upload_flow(self):
        item_id = self.upload("tshirt")
        item = self.db.get_item(5, item_id)
        self.assertEqual((item["category"], item["sub"], item["status"]), ("top", "tshirt", "clean"))
        self.assertIn("Добавлено", self.tg.last("editMessageCaption")["caption"])

    def test_delete_draft(self):
        self.run_(msg(photo=[{"file_id": "big"}]))
        item_id = int(self.tg.buttons("sendPhoto")[0]["callback_data"].split(":")[1])
        self.run_(cb(f"x:{item_id}"))
        self.assertIsNone(self.db.get_item(5, item_id))

    def test_today_needs_items(self):
        self.run_(msg("/today"))
        self.assertIn("добавьте", self.tg.last("sendMessage")["text"])

    def test_today_wear_and_laundry(self):
        tee = self.upload("tshirt")
        self.upload("jeans")
        self.upload("sneakers")
        self.run_(msg("/today"))
        caption = self.tg.last("sendPhoto")["caption"]
        self.assertIn("Образ дня", caption)
        wear_btn = next(b for b in self.tg.buttons("sendPhoto") if b.get("callback_data", "").startswith("ow:"))
        self.run_(cb(wear_btn["callback_data"]))
        self.assertIn("постирать", self.tg.last("editMessageCaption")["caption"])
        self.assertEqual(self.db.get_item(5, tee)["status"], "dirty")

        self.run_(msg("/laundry"))
        self.assertIn("Футболка", self.tg.last("sendMessage")["text"])
        self.run_(cb("lw:start"))
        self.assertEqual(self.db.get_item(5, tee)["status"], "washing")
        self.run_(cb("lw:finish"))
        self.assertEqual(self.db.get_item(5, tee)["status"], "clean")
        self.assertIn("пуста", self.tg.last("editMessageText")["text"])

    def test_dislike_sends_new(self):
        for s in ("tshirt", "shirt", "jeans", "trousers", "sneakers"):
            self.upload(s)
        self.run_(msg("/today"))
        od = next(b for b in self.tg.buttons("sendPhoto") if b.get("callback_data", "").startswith("od:"))
        n = sum(1 for m, _ in self.tg.calls if m == "sendPhoto")
        self.run_(cb(od["callback_data"]))
        self.assertEqual(sum(1 for m, _ in self.tg.calls if m == "sendPhoto"), n + 1)
        oid = int(od["callback_data"].split(":")[1])
        self.assertEqual(self.db.get_outfit(5, oid)["status"], "disliked")

    def test_morning_digest_once_per_day(self):
        for s in ("tshirt", "jeans"):
            self.upload(s)
        self.run_(msg("/morning 8"))
        self.assertEqual(self.db.get_user(5)["notify_hour"], 8)
        # tz_offset по умолчанию 180 (МСК): 05:00 UTC = 08:00 local
        now = datetime(2026, 10, 5, 5, 0, tzinfo=timezone.utc)
        self.assertEqual(asyncio.run(self.bot.morning_tick(now)), 1)
        self.assertEqual(asyncio.run(self.bot.morning_tick(now)), 0)
        self.assertEqual(asyncio.run(self.bot.morning_tick(datetime(2026, 10, 5, 6, 0, tzinfo=timezone.utc))), 0)
        self.run_(msg("/morning off"))
        self.assertIsNone(self.db.get_user(5)["notify_hour"])

    def test_bad_callback_is_ignored(self):
        self.run_(cb("s:notanumber:tshirt"))
        self.run_(cb("st:999:dirty"))
        self.assertEqual(self.tg.calls[-1][0], "answerCallbackQuery")


if __name__ == "__main__":
    unittest.main()
