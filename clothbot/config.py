from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Минимальная загрузка .env без зависимостей."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass
class Settings:
    bot_token: str
    webapp_url: str
    data_dir: Path
    host: str
    port: int
    rembg_model: str
    default_tz_offset: int  # минуты относительно UTC
    dev_user_id: int | None

    @property
    def db_path(self) -> Path:
        return self.data_dir / "clothbot.sqlite3"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"


def load_settings() -> Settings:
    _load_dotenv(Path(".env"))
    dev = os.environ.get("DEV_USER_ID")
    s = Settings(
        bot_token=os.environ.get("BOT_TOKEN", ""),
        webapp_url=os.environ.get("WEBAPP_URL", "").rstrip("/"),
        data_dir=Path(os.environ.get("DATA_DIR", "data")),
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8080")),
        rembg_model=os.environ.get("REMBG_MODEL", "birefnet-general"),
        default_tz_offset=int(os.environ.get("DEFAULT_TZ_OFFSET", "180")),
        dev_user_id=int(dev) if dev else None,
    )
    s.media_dir.mkdir(parents=True, exist_ok=True)
    return s
