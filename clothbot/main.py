"""Точка входа: API + Mini App + чат-бот в одном процессе."""
from __future__ import annotations

import asyncio
import logging
from functools import partial

import uvicorn

from .api import create_app
from .bot import Bot
from .config import load_settings
from .db import Database
from .imaging import Segmenter, process_upload
from .services import Wardrobe
from .tg import Telegram
from .weather import WeatherClient

log = logging.getLogger("clothbot")


async def amain() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s = load_settings()
    if not s.bot_token:
        raise SystemExit("Укажите BOT_TOKEN в .env")

    segmenter = Segmenter(s.rembg_model)
    wardrobe = Wardrobe(Database(s.db_path, s.default_tz_offset), s.media_dir,
                        partial(_process, segmenter=segmenter), WeatherClient())
    app = create_app(wardrobe, s.bot_token, s.dev_user_id)
    bot = Bot(Telegram(s.bot_token), wardrobe, s.webapp_url)

    # Модель весит ~900 МБ: прогреваем в фоне, чтобы первая вещь не ждала загрузку.
    asyncio.get_running_loop().run_in_executor(None, segmenter.warmup)
    try:
        await bot.setup()
    except Exception:  # noqa: BLE001
        log.warning("Не удалось настроить команды/меню бота", exc_info=True)
    if not s.webapp_url:
        log.warning("WEBAPP_URL не задан — Mini App не будет доступна из Telegram")

    server = uvicorn.Server(uvicorn.Config(app, host=s.host, port=s.port, log_level="info"))
    await asyncio.gather(server.serve(), bot.run_polling(), bot.run_morning())


def _process(data, user_dir, segmenter):
    return process_upload(data, user_dir, segmenter)


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
