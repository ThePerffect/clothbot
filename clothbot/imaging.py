"""Вырезка одежды с фото и сборка коллажей.

Фон удаляется нейросетью BiRefNet (через rembg) — на сегодня одна из самых
точных открытых моделей сегментации: аккуратно держит кружево, мех, шнурки
и тонкие лямки. Дальше маска чистится:

* выкидываем мусорные островки (оставляем крупные компоненты — например,
  обе кроссовки из пары);
* закрываем дырки-«пинхолы» внутри вещи;
* слегка сжимаем и растушёвываем край, чтобы убрать ореол фона;
* убираем цветную кайму (defringe) — края берут цвет вещи, а не фона;
* обрезаем по контуру, центрируем на квадрате с полями.

Тяжёлые зависимости (rembg, onnxruntime, opencv) импортируются лениво.
"""
from __future__ import annotations

import io
import logging
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path

from .colors import extract_palette, guess_pattern

log = logging.getLogger(__name__)

MAX_INPUT_SIDE = 2048
OUTPUT_SIDE = 1024
THUMB_SIDE = 384
PADDING = 0.06


class CutoutError(Exception):
    pass


@dataclass
class ProcessedImage:
    image: str        # имя файла относительно media/<user>/
    thumb: str
    colors: list[dict]
    pattern: bool


class Segmenter:
    """Обёртка над rembg с одной сессией модели на процесс."""

    def __init__(self, model: str = "birefnet-general"):
        self.model = model
        self._session = None
        self._lock = threading.Lock()

    def _get_session(self):
        if self._session is None:
            from rembg import new_session
            log.info("Загружаю модель сегментации %s…", self.model)
            self._session = new_session(self.model)
        return self._session

    def warmup(self) -> None:
        with self._lock:
            self._get_session()

    def mask(self, img):
        """Возвращает маску L (0..255) для RGB-изображения PIL."""
        from rembg import remove
        with self._lock:  # onnx-сессия не любит параллельные вызовы на CPU
            return remove(img, session=self._get_session(), only_mask=True,
                          post_process_mask=False)


def _refine_mask(mask):
    import cv2
    import numpy as np

    m = np.array(mask, dtype=np.uint8)
    hard = (m > 127).astype(np.uint8)
    if hard.sum() < m.size * 0.005:
        raise CutoutError("Не нашёл на фото вещь. Попробуйте снять её крупнее на контрастном фоне.")

    # Крупные компоненты связности (пара обуви = две компоненты).
    _, labels, stats, _ = cv2.connectedComponentsWithStats(hard, connectivity=8)
    areas = stats[:, cv2.CC_STAT_AREA].copy()
    areas[0] = 0  # фон
    keep_lut = areas >= areas.max() * 0.08
    keep = keep_lut[labels].astype(np.uint8)

    # Заливаем маленькие дырки внутри вещи (крупные — например, проймы — остаются).
    inv = (1 - keep).astype(np.uint8)
    _, labels2, stats2, _ = cv2.connectedComponentsWithStats(inv, connectivity=4)
    h, w = keep.shape
    x, y = stats2[:, cv2.CC_STAT_LEFT], stats2[:, cv2.CC_STAT_TOP]
    bw, bh = stats2[:, cv2.CC_STAT_WIDTH], stats2[:, cv2.CC_STAT_HEIGHT]
    touches_border = (x == 0) | (y == 0) | (x + bw >= w) | (y + bh >= h)
    hole_lut = ~touches_border & (stats2[:, cv2.CC_STAT_AREA] < keep.sum() * 0.002)
    hole_lut[0] = False  # метка 0 — сама вещь
    holes = hole_lut[labels2]
    keep[holes] = 1

    # Мягкая маска модели только внутри выбранных компонент + лёгкая эрозия края.
    region = cv2.dilate(keep, np.ones((5, 5), np.uint8))
    soft = (m.astype(np.float32) / 255.0) * region
    soft[holes] = 1.0
    soft = cv2.erode(soft, np.ones((3, 3), np.uint8), iterations=1)
    soft = cv2.GaussianBlur(soft, (0, 0), sigmaX=0.8)
    soft[soft < 0.04] = 0
    return soft


def _defringe(rgb, alpha):
    """Заменяет цвет полупрозрачных краёв на цвет ближайших «уверенных» пикселей."""
    import cv2
    import numpy as np

    solid = (alpha > 0.9).astype(np.uint8)
    if solid.sum() == 0:
        return rgb
    # Размываем только «уверенные» пиксели и нормируем — получаем цвет вещи у края.
    weight = cv2.GaussianBlur(solid.astype(np.float32), (0, 0), 3)
    color = cv2.GaussianBlur(rgb.astype(np.float32) * solid[..., None], (0, 0), 3)
    inner = color / np.maximum(weight[..., None], 1e-4)
    edge = ((alpha > 0) & (alpha <= 0.9))[..., None]
    out = np.where(edge, inner, rgb.astype(np.float32))
    return np.clip(out, 0, 255).astype(np.uint8)


