"""Справочник категорий и подкатегорий одежды.

warmth    — вклад вещи в «теплоту» образа (0 — ничего, 4 — пуховик).
formality — уровень формальности: 0 спорт, 1 кэжуал, 2 смарт-кэжуал, 3 бизнес, 4 формал.
wash      — через сколько носок вещь по умолчанию уходит в стирку (0 — не отслеживать).
t_min/max — диапазон погоды, в которой вещь уместна.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Sub:
    key: str
    name: str
    warmth: float
    formality: float
    wash: int
    t_min: float | None = None  # комфортная температура «ощущается как», °C
    t_max: float | None = None


@dataclass(frozen=True)
class Category:
    key: str
    name: str
    emoji: str
    subs: tuple[Sub, ...]


CATEGORIES: tuple[Category, ...] = (
    Category("top", "Верх", "👕", (
        Sub("tshirt", "Футболка", 1.0, 1, 1),
        Sub("longsleeve", "Лонгслив", 1.5, 1, 2),
        Sub("tank", "Майка / топ", 0.5, 1, 1, 18, None),
        Sub("polo", "Поло", 1.0, 2, 2),
        Sub("shirt", "Рубашка", 1.5, 3, 2),
        Sub("blouse", "Блузка", 1.0, 3, 2),
        Sub("turtleneck", "Водолазка", 2.0, 3, 3, None, 21),
        Sub("sweater", "Свитер / джемпер", 3.0, 2, 4, None, 19),
        Sub("sweatshirt", "Свитшот", 2.0, 1, 3, None, 23),
        Sub("hoodie", "Худи", 2.5, 0.5, 3, None, 23),
    )),
    Category("layer", "Слой", "🧥", (
        Sub("cardigan", "Кардиган", 2.0, 2, 4, None, 22),
        Sub("blazer", "Пиджак / жакет", 1.5, 4, 6),
        Sub("overshirt", "Рубашка-куртка", 1.5, 1.5, 4),
        Sub("vest", "Жилет", 1.0, 3, 5),
        Sub("zip_hoodie", "Зип-худи", 2.0, 0.5, 4, None, 23),
    )),
    Category("bottom", "Низ", "👖", (
        Sub("jeans", "Джинсы", 1.5, 1.5, 6),
        Sub("trousers", "Брюки", 1.5, 3, 4),
        Sub("chinos", "Чиносы", 1.5, 2.5, 4),
        Sub("cargo", "Карго", 1.5, 1, 4),
        Sub("joggers", "Джоггеры / спортивки", 1.5, 0.5, 3),
        Sub("shorts", "Шорты", 0.0, 1, 2, 18, None),
        Sub("skirt", "Юбка", 0.5, 2.5, 3),
        Sub("leggings", "Леггинсы", 1.0, 0, 1),
    )),
    Category("dress", "Платье / комбинезон", "👗", (
        Sub("dress", "Платье", 1.0, 3, 2),
        Sub("sundress", "Сарафан", 0.5, 1.5, 2, 18, None),
        Sub("knit_dress", "Трикотажное платье", 2.0, 2, 3, None, 20),
        Sub("jumpsuit", "Комбинезон", 1.5, 2, 2),
    )),
    Category("outer", "Верхняя одежда", "🧣", (
        Sub("coat", "Пальто", 3.0, 3.5, 0, -12, 12),
        Sub("trench", "Тренч", 2.0, 3, 0, 4, 18),
        Sub("puffer", "Пуховик", 4.0, 1, 0, None, 4),
        Sub("jacket", "Куртка", 2.5, 1.5, 0, -5, 16),
        Sub("leather", "Кожаная куртка", 2.0, 2, 0, 4, 18),
        Sub("denim_jacket", "Джинсовка", 1.5, 1, 0, 10, 22),
        Sub("bomber", "Бомбер", 2.0, 1, 0, 4, 19),
        Sub("windbreaker", "Ветровка", 1.0, 0.5, 0, 8, 23),
    )),
    Category("shoes", "Обувь", "👟", (
        Sub("sneakers", "Кеды / кроссовки", 0.3, 1, 0),
        Sub("running", "Спортивные кроссовки", 0.3, 0, 0),
        Sub("boots", "Ботинки", 0.8, 2.5, 0, None, 20),
        Sub("chelsea", "Челси", 0.7, 3, 0, None, 22),
        Sub("loafers", "Лоферы", 0.2, 3, 0),
        Sub("oxfords", "Туфли", 0.2, 4, 0),
        Sub("heels", "Каблуки", 0.1, 3.5, 0),
        Sub("sandals", "Сандалии / шлёпки", 0.0, 0.5, 0, 18, None),
    )),
    Category("bag", "Сумка", "👜", (
        Sub("backpack", "Рюкзак", 0, 1, 0),
        Sub("tote", "Шопер / тоут", 0, 1.5, 0),
        Sub("crossbody", "Через плечо", 0, 2, 0),
        Sub("handbag", "Сумка в руку", 0, 3, 0),
        Sub("clutch", "Клатч", 0, 4, 0),
    )),
    Category("accessory", "Аксессуар", "🧢", (
        Sub("cap", "Кепка", 0.1, 0.5, 0),
        Sub("beanie", "Шапка", 0.6, 1, 0, None, 6),
        Sub("hat", "Шляпа / панама", 0.1, 2, 0),
        Sub("scarf", "Шарф", 0.6, 2, 0, None, 8),
        Sub("belt", "Ремень", 0, 2.5, 0),
        Sub("glasses", "Очки", 0, 2, 0),
        Sub("jewelry", "Украшения", 0, 2.5, 0),
        Sub("watch", "Часы", 0, 2.5, 0),
    )),
)

CATEGORY_BY_KEY = {c.key: c for c in CATEGORIES}
SUB_BY_KEY = {s.key: s for c in CATEGORIES for s in c.subs}
CATEGORY_OF_SUB = {s.key: c.key for c in CATEGORIES for s in c.subs}

# Аксессуары, которые физически носятся на голове — не больше одного на образ.
HEADWEAR = {"cap", "beanie", "hat"}


def is_valid(category: str, sub: str) -> bool:
    return sub in CATEGORY_OF_SUB and CATEGORY_OF_SUB[sub] == category


def label(sub: str) -> str:
    s = SUB_BY_KEY.get(sub)
    return s.name if s else sub


def as_json() -> list[dict]:
    return [
        {
            "key": c.key,
            "name": c.name,
            "emoji": c.emoji,
            "subs": [
                {"key": s.key, "name": s.name, "warmth": s.warmth,
                 "formality": s.formality, "wash": s.wash,
                 "t_min": s.t_min, "t_max": s.t_max}
                for s in c.subs
            ],
        }
        for c in CATEGORIES
    ]
