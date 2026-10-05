"""Работа с цветом: извлечение палитры, названия цветов и правила сочетаемости.

Всё считается в CIE Lab / LCh — это пространство близко к восприятию глазом,
поэтому «похожесть» и «контраст» цветов в нём честнее, чем в RGB/HSV.
Модуль намеренно на чистом Python — без numpy, чтобы его легко тестировать.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

RGB = tuple[int, int, int]


# --------------------------------------------------------------------------- #
# Конвертации
# --------------------------------------------------------------------------- #
def hex_to_rgb(value: str) -> RGB:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def rgb_to_hex(rgb: RGB) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, round(c))) for c in rgb))


def _lin(c: float) -> float:
    c /= 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def rgb_to_lab(rgb: RGB) -> tuple[float, float, float]:
    r, g, b = (_lin(c) for c in rgb)
    x = (r * 0.4124 + g * 0.3576 + b * 0.1805) / 0.95047
    y = r * 0.2126 + g * 0.7152 + b * 0.0722
    z = (r * 0.0193 + g * 0.1192 + b * 0.9505) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def lab_to_lch(lab: tuple[float, float, float]) -> tuple[float, float, float]:
    l, a, b = lab
    return l, math.hypot(a, b), math.degrees(math.atan2(b, a)) % 360


def delta_e(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return math.dist(a, b)


def hue_distance(h1: float, h2: float) -> float:
    d = abs(h1 - h2) % 360
    return 360 - d if d > 180 else d


# --------------------------------------------------------------------------- #
# Именованная палитра «как в магазине одежды»
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class NamedColor:
    key: str
    name: str
    hex: str
    neutral: bool  # «базовый» цвет, который сочетается почти со всем


NAMED_COLORS: tuple[NamedColor, ...] = (
    NamedColor("black", "чёрный", "#1c1c1e", True),
    NamedColor("charcoal", "графитовый", "#3a3b3f", True),
    NamedColor("gray", "серый", "#8e8e93", True),
    NamedColor("light_gray", "светло-серый", "#c7c7cc", True),
    NamedColor("white", "белый", "#f7f7f5", True),
    NamedColor("cream", "молочный", "#efe6d2", True),
    NamedColor("beige", "бежевый", "#d6c2a1", True),
    NamedColor("sand", "песочный", "#c2a878", True),
    NamedColor("camel", "кэмел", "#b5835a", True),
    NamedColor("brown", "коричневый", "#6b4a32", True),
    NamedColor("chocolate", "шоколадный", "#3f2a1e", True),
    NamedColor("khaki", "хаки", "#8a8458", True),
    NamedColor("olive", "оливковый", "#5b5f2e", True),
    NamedColor("navy", "тёмно-синий", "#1f2a44", True),
    NamedColor("denim", "деним", "#4a6382", True),
    NamedColor("light_denim", "светлый деним", "#8aa3bf", True),
    NamedColor("blue", "синий", "#2f5fd0", False),
    NamedColor("sky", "голубой", "#8ec5ea", False),
    NamedColor("teal", "бирюзовый", "#2b8c8c", False),
    NamedColor("green", "зелёный", "#2f8a4c", False),
    NamedColor("forest", "тёмно-зелёный", "#25402e", False),
    NamedColor("mint", "мятный", "#a8dcc1", False),
    NamedColor("lime", "салатовый", "#b5d334", False),
    NamedColor("yellow", "жёлтый", "#f2cd3a", False),
    NamedColor("mustard", "горчичный", "#c99a2e", False),
    NamedColor("orange", "оранжевый", "#ec7a2c", False),
    NamedColor("terracotta", "терракотовый", "#b85a3c", False),
    NamedColor("red", "красный", "#c8302e", False),
    NamedColor("burgundy", "бордовый", "#6b1f2c", False),
    NamedColor("pink", "розовый", "#ee8fb0", False),
    NamedColor("pastel_pink", "пудровый", "#e9c4c4", False),
    NamedColor("fuchsia", "фуксия", "#c8327f", False),
    NamedColor("lavender", "лавандовый", "#b9a7dc", False),
    NamedColor("purple", "фиолетовый", "#5c3a8a", False),
)
_NAMED_LAB = [(c, rgb_to_lab(hex_to_rgb(c.hex))) for c in NAMED_COLORS]
NAMED_BY_KEY = {c.key: c for c in NAMED_COLORS}


def nearest_named(rgb: RGB) -> NamedColor:
    lab = rgb_to_lab(rgb)
    return min(_NAMED_LAB, key=lambda p: delta_e(lab, p[1]))[0]


@dataclass
class ColorInfo:
    hex: str
    share: float = 1.0

    @property
    def rgb(self) -> RGB:
        return hex_to_rgb(self.hex)

    @property
    def lch(self) -> tuple[float, float, float]:
        return lab_to_lch(rgb_to_lab(self.rgb))

    @property
    def named(self) -> NamedColor:
        return nearest_named(self.rgb)

    @property
    def is_neutral(self) -> bool:
        l, c, _ = self.lch
        # Ахроматичные и «магазинные» базовые цвета (деним, кэмел, хаки...) нейтральны.
        return c < 14 or self.named.neutral or l < 18

    def as_dict(self) -> dict:
        n = self.named
        return {"hex": self.hex, "share": round(self.share, 3), "key": n.key,
                "name": n.name, "neutral": self.is_neutral}


# --------------------------------------------------------------------------- #
# Извлечение палитры из пикселей (k-means++ в Lab)
# --------------------------------------------------------------------------- #
def extract_palette(pixels: list[RGB], k: int = 4, seed: int = 7,
                    iterations: int = 12, min_share: float = 0.06) -> list[ColorInfo]:
    """Возвращает доминирующие цвета, отсортированные по доле площади."""
    if not pixels:
        return []
    rnd = random.Random(seed)
    if len(pixels) > 4000:
        pixels = rnd.sample(pixels, 4000)
    labs = [rgb_to_lab(p) for p in pixels]
    k = max(1, min(k, len(set(pixels))))

    # k-means++ инициализация
    centers = [labs[rnd.randrange(len(labs))]]
    while len(centers) < k:
        d2 = [min(delta_e(p, c) ** 2 for c in centers) for p in labs]
        total = sum(d2)
        if total == 0:
            break
        r = rnd.random() * total
        acc = 0.0
        for p, w in zip(labs, d2):
            acc += w
            if acc >= r:
                centers.append(p)
                break

    assign = [0] * len(labs)
    for _ in range(iterations):
        for i, p in enumerate(labs):
            assign[i] = min(range(len(centers)), key=lambda j: delta_e(p, centers[j]))
        new_centers = []
        for j in range(len(centers)):
            members = [labs[i] for i in range(len(labs)) if assign[i] == j]
            if members:
                new_centers.append(tuple(sum(v) / len(members) for v in zip(*members)))
            else:
                new_centers.append(centers[j])
        if new_centers == centers:
            break
        centers = new_centers

    # Средний RGB кластера (а не обратная конвертация Lab) — без ошибок округления.
    sums: dict[int, list[float]] = {}
    for px, j in zip(pixels, assign):
        s = sums.setdefault(j, [0.0, 0.0, 0.0, 0])
        s[0] += px[0]; s[1] += px[1]; s[2] += px[2]; s[3] += 1
    n = len(pixels)
    result = [ColorInfo(rgb_to_hex((s[0] / s[3], s[1] / s[3], s[2] / s[3])), s[3] / n)
              for s in sums.values()]

    # Склеиваем визуально одинаковые кластеры (тени/складки одной ткани).
    result.sort(key=lambda c: -c.share)
    merged: list[ColorInfo] = []
    for c in result:
        lab = rgb_to_lab(c.rgb)
        for m in merged:
            if delta_e(lab, rgb_to_lab(m.rgb)) < 12:
                m.share += c.share
                break
        else:
            merged.append(c)
    merged = [c for c in merged if c.share >= min_share] or merged[:1]
    total = sum(c.share for c in merged)
    for c in merged:
        c.share /= total
    return sorted(merged, key=lambda c: -c.share)


def guess_pattern(palette: list[ColorInfo]) -> bool:
    """Грубая эвристика «вещь с принтом»: несколько контрастных крупных цветов."""
    big = [c for c in palette if c.share >= 0.18]
    if len(big) < 2:
        return False
    labs = [rgb_to_lab(c.rgb) for c in big]
    return any(delta_e(a, b) > 35 for i, a in enumerate(labs) for b in labs[i + 1:])


# --------------------------------------------------------------------------- #
# Сочетаемость
# --------------------------------------------------------------------------- #
@dataclass
class Harmony:
    score: float  # 0..1
    reason: str


def harmony(colors: list[ColorInfo]) -> Harmony:
    """Оценивает сочетание главных цветов вещей образа.

    Правила взяты из классической теории для одежды:
      * база из нейтралов + 0–1 акцент — беспроигрышно;
      * монохром/тон-в-тон и аналоговые цвета — гармонично;
      * комплементарные пары хороши, если один из цветов приглушён;
      * три и более ярких несвязанных цвета — перебор.
    """
    if not colors:
        return Harmony(0.5, "")
    lch = [c.lch for c in colors]
    accents = [(c, v) for c, v in zip(colors, lch) if not c.is_neutral]

    # Контраст по светлоте между вещами — образ не «сливается».
    lights = [v[0] for v in lch]
    spread = max(lights) - min(lights)
    contrast_bonus = 0.08 if spread > 30 else (0.04 if spread > 15 else -0.04)

    if not accents:
        named = {c.named.key for c in colors}
        if len(named) == 1:
            return Harmony(0.8 + contrast_bonus, f"Тотал-лук: {colors[0].named.name}")
        return Harmony(0.78 + contrast_bonus, "Спокойная база из нейтральных цветов")

    if len(accents) == 1:
        name = accents[0][0].named.name
        return Harmony(0.9 + contrast_bonus, f"Нейтральная база и один акцент — {name}")

    hues = [v[2] for _, v in accents]
    chromas = [v[1] for _, v in accents]
    pair_scores = []
    kinds = set()
    for i in range(len(hues)):
        for j in range(i + 1, len(hues)):
            d = hue_distance(hues[i], hues[j])
            muted = min(chromas[i], chromas[j]) < 35
            if d <= 18:
                pair_scores.append(0.95); kinds.add("mono")
            elif d <= 45:
                pair_scores.append(0.85); kinds.add("analog")
            elif d >= 155:
                pair_scores.append(0.8 if muted else 0.62); kinds.add("compl")
            elif 105 <= d <= 135:
                pair_scores.append(0.62); kinds.add("triad")
            else:
                pair_scores.append(0.35 if not muted else 0.5); kinds.add("clash")

    score = sum(pair_scores) / len(pair_scores)
    # Каждый лишний яркий цвет сверх двух — штраф.
    score -= 0.12 * max(0, len(accents) - 2)
    score += contrast_bonus

    if kinds == {"mono"}:
        reason = f"Тон-в-тон: оттенки {accents[0][0].named.name}"
    elif kinds <= {"mono", "analog"}:
        reason = "Аналоговые цвета — мягкий переход оттенков"
    elif "compl" in kinds and "clash" not in kinds:
        reason = "Контрастная пара дополнительных цветов"
    elif "clash" in kinds:
        reason = "Смелое сочетание цветов"
    else:
        reason = "Триада цветов"
    return Harmony(max(0.0, min(1.0, score)), reason)