def cutout(data: bytes, segmenter: Segmenter):
    """Байты фото → RGBA PIL-изображение вещи, обрезанное и выровненное."""
    import numpy as np
    from PIL import Image, ImageOps

    try:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img).convert("RGB")
    except Exception as e:  # noqa: BLE001
        raise CutoutError("Не получилось открыть изображение") from e
    img.thumbnail((MAX_INPUT_SIDE, MAX_INPUT_SIDE), Image.LANCZOS)

    mask = segmenter.mask(img)
    alpha = _refine_mask(mask)
    rgb = _defringe(np.array(img), alpha)
    rgba = np.dstack([rgb, (alpha * 255).astype(np.uint8)])
    out = Image.fromarray(rgba)

    bbox = out.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
    if not bbox:
        raise CutoutError("Не нашёл на фото вещь")
    out = out.crop(bbox)

    side = int(max(out.size) * (1 + PADDING * 2))
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(out, ((side - out.width) // 2, (side - out.height) // 2))
    if side > OUTPUT_SIDE:
        canvas = canvas.resize((OUTPUT_SIDE, OUTPUT_SIDE), Image.LANCZOS)
    return canvas


def palette_of(rgba) -> tuple[list[dict], bool]:
    from PIL import Image

    small = rgba.copy()
    small.thumbnail((96, 96), Image.LANCZOS)
    px = [(r, g, b) for r, g, b, a in small.getdata() if a > 200]
    pal = extract_palette(px, k=5)
    return [c.as_dict() for c in pal], guess_pattern(pal)


def process_upload(data: bytes, user_dir: Path, segmenter: Segmenter) -> ProcessedImage:
    from PIL import Image

    rgba = cutout(data, segmenter)
    colors, pattern = palette_of(rgba)
    user_dir.mkdir(parents=True, exist_ok=True)
    name = uuid.uuid4().hex
    image_name, thumb_name = f"{name}.webp", f"{name}_t.webp"
    rgba.save(user_dir / image_name, "WEBP", quality=92, method=6)
    thumb = rgba.copy()
    thumb.thumbnail((THUMB_SIDE, THUMB_SIDE), Image.LANCZOS)
    thumb.save(user_dir / thumb_name, "WEBP", quality=88, method=6)
    return ProcessedImage(image_name, thumb_name, colors, pattern)


# --------------------------------------------------------------------------- #
# Коллаж образа для чата
# --------------------------------------------------------------------------- #
MAIN_SLOTS = {"top": 0.36, "dress": 0.62, "bottom": 0.40, "shoes": 0.24}
SIDE_ORDER = ("outer", "layer", "bag", "accessory")


def collage(paths_by_category: list[tuple[str, Path]], bg: str = "#f3efe8") -> bytes:
    """Флэт-лэй: основная колонна (верх → низ → обувь) и боковая (слои, сумка, аксессуары)."""
    from PIL import Image, ImageFilter

    W, H, M = 1080, 1350, 48
    canvas = Image.new("RGB", (W, H), bg)
    main = [(c, p) for c, p in paths_by_category if c in MAIN_SLOTS]
    side = [(c, p) for c, p in paths_by_category if c in SIDE_ORDER]
    side.sort(key=lambda cp: SIDE_ORDER.index(cp[0]))

    main_w = int((W - 2 * M) * (0.6 if side else 1.0))
    side_w = W - 2 * M - main_w - (M // 2 if side else 0)

    def place(path: Path, box: tuple[int, int, int, int]) -> None:
        x, y, bw, bh = box
        try:
            im = Image.open(path).convert("RGBA")
        except Exception:  # noqa: BLE001
            return
        bbox = im.getchannel("A").getbbox()
        if bbox:
            im = im.crop(bbox)
        im.thumbnail((bw, bh), Image.LANCZOS)
        px, py = x + (bw - im.width) // 2, y + (bh - im.height) // 2
        shadow = Image.new("RGBA", im.size, (40, 30, 20, 0))
        shadow.putalpha(im.getchannel("A").point(lambda v: int(v * 0.22)))
        shadow = shadow.filter(ImageFilter.GaussianBlur(14))
        canvas.paste(shadow, (px + 6, py + 14), shadow)
        canvas.paste(im, (px, py), im)

    if main:
        total = sum(MAIN_SLOTS[c] for c, _ in main)
        y = M
        avail = H - 2 * M - (len(main) - 1) * 16
        for c, p in main:
            h = int(avail * MAIN_SLOTS[c] / total)
            place(p, (M, y, main_w, h))
            y += h + 16
    if side:
        n = len(side)
        h = (H - 2 * M - (n - 1) * 16) // n
        x = M + main_w + M // 2
        for i, (_, p) in enumerate(side):
            place(p, (x, M + i * (h + 16), side_w, h))

    buf = io.BytesIO()
    canvas.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def preview_jpeg(path: Path, bg: str = "#f3efe8") -> bytes:
    """Вырезанная вещь на мягком фоне — для отправки в чат."""
    from PIL import Image

    im = Image.open(path).convert("RGBA")
    canvas = Image.new("RGB", im.size, bg)
    canvas.paste(im, (0, 0), im)
    buf = io.BytesIO()
    canvas.save(buf, "JPEG", quality=92)
    return buf.getvalue()
