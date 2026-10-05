from __future__ import annotations

from datetime import date
from pathlib import Path

from clothbot.colors import ColorInfo
from clothbot.imaging import ProcessedImage
from clothbot.stylist import Item

_counter = [0]


def item(sub: str, hex_: str, id_: int | None = None, **kw) -> Item:
    from clothbot import catalog
    _counter[0] += 1
    return Item(id=id_ if id_ is not None else _counter[0],
                category=catalog.CATEGORY_OF_SUB[sub], sub=sub,
                colors=[ColorInfo(hex_)], **kw)


def wardrobe_items() -> list[Item]:
    return [
        item("tshirt", "#f7f7f5", 1),     # белая футболка
        item("tshirt", "#1c1c1e", 2),     # чёрная футболка
        item("shirt", "#8ec5ea", 3),      # голубая рубашка
        item("sweater", "#6b1f2c", 4),    # бордовый свитер
        item("hoodie", "#ec7a2c", 5),     # оранжевое худи
        item("jeans", "#4a6382", 6),
        item("trousers", "#1c1c1e", 7),
        item("shorts", "#d6c2a1", 8),
        item("joggers", "#c8327f", 9),    # фуксия-джоггеры
        item("sneakers", "#f7f7f5", 10),
        item("chelsea", "#3f2a1e", 11),
        item("coat", "#b5835a", 12),
        item("puffer", "#1c1c1e", 13),
        item("blazer", "#1f2a44", 14),
        item("tote", "#d6c2a1", 15),
        item("beanie", "#8e8e93", 16),
        item("dress", "#2f8a4c", 17),
        item("sandals", "#6b4a32", 18),
    ]


TODAY = date(2026, 10, 5)


def fake_processor(colors: list[dict] | None = None):
    def run(data: bytes, user_dir: Path) -> ProcessedImage:
        user_dir.mkdir(parents=True, exist_ok=True)
        n = len(list(user_dir.iterdir()))
        (user_dir / f"i{n}.webp").write_bytes(data)
        (user_dir / f"i{n}_t.webp").write_bytes(data)
        return ProcessedImage(f"i{n}.webp", f"i{n}_t.webp",
                              colors or [{"hex": "#1c1c1e", "share": 1.0, "key": "black",
                                          "name": "чёрный", "neutral": True}], False)
    return run
