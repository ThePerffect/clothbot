"""Чат-бот: загрузка вещей фотографиями, образ дня, стирка, настройки."""
from __future__ import annotations

import asyncio
import html
import logging
import random
from datetime import datetime, timedelta, timezone

from . import catalog
from .imaging import CutoutError, collage, preview_jpeg
from .services import Suggestion, Wardrobe
from .tg import Telegram, TelegramError

log = logging.getLogger(__name__)

STATUS_LABEL = {"clean": "✨ чистая", "dirty": "🧺 грязная", "washing": "🫧 в стирке",
                "draft": "📝 без категории"}

HELP = (
    "<b>Как пользоваться</b>\n\n"
    "📸 Пришлите фото вещи (можно сразу несколько) — я вырежу фон и спрошу категорию.\n"
    "✨ /today — образ на сегодня с учётом погоды\n"
    "🧺 /laundry — что ждёт стирки\n"
    "🏙 /city Москва — город для прогноза погоды\n"
    "⏰ /morning 8 — присылать образ каждое утро в 8:00 (/morning off — выключить)\n"
    "👗 /wardrobe — открыть гардероб\n\n"
    "Совет: снимайте вещь целиком, разложив на ровном фоне при дневном свете, "
    "и отправляйте <b>файлом</b> — так Telegram не сжимает фото и вырезка будет чище."
)


def kb(rows: list[list[tuple[str, str]]]) -> dict:
    return {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in rows]}


def chunk(buttons: list[tuple[str, str]], n: int) -> list[list[tuple[str, str]]]:
    return [buttons[i:i + n] for i in range(0, len(buttons), n)]


