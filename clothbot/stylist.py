"""Генератор образов.

Подход — поиск по лучу (beam search) с явной функцией оценки:

1. Составляем базы: «верх + низ» или «платье/комбинезон».
2. К лучшим базам подбираем обувь, затем слой (кардиган/пиджак)
   и верхнюю одежду по погоде, затем жадно добавляем сумку и аксессуары,
   только если они улучшают оценку.
3. Из лучших кандидатов выбираем разнообразный набор (MMR), чтобы
   варианты не отличались одной парой носков.

Оценка складывается из сочетаемости цветов, единства стиля (формальности),
соответствия погоде, правила «не больше одного принта», свежести
(давно не надевали — плюс, вчера надевали — минус) и личных лайков/дизлайков.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable

from . import catalog
from .colors import ColorInfo, harmony

FORMALITY_NAMES = ["спорт", "кэжуал", "смарт-кэжуал", "бизнес", "формал"]
CLOTHING = ("top", "layer", "bottom", "dress", "outer", "shoes")
SLOT_ORDER = ("outer", "layer", "top", "dress", "bottom", "shoes", "bag", "accessory")


@dataclass
class Item:
    id: int
    category: str
    sub: str
    colors: list[ColorInfo] = field(default_factory=list)
    pattern: bool = False
    times_worn: int = 0
    last_worn: date | None = None
    favorite: bool = False

    @property
    def spec(self) -> catalog.Sub:
        return catalog.SUB_BY_KEY[self.sub]

    @property
    def main_color(self) -> ColorInfo | None:
        return self.colors[0] if self.colors else None


@dataclass
class Context:
    today: date
    temperature: float | None = None  # «ощущается как», °C
    precipitation: bool = False
    formality: float | None = None    # желаемый уровень 0..4
    lock_item: int | None = None      # «собери образ вокруг этой вещи»
    pair_weights: dict[tuple[int, int], float] = field(default_factory=dict)
    disliked: set[frozenset[int]] = field(default_factory=set)
    seed: int = 0


@dataclass
class Outfit:
    items: list[Item]
    score: float
    reasons: list[str]

    @property
    def ids(self) -> list[int]:
        return [i.id for i in self.items]


def warmth_target(temp: float | None) -> tuple[float, float] | None:
    if temp is None:
        return None
    if temp >= 26:
        return 1.0, 2.8
    if temp >= 20:
        return 1.5, 3.6
    if temp >= 14:
        return 2.5, 5.2
    if temp >= 8:
        return 4.0, 7.5
    if temp >= 0:
        return 5.5, 9.0
    return 7.0, 12.0


def _pair(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


# --------------------------------------------------------------------------- #
# Оценка
# --------------------------------------------------------------------------- #
def score_outfit(items: list[Item], ctx: Context, *, final: bool = False) -> tuple[float, list[str]]:
    reasons: list[str] = []
    cats = [i.category for i in items]
    subs = {i.sub for i in items}
    clothing = [i for i in items if i.category in CLOTHING]
    temp = ctx.temperature

    # 1. Цвет: гармония главных цветов одежды и обуви.
    main = [i.main_color for i in clothing if i.main_color]
    h = harmony(main)
    score = h.score
    if h.reason:
        reasons.append(h.reason)

    # Сумка и аксессуары должны быть нейтральными или повторять цвет образа.
    for acc in (i for i in items if i.category in ("bag", "accessory")):
        c = acc.main_color
        if not c or c.is_neutral:
            score += 0.03 if acc.category == "bag" else 0.005
        elif any(m.named.key == c.named.key for m in main):
            score += 0.05
            reasons.append(f"{catalog.label(acc.sub)} перекликается по цвету")
        else:
            score -= 0.08

    # 2. Принты: максимум один.
    patterned = sum(1 for i in clothing if i.pattern)
    if patterned > 1:
        score -= 0.25 * (patterned - 1)

    # 3. Стиль: близкая формальность вещей.
    forms = [i.spec.formality for i in clothing]
    if forms:
        spread = max(forms) - min(forms)
        score -= max(0.0, spread - 1.5) * 0.15
        mean = sum(forms) / len(forms)
        if ctx.formality is not None:
            score -= abs(mean - ctx.formality) * 0.12
        if spread <= 1.5 and len(forms) >= 3:
            reasons.append(f"Единый стиль: {FORMALITY_NAMES[round(min(4, mean))]}")

    # 4. Конструкция образа.
    top = next((i for i in items if i.category == "top"), None)
    layer = next((i for i in items if i.category == "layer"), None)
    if layer and top and top.spec.warmth >= 2.5 and layer.sub != "blazer":
        score -= 0.2  # кардиган поверх худи — тяжело
    if layer and top and layer.sub == "blazer" and top.sub in ("hoodie", "tank"):
        score -= 0.1
    if layer and layer.main_color and top and top.main_color:
        if layer.main_color.named.key == top.main_color.named.key:
            score -= 0.05  # слой должен читаться отдельно
    if sum(1 for i in items if i.sub in catalog.HEADWEAR) > 1:
        score -= 1.0
    if cats.count("accessory") > 2 or cats.count("bag") > 1:
        score -= 1.0

    # 5. Погода.
    has_outer = "outer" in cats
    if temp is not None:
        lo, hi = warmth_target(temp)  # type: ignore[misc]
        warmth = sum(i.spec.warmth for i in items)
        if final or has_outer:
            if warmth < lo:
                score -= (lo - warmth) * 0.12
            elif warmth > hi:
                score -= (warmth - hi) * 0.12
        if temp < 14 and final and not has_outer:
            score -= 0.6
        if temp > 22 and has_outer:
            score -= 0.4
        # Каждая вещь — в своём диапазоне температур.
        off = 0.0
        for i in items:
            sp = i.spec
            if sp.t_max is not None and temp > sp.t_max:
                off += temp - sp.t_max
            if sp.t_min is not None and temp < sp.t_min:
                off += sp.t_min - temp
        score -= min(1.0, off * 0.1)
        if temp < 6 and subs & {"beanie", "scarf"}:
            score += 0.04
        if ctx.precipitation and subs & {"sandals", "loafers", "oxfords", "heels"}:
            score -= 0.15
        if ctx.precipitation and subs & {"windbreaker", "trench"}:
            score += 0.05
    elif has_outer:
        score -= 0.05

    # 6. Свежесть и ротация гардероба.
    for i in items:
        if i.last_worn:
            days = (ctx.today - i.last_worn).days
            if days <= 1:
                score -= 0.15
            elif days <= 3:
                score -= 0.06
            elif days >= 21 and i.category in CLOTHING:
                score += 0.03
        if i.times_worn == 0 and i.category in CLOTHING:
            score += 0.02
        if i.favorite:
            score += 0.03

    # 7. Личные предпочтения (лайки/дизлайки сочетаний).
    ids = [i.id for i in items]
    fb = 0.0
    for a in range(len(ids)):
        for b in range(a + 1, len(ids)):
            fb += ctx.pair_weights.get(_pair(ids[a], ids[b]), 0.0)
    score += max(-0.5, min(0.3, fb * 0.08))
    if frozenset(ids) in ctx.disliked:
        score -= 2.0

    return score, reasons


# --------------------------------------------------------------------------- #
# Поиск
# --------------------------------------------------------------------------- #
def _usable(item: Item, ctx: Context) -> bool:
    return item.sub in catalog.SUB_BY_KEY


def generate(items: Iterable[Item], ctx: Context, count: int = 5,
             beam: int = 60) -> list[Outfit]:
    rnd = random.Random(ctx.seed)
    pool: dict[str, list[Item]] = {k: [] for k in catalog.CATEGORY_BY_KEY}
    for it in items:
        if _usable(it, ctx):
            pool[it.category].append(it)

    locked = None
    if ctx.lock_item is not None:
        locked = next((i for lst in pool.values() for i in lst if i.id == ctx.lock_item), None)
        if locked:
            pool[locked.category] = [locked]
            if locked.category == "dress":
                pool["top"], pool["bottom"] = [], []
            elif locked.category in ("top", "bottom"):
                pool["dress"] = []

    def noisy(items_: list[Item], *, final: bool = False) -> float:
        return score_outfit(items_, ctx, final=final)[0] + rnd.uniform(0, 0.06)

    def top_k(cands: list[list[Item]], k: int, final: bool = False) -> list[list[Item]]:
        scored = sorted(((noisy(c, final=final), c) for c in cands), key=lambda p: -p[0])
        return [c for _, c in scored[:k]]

    # 1. Базы
    bases: list[list[Item]] = [[t, b] for t in pool["top"] for b in pool["bottom"]]
    bases += [[d] for d in pool["dress"]]
    if not bases:
        return []
    bases = top_k(bases, beam)

    # 2. Обувь (без обуви образ тоже возможен — гардероб может быть неполным)
    if pool["shoes"]:
        bases = top_k([b + [s] for b in bases for s in pool["shoes"]], beam)

    # 3. Слой и верхняя одежда
    expanded = []
    for b in bases:
        layer_opts: list[Item | None] = [None]
        if any(i.category in ("top", "dress") for i in b):
            layer_opts += pool["layer"]
        for lay in layer_opts:
            for out in [None, *pool["outer"]]:
                expanded.append(b + [x for x in (lay, out) if x is not None])
    bases = top_k(expanded, beam, final=True)

    # 4. Сумка и аксессуары — жадно, только если становится лучше.
    finished = []
    for b in bases:
        cur = b
        cur_score = score_outfit(cur, ctx, final=True)[0]
        for cat in ("bag", "accessory", "accessory"):
            best, best_score = None, cur_score
            for x in pool[cat]:
                if x in cur:
                    continue
                s = score_outfit(cur + [x], ctx, final=True)[0]
                if s > best_score + 0.01:
                    best, best_score = x, s
            if best:
                cur, cur_score = cur + [best], best_score
        finished.append(cur)

    # Дедупликация и финальная оценка
    seen: set[frozenset[int]] = set()
    scored: list[Outfit] = []
    for c in finished:
        key = frozenset(i.id for i in c)
        if key in seen:
            continue
        seen.add(key)
        s, reasons = score_outfit(c, ctx, final=True)
        c_sorted = sorted(c, key=lambda i: SLOT_ORDER.index(i.category))
        scored.append(Outfit(c_sorted, s + rnd.uniform(0, 0.04), reasons))
    scored.sort(key=lambda o: -o.score)

    # 5. Разнообразие (Maximal Marginal Relevance)
    chosen: list[Outfit] = []
    while scored and len(chosen) < count:
        def mmr(o: Outfit) -> float:
            if not chosen:
                return o.score
            overlap = max(len(set(o.ids) & set(c.ids)) / len(o.ids) for c in chosen)
            return o.score - 0.45 * overlap
        best = max(scored, key=mmr)
        chosen.append(best)
        scored.remove(best)

    for o in chosen:
        _add_context_reasons(o, ctx)
    return chosen


def _add_context_reasons(o: Outfit, ctx: Context) -> None:
    if ctx.temperature is not None:
        t = round(ctx.temperature)
        sign = "+" if t > 0 else ""
        if any(i.category == "outer" for i in o.items):
            o.reasons.append(f"По погоде {sign}{t}°: с верхней одеждой")
        else:
            o.reasons.append(f"По погоде {sign}{t}°")
    stale = [i for i in o.items if i.last_worn and (ctx.today - i.last_worn).days >= 21]
    if stale:
        o.reasons.append(f"Давно не носили: {catalog.label(stale[0].sub).lower()}")
    # Убираем повторы, сохраняя порядок
    seen = set()
    o.reasons = [r for r in o.reasons if not (r in seen or seen.add(r))]


def feedback_updates(item_ids: list[int], verdict: str) -> dict[tuple[int, int], float]:
    """Как меняются веса пар вещей после реакции пользователя."""
    delta = {"like": 1.0, "wear": 0.5, "dislike": -1.5}[verdict]
    ids = sorted(set(item_ids))
    return {(a, b): delta for i, a in enumerate(ids) for b in ids[i + 1:]}
