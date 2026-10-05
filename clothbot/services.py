"""Бизнес-логика, общая для чат-бота и Mini App."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from . import catalog, stylist
from .db import Database
from .imaging import ProcessedImage
from .weather import Weather, WeatherClient

Processor = Callable[[bytes, Path], ProcessedImage]


@dataclass
class Suggestion:
    outfit_id: int
    items: list[dict]
    score: float
    reasons: list[str]

    def as_dict(self) -> dict:
        return {"id": self.outfit_id, "items": self.items,
                "score": round(self.score, 3), "reasons": self.reasons}


class Wardrobe:
    def __init__(self, db: Database, media_dir: Path, processor: Processor,
                 weather: WeatherClient | None = None):
        self.db = db
        self.media_dir = media_dir
        self.processor = processor
        self.weather = weather
        self._cpu = asyncio.Semaphore(1)

    # ---------------------------------------------------------------- utils
    def local_today(self, user: dict) -> date:
        tz = timezone(timedelta(minutes=user.get("tz_offset") or 0))
        return datetime.now(tz).date()

    def user_dir(self, user_id: int) -> Path:
        return self.media_dir / str(user_id)

    def image_path(self, item: dict, thumb: bool = False) -> Path:
        return self.user_dir(item["user_id"]) / (item["thumb"] if thumb else item["image"])

    # ---------------------------------------------------------------- items
    async def add_photo(self, user_id: int, data: bytes, category: str | None = None,
                        sub: str | None = None) -> dict:
        if category or sub:
            if not catalog.is_valid(category or "", sub or ""):
                raise ValueError("Неизвестная категория или подкатегория")
        if not self.db.get_user(user_id):
            self.db.ensure_user(user_id)
        async with self._cpu:
            processed = await asyncio.to_thread(self.processor, data, self.user_dir(user_id))
        return self.db.add_item(user_id, image=processed.image, thumb=processed.thumb,
                                colors=processed.colors, pattern=processed.pattern,
                                category=category, sub=sub)

    def delete_item(self, user_id: int, item_id: int) -> bool:
        item = self.db.delete_item(user_id, item_id)
        if not item:
            return False
        for name in (item["image"], item["thumb"]):
            (self.user_dir(user_id) / name).unlink(missing_ok=True)
        return True

    # -------------------------------------------------------------- weather
    async def weather_for(self, user: dict) -> Weather | None:
        if not self.weather or user.get("lat") is None:
            return None
        return await self.weather.today(user["lat"], user["lon"])

    # -------------------------------------------------------------- outfits
    async def suggest(self, user_id: int, count: int = 5, lock_item: int | None = None,
                      formality: float | None = None, seed: int | None = None,
                      ) -> tuple[list[Suggestion], Weather | None]:
        user = self.db.get_user(user_id) or self.db.ensure_user(user_id)
        weather = await self.weather_for(user)
        today = self.local_today(user)
        include = {lock_item} if lock_item else None
        items = self.db.stylist_items(user_id, include_ids=include)
        ctx = stylist.Context(
            today=today,
            temperature=weather.temperature if weather else None,
            precipitation=weather.precipitation if weather else False,
            formality=formality, lock_item=lock_item,
            pair_weights=self.db.pair_weights(user_id),
            disliked=self.db.disliked_sets(user_id),
            seed=seed if seed is not None else hash((user_id, today.toordinal())) & 0xFFFF,
        )
        outfits = await asyncio.to_thread(stylist.generate, items, ctx, count)
        by_id = {i["id"]: i for i in self.db.list_items(user_id)}
        result = []
        for o in outfits:
            oid = self.db.save_outfit(user_id, o.ids, o.score, o.reasons)
            result.append(Suggestion(oid, [by_id[i] for i in o.ids if i in by_id],
                                     o.score, o.reasons))
        self.db.prune_suggestions(user_id)
        return result, weather

    def missing_for_outfits(self, user_id: int) -> list[str]:
        """Чего не хватает в гардеробе, чтобы собирать образы."""
        items = self.db.stylist_items(user_id)
        cats = {i.category for i in items}
        missing = []
        if not ({"top", "bottom"} <= cats or "dress" in cats):
            if "top" not in cats and "dress" not in cats:
                missing.append("верх")
            if "bottom" not in cats and "dress" not in cats:
                missing.append("низ")
        return missing

    def wear(self, user_id: int, item_ids: list[int], outfit_id: int | None = None) -> list[dict]:
        user = self.db.get_user(user_id) or self.db.ensure_user(user_id)
        today = self.local_today(user)
        if outfit_id is None:
            outfit_id = self.db.save_outfit(user_id, item_ids, 0, [])
        self.db.set_outfit_status(user_id, outfit_id, "worn", today.isoformat())
        self.db.add_pair_weights(user_id, stylist.feedback_updates(item_ids, "wear"))
        return self.db.wear(user_id, item_ids, today)

    def feedback(self, user_id: int, outfit_id: int, verdict: str) -> dict | None:
        outfit = self.db.get_outfit(user_id, outfit_id)
        if not outfit or verdict not in ("like", "dislike"):
            return None
        self.db.set_outfit_status(user_id, outfit_id, "liked" if verdict == "like" else "disliked")
        self.db.add_pair_weights(user_id, stylist.feedback_updates(outfit["item_ids"], verdict))
        return outfit

    def history(self, user_id: int, limit: int = 30) -> list[dict]:
        by_id = {i["id"]: i for i in self.db.list_items(user_id)}
        out = []
        for o in self.db.history(user_id, limit):
            o["items"] = [by_id[i] for i in o["item_ids"] if i in by_id]
            if o["items"]:
                out.append(o)
        return out