class Bot:
    def __init__(self, tg: Telegram, wardrobe: Wardrobe, webapp_url: str = ""):
        self.tg = tg
        self.w = wardrobe
        self.db = wardrobe.db
        self.webapp_url = webapp_url
        self._tasks: set[asyncio.Task] = set()

    # ---------------------------------------------------------------- utils
    def app_button(self, text: str = "👗 Открыть гардероб", path: str = "") -> dict | None:
        if not self.webapp_url:
            return None
        return {"text": text, "web_app": {"url": f"{self.webapp_url}/app/{path}"}}

    def with_app(self, markup: dict | None, text: str = "👗 Открыть гардероб") -> dict | None:
        btn = self.app_button(text)
        if not btn:
            return markup
        rows = list((markup or {}).get("inline_keyboard", []))
        rows.append([btn])
        return {"inline_keyboard": rows}

    async def setup(self) -> None:
        await self.tg.call("setMyCommands", commands=[
            {"command": "today", "description": "Образ на сегодня"},
            {"command": "laundry", "description": "Стирка"},
            {"command": "wardrobe", "description": "Мой гардероб"},
            {"command": "city", "description": "Город для погоды"},
            {"command": "morning", "description": "Утренняя рассылка образа"},
            {"command": "help", "description": "Помощь"},
        ])
        if self.webapp_url:
            await self.tg.call("setChatMenuButton", menu_button={
                "type": "web_app", "text": "Гардероб",
                "web_app": {"url": f"{self.webapp_url}/app/"}})

    # -------------------------------------------------------------- polling
    async def run_polling(self) -> None:
        offset = None
        while True:
            try:
                updates = await self.tg.updates(offset)
            except Exception:  # noqa: BLE001
                log.warning("getUpdates failed", exc_info=True)
                await asyncio.sleep(3)
                continue
            for upd in updates:
                offset = upd["update_id"] + 1
                task = asyncio.create_task(self.safe_handle(upd))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)

    async def safe_handle(self, upd: dict) -> None:
        try:
            await self.handle(upd)
        except Exception:  # noqa: BLE001
            log.exception("handler failed")

    async def handle(self, upd: dict) -> None:
        if "message" in upd:
            await self.on_message(upd["message"])
        elif "callback_query" in upd:
            await self.on_callback(upd["callback_query"])

    # ------------------------------------------------------------- messages
    async def on_message(self, msg: dict) -> None:
        chat = msg["chat"]["id"]
        user = msg.get("from") or {}
        uid = user.get("id", chat)
        self.db.ensure_user(uid, user.get("first_name"))
        text = (msg.get("text") or "").strip()

        image_id = None
        if msg.get("photo"):
            image_id = msg["photo"][-1]["file_id"]
        elif (msg.get("document") or {}).get("mime_type", "").startswith("image/"):
            image_id = msg["document"]["file_id"]
        if image_id:
            return await self.on_photo(chat, uid, image_id)

        cmd, _, arg = text.partition(" ")
        cmd = cmd.split("@")[0].lower()
        arg = arg.strip()
        handlers = {
            "/start": self.cmd_start, "/help": self.cmd_help, "/today": self.cmd_today,
            "/laundry": self.cmd_laundry, "/wardrobe": self.cmd_wardrobe,
            "/city": self.cmd_city, "/morning": self.cmd_morning,
        }
        if cmd in handlers:
            return await handlers[cmd](chat, uid, arg)
        if msg.get("location"):
            return await self.set_location(chat, uid, msg["location"])
        await self.tg.send(chat, "Пришлите фото вещи или выберите команду 👇\n\n" + HELP,
                           self.with_app(None))

    async def cmd_start(self, chat: int, uid: int, arg: str) -> None:
        name = html.escape((self.db.get_user(uid) or {}).get("first_name") or "")
        text = (
            f"Привет{', ' + name if name else ''}! 👋\n\n"
            "Я — ваш карманный стилист. Сфотографируйте свою одежду, а я:\n"
            "✂️ аккуратно вырежу каждую вещь из фона,\n"
            "🎨 определю её цвета,\n"
            "✨ буду каждый день собирать сочетаемые образы по погоде,\n"
            "🧺 и напомню, что пора в стирку.\n\n"
            "Начните с того, что пришлите фото любой вещи 📸"
        )
        await self.tg.send(chat, text, self.with_app(None))

    async def cmd_help(self, chat: int, uid: int, arg: str) -> None:
        await self.tg.send(chat, HELP, self.with_app(None))

    async def cmd_wardrobe(self, chat: int, uid: int, arg: str) -> None:
        c = self.db.counts(uid)
        items = self.db.list_items(uid)
        by_cat: dict[str, int] = {}
        for i in items:
            by_cat[i["category"]] = by_cat.get(i["category"], 0) + 1
        lines = [f"{cat.emoji} {cat.name}: <b>{by_cat[cat.key]}</b>"
                 for cat in catalog.CATEGORIES if by_cat.get(cat.key)]
        text = (f"👗 <b>Гардероб</b> — {len(items)} вещей\n"
                f"✨ чистых: {c['clean']} · 🧺 грязных: {c['dirty']} · 🫧 в стирке: {c['washing']}\n\n"
                + ("\n".join(lines) if lines else "Пока пусто — пришлите фото вещи 📸"))
        await self.tg.send(chat, text, self.with_app(None))

    async def cmd_city(self, chat: int, uid: int, arg: str) -> None:
        if not arg:
            await self.tg.send(chat, "Напишите город так: <code>/city Москва</code>\n"
                                     "или отправьте геолокацию 📍")
            return
        if not self.w.weather:
            await self.tg.send(chat, "Погода сейчас недоступна 😔")
            return
        place = await self.w.weather.geocode(arg[:80])
        if not place:
            await self.tg.send(chat, "Не нашёл такой город 🤔 Попробуйте иначе.")
            return
        self.db.update_user(uid, city=place.name, lat=place.lat, lon=place.lon,
                            tz_offset=place.tz_offset)
        user = self.db.get_user(uid)
        w = await self.w.weather_for(user)
        extra = f"\nСегодня: {w.summary()}" if w else ""
        await self.tg.send(chat, f"🏙 Город: <b>{html.escape(place.name)}</b>{extra}")

    async def set_location(self, chat: int, uid: int, loc: dict) -> None:
        tz = 180
        if self.w.weather:
            tz = await self.w.weather._tz_offset(loc["latitude"], loc["longitude"])  # noqa: SLF001
        self.db.update_user(uid, city="📍 по геолокации", lat=loc["latitude"],
                            lon=loc["longitude"], tz_offset=tz)
        await self.tg.send(chat, "📍 Запомнил местоположение — буду учитывать погоду.")

    async def cmd_morning(self, chat: int, uid: int, arg: str) -> None:
        if arg.lower() in ("off", "выкл", "нет", "0ff"):
            self.db.update_user(uid, notify_hour=None)
            await self.tg.send(chat, "🔕 Утренняя рассылка выключена.")
            return
        try:
            hour = int(arg.split(":")[0]) if arg else 8
            if not 0 <= hour <= 23:
                raise ValueError
        except ValueError:
            await self.tg.send(chat, "Укажите час: <code>/morning 8</code>")
            return
        self.db.update_user(uid, notify_hour=hour)
        user = self.db.get_user(uid)
        hint = "" if user.get("lat") is not None else \
            "\n\nЧтобы учитывать погоду, укажите город: <code>/city Москва</code>"
        await self.tg.send(chat, f"⏰ Буду присылать образ дня в {hour:02d}:00.{hint}")

    # --------------------------------------------------------------- photos
    async def on_photo(self, chat: int, uid: int, file_id: str) -> None:
        status = await self.tg.send(chat, "✂️ Вырезаю вещь из фона…")
        try:
            await self.tg.call("sendChatAction", chat_id=chat, action="upload_photo")
            data = await self.tg.download(file_id)
            item = await self.w.add_photo(uid, data)
        except CutoutError as e:
            await self.tg.edit_text(chat, status["message_id"], f"😔 {html.escape(str(e))}")
            return
        except Exception:  # noqa: BLE001
            log.exception("photo processing failed")
            await self.tg.edit_text(chat, status["message_id"],
                                    "😔 Не получилось обработать фото. Попробуйте ещё раз.")
            return
        try:
            await self.tg.call("deleteMessage", chat_id=chat, message_id=status["message_id"])
        except TelegramError:
            pass
        preview = await asyncio.to_thread(preview_jpeg, self.w.image_path(item))
        await self.tg.send_photo(chat, preview, self.item_caption(item, "Что это за вещь?"),
                                 self.category_kb(item["id"]))

    def item_caption(self, item: dict, question: str = "") -> str:
        colors = ", ".join(c["name"] for c in item["colors"][:3])
        parts = []
        if item.get("sub"):
            cat = catalog.CATEGORY_BY_KEY[item["category"]]
            parts.append(f"{cat.emoji} <b>{html.escape(item['label'])}</b>")
        if colors:
            parts.append(f"🎨 {colors}" + (" · с принтом" if item.get("pattern") else ""))
        if item.get("status") in ("clean", "dirty", "washing"):
            parts.append(STATUS_LABEL[item["status"]])
        if question:
            parts.append(f"\n{question}")
        return "\n".join(parts)

    def category_kb(self, item_id: int) -> dict:
        btns = [(f"{c.emoji} {c.name}", f"c:{item_id}:{c.key}") for c in catalog.CATEGORIES]
        rows = chunk(btns, 2)
        rows.append([("🗑 Удалить", f"x:{item_id}")])
        return kb(rows)

    def sub_kb(self, item_id: int, cat_key: str) -> dict:
        cat = catalog.CATEGORY_BY_KEY[cat_key]
        rows = chunk([(s.name, f"s:{item_id}:{s.key}") for s in cat.subs], 2)
        rows.append([("← Назад", f"b:{item_id}")])
        return kb(rows)

    def item_kb(self, item: dict) -> dict:
        iid = item["id"]
        st = item["status"]
        row = []
        if st != "dirty":
            row.append(("🧺 В грязное", f"st:{iid}:dirty"))
        if st != "clean":
            row.append(("✨ Чистая", f"st:{iid}:clean"))
        return kb([row, [("✨ Образ с этой вещью", f"lk:{iid}"), ("✏️ Категория", f"b:{iid}")]])

    # ------------------------------------------------------------ callbacks
    async def on_callback(self, cq: dict) -> None:
        uid = cq["from"]["id"]
        msg = cq.get("message") or {}
        chat = (msg.get("chat") or {}).get("id", uid)
        mid = msg.get("message_id")
        data = cq.get("data") or ""
        parts = data.split(":")
        kind = parts[0]
        self.db.ensure_user(uid, cq["from"].get("first_name"))

        def num(i: int) -> int:
            return int(parts[i])

        try:
            if kind == "c":  # выбрана категория
                item = self.db.get_item(uid, num(1))
                if item and parts[2] in catalog.CATEGORY_BY_KEY:
                    cat = catalog.CATEGORY_BY_KEY[parts[2]]
                    await self.tg.edit_caption(chat, mid, self.item_caption(
                        item, f"{cat.emoji} {cat.name} — а точнее?"), self.sub_kb(item["id"], cat.key))
            elif kind == "b":  # назад к категориям
                item = self.db.get_item(uid, num(1))
                if item:
                    await self.tg.edit_caption(chat, mid, self.item_caption(item, "Что это за вещь?"),
                                               self.category_kb(item["id"]))
            elif kind == "s":  # выбрана подкатегория
                sub = parts[2]
                item = self.db.update_item(uid, num(1), category=catalog.CATEGORY_OF_SUB.get(sub),
                                           sub=sub)
                if item:
                    await self.tg.edit_caption(chat, mid, "✅ Добавлено в гардероб\n" +
                                               self.item_caption(item), self.item_kb(item))
                    await self.tg.answer(cq["id"], "Сохранено ✨")
                    return
            elif kind == "x":
                self.w.delete_item(uid, num(1))
                await self.tg.call("deleteMessage", chat_id=chat, message_id=mid)
                await self.tg.answer(cq["id"], "Удалено")
                return
            elif kind == "st":
                item = self.db.update_item(uid, num(1), status=parts[2])
                if item:
                    await self.tg.edit_caption(chat, mid, self.item_caption(item), self.item_kb(item))
            elif kind == "lk":
                await self.tg.answer(cq["id"], "Подбираю…")
                await self.send_outfit(chat, uid, lock=num(1), seed=random.randrange(1 << 16))
                return
            elif kind == "ow":  # надеть
                outfit = self.db.get_outfit(uid, num(1))
                if outfit:
                    dirty = self.w.wear(uid, outfit["item_ids"], outfit["id"])
                    note = ""
                    if dirty:
                        names = ", ".join(i["label"].lower() for i in dirty)
                        note = f"\n\n🧺 После сегодняшнего пора постирать: {html.escape(names)}"
                    await self.tg.edit_caption(chat, mid,
                                               html.escape(msg.get("caption") or "") + "\n\n✅ Отличный выбор! Записал."
                                               + note, None)
            elif kind in ("ol", "od"):
                verdict = "like" if kind == "ol" else "dislike"
                if self.w.feedback(uid, num(1), verdict):
                    await self.tg.answer(cq["id"], "Запомнил ❤️" if verdict == "like"
                                         else "Учту — больше не предложу")
                    if verdict == "dislike":
                        await self.send_outfit(chat, uid, seed=random.randrange(1 << 16))
                    return
            elif kind == "on":  # другой вариант
                await self.tg.answer(cq["id"], "Подбираю другой…")
                await self.send_outfit(chat, uid, seed=random.randrange(1 << 16))
                return
            elif kind == "lw":  # стирка
                action = parts[1]
                if action == "start":
                    self.db.set_status_bulk(uid, "dirty", "washing")
                elif action == "finish":
                    self.db.set_status_bulk(uid, "washing", "clean")
                elif action == "one":
                    self.db.update_item(uid, num(2), status="clean")
                text, markup = self.laundry_view(uid)
                await self.tg.edit_text(chat, mid, text, markup)
        except (ValueError, IndexError):
            log.info("bad callback %r", data)
        await self.tg.answer(cq["id"])

    # --------------------------------------------------------------- outfit
    async def cmd_today(self, chat: int, uid: int, arg: str) -> None:
        await self.send_outfit(chat, uid)

    async def send_outfit(self, chat: int, uid: int, lock: int | None = None,
                          seed: int | None = None, greeting: str = "") -> bool:
        missing = self.w.missing_for_outfits(uid)
        if missing:
            await self.tg.send(chat, "Чтобы собирать образы, добавьте хотя бы: "
                                     f"<b>{', '.join(missing)}</b> 📸", self.with_app(None))
            return False
        await self.tg.call("sendChatAction", chat_id=chat, action="upload_photo")
        outfits, weather = await self.w.suggest(uid, count=1, lock_item=lock, seed=seed)
        if not outfits:
            await self.tg.send(chat, "Не получилось собрать образ из чистых вещей 😔 "
                                     "Проверьте /laundry")
            return False
        o = outfits[0]
        img = await asyncio.to_thread(self.render, o)
        lines = []
        if greeting:
            lines.append(greeting)
        if weather:
            lines.append(weather.summary())
        lines.append("<b>✨ Образ дня</b>" if lock is None else "<b>✨ Образ с этой вещью</b>")
        lines.append(" + ".join(html.escape(i["label"].lower()) for i in o.items))
        lines += [f"• {html.escape(r)}" for r in o.reasons[:3]]
        markup = kb([[("👗 Надену!", f"ow:{o.outfit_id}")],
                     [("❤️", f"ol:{o.outfit_id}"), ("🔄 Другой", f"on:{o.outfit_id}"),
                      ("👎", f"od:{o.outfit_id}")]])
        await self.tg.send_photo(chat, img, "\n".join(lines), self.with_app(markup, "👗 Все варианты"))
        return True

    def render(self, o: Suggestion) -> bytes:
        return collage([(i["category"], self.w.image_path(i)) for i in o.items])

    # -------------------------------------------------------------- laundry
    def laundry_view(self, uid: int) -> tuple[str, dict | None]:
        dirty = self.db.list_items(uid, "dirty")
        washing = self.db.list_items(uid, "washing")
        if not dirty and not washing:
            return "🧺 Корзина пуста — всё чистое ✨", self.with_app(None)
        lines = ["<b>🧺 Стирка</b>"]
        rows: list[list[tuple[str, str]]] = []
        if dirty:
            lines.append(f"\nГрязное ({len(dirty)}):")
            lines += [f"• {html.escape(i['label'])} — {', '.join(c['name'] for c in i['colors'][:2])}"
                      for i in dirty]
            rows.append([("🫧 Загрузить в стирку", "lw:start")])
            rows += chunk([(f"✨ {i['label']}", f"lw:one:{i['id']}") for i in dirty[:8]], 2)
        if washing:
            lines.append(f"\nВ стирке ({len(washing)}):")
            lines += [f"• {html.escape(i['label'])}" for i in washing]
            rows.append([("✅ Постирано — всё чистое", "lw:finish")])
        return "\n".join(lines), self.with_app(kb(rows), "🧺 Открыть стирку")

    async def cmd_laundry(self, chat: int, uid: int, arg: str) -> None:
        text, markup = self.laundry_view(uid)
        await self.tg.send(chat, text, markup)

    # ------------------------------------------------------- morning digest
    async def run_morning(self) -> None:
        while True:
            try:
                await self.morning_tick()
            except Exception:  # noqa: BLE001
                log.exception("morning tick failed")
            await asyncio.sleep(60)

    async def morning_tick(self, now: datetime | None = None) -> int:
        now = now or datetime.now(timezone.utc)
        sent = 0
        for user in self.db.users_with_notifications():
            local = now + timedelta(minutes=user["tz_offset"] or 0)
            today = local.date().isoformat()
            if local.hour != user["notify_hour"] or user["last_notified"] == today:
                continue
            self.db.update_user(user["id"], last_notified=today)
            try:
                if await self.send_outfit(user["id"], user["id"], greeting="Доброе утро ☀️"):
                    sent += 1
            except TelegramError:
                log.warning("morning send failed for %s", user["id"], exc_info=True)
        return sent
