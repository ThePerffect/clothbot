"""HTTP API для Mini App + раздача статики."""
from __future__ import annotations

import logging
from pathlib import Path

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import catalog
from .auth import AuthError, validate_init_data
from .colors import NAMED_COLORS
from .imaging import CutoutError
from .services import Wardrobe

log = logging.getLogger(__name__)
MAX_UPLOAD = 20 * 1024 * 1024
WEBAPP_DIR = Path(__file__).resolve().parent.parent / "webapp"


def item_json(item: dict) -> dict:
    base = f"/media/{item['user_id']}/"
    out = {k: v for k, v in item.items() if k not in ("user_id", "image", "thumb")}
    out["image_url"] = base + item["image"]
    out["thumb_url"] = base + item["thumb"]
    return out


def create_app(wardrobe: Wardrobe, bot_token: str, dev_user_id: int | None = None,
               webapp_dir: Path = WEBAPP_DIR) -> Starlette:
    db = wardrobe.db

    def current_user(request: Request) -> int:
        raw = request.headers.get("x-init-data", "")
        if not raw and dev_user_id:
            db.ensure_user(dev_user_id, "Dev")
            return dev_user_id
        try:
            user = validate_init_data(raw, bot_token)
        except AuthError as e:
            raise HTTPException(401, f"Unauthorized: {e}") from e
        db.ensure_user(int(user["id"]), user.get("first_name"))
        return int(user["id"])

    async def body(request: Request) -> dict:
        try:
            data = await request.json()
        except ValueError as e:
            raise HTTPException(400, "Invalid JSON") from e
        if not isinstance(data, dict):
            raise HTTPException(400, "Expected object")
        return data

    def int_param(value, name: str) -> int:
        try:
            return int(value)
        except (TypeError, ValueError) as e:
            raise HTTPException(400, f"Bad {name}") from e

    # ------------------------------------------------------------------ me
    async def me(request: Request):
        uid = current_user(request)
        user = db.get_user(uid)
        weather = await wardrobe.weather_for(user)
        return JSONResponse({
            "user": user,
            "counts": db.counts(uid),
            "weather": weather.as_dict() if weather else None,
            "weather_summary": weather.summary() if weather else None,
        })

    async def get_catalog(request: Request):
        return JSONResponse({
            "categories": catalog.as_json(),
            "colors": [{"key": c.key, "name": c.name, "hex": c.hex} for c in NAMED_COLORS],
        })

    async def settings(request: Request):
        uid = current_user(request)
        data = await body(request)
        fields = {}
        if "notify_hour" in data:
            h = data["notify_hour"]
            fields["notify_hour"] = None if h is None else int_param(h, "notify_hour") % 24
        if "tz_offset" in data and data["tz_offset"] is not None:
            fields["tz_offset"] = max(-720, min(840, int_param(data["tz_offset"], "tz_offset")))
        if data.get("city"):
            if not wardrobe.weather:
                raise HTTPException(503, "Погода недоступна")
            place = await wardrobe.weather.geocode(str(data["city"])[:80])
            if not place:
                raise HTTPException(404, "Город не найден")
            fields.update(city=place.name, lat=place.lat, lon=place.lon, tz_offset=place.tz_offset)
        elif "city" in data:
            fields.update(city=None, lat=None, lon=None)
        return JSONResponse({"user": db.update_user(uid, **fields)})

    # --------------------------------------------------------------- items
    async def list_items(request: Request):
        uid = current_user(request)
        status = request.query_params.get("status")
        return JSONResponse({"items": [item_json(i) for i in db.list_items(uid, status)]})

    async def create_item(request: Request):
        uid = current_user(request)
        if int(request.headers.get("content-length") or 0) > MAX_UPLOAD:
            raise HTTPException(413, "Файл слишком большой")
        async with request.form(max_files=1) as form:
            upload = form.get("file")
            if upload is None or not hasattr(upload, "read"):
                raise HTTPException(400, "Нет файла")
            data = await upload.read()
            category, sub = form.get("category") or None, form.get("sub") or None
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "Файл слишком большой")
        try:
            item = await wardrobe.add_photo(uid, data, category, sub)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        except CutoutError as e:
            raise HTTPException(422, str(e)) from e
        return JSONResponse({"item": item_json(item)}, status_code=201)

    async def item_detail(request: Request):
        uid = current_user(request)
        item_id = int_param(request.path_params["item_id"], "id")
        if request.method == "DELETE":
            if not wardrobe.delete_item(uid, item_id):
                raise HTTPException(404, "Not found")
            return JSONResponse({"ok": True})
        if request.method == "PATCH":
            try:
                item = db.update_item(uid, item_id, **await body(request))
            except ValueError as e:
                raise HTTPException(400, str(e)) from e
        else:
            item = db.get_item(uid, item_id)
        if not item:
            raise HTTPException(404, "Not found")
        return JSONResponse({"item": item_json(item)})

    async def laundry_bulk(request: Request):
        uid = current_user(request)
        data = await body(request)
        transitions = {"start": ("dirty", "washing"), "finish": ("washing", "clean"),
                       "all_clean": ("dirty", "clean")}
        if data.get("action") not in transitions:
            raise HTTPException(400, "Bad action")
        frm, to = transitions[data["action"]]
        n = db.set_status_bulk(uid, frm, to)
        return JSONResponse({"changed": n, "counts": db.counts(uid)})

    # ------------------------------------------------------------- outfits
    async def suggest(request: Request):
        uid = current_user(request)
        q = request.query_params
        count = max(1, min(10, int_param(q.get("count", 6), "count")))
        lock = int_param(q["lock"], "lock") if q.get("lock") else None
        formality = float(q["formality"]) if q.get("formality") else None
        seed = int_param(q["seed"], "seed") if q.get("seed") else None
        outfits, weather = await wardrobe.suggest(uid, count, lock, formality, seed)
        return JSONResponse({
            "outfits": [{**o.as_dict(), "items": [item_json(i) for i in o.items]}
                        for o in outfits],
            "weather": weather.as_dict() if weather else None,
            "weather_summary": weather.summary() if weather else None,
            "missing": wardrobe.missing_for_outfits(uid),
        })

    async def feedback(request: Request):
        uid = current_user(request)
        data = await body(request)
        if not wardrobe.feedback(uid, int_param(data.get("outfit_id"), "outfit_id"),
                                 str(data.get("verdict"))):
            raise HTTPException(404, "Not found")
        return JSONResponse({"ok": True})

    async def wear(request: Request):
        uid = current_user(request)
        data = await body(request)
        ids = data.get("item_ids")
        if not isinstance(ids, list) or not ids:
            raise HTTPException(400, "item_ids required")
        ids = [int_param(i, "item_id") for i in ids]
        oid = int_param(data["outfit_id"], "outfit_id") if data.get("outfit_id") else None
        if oid is not None and not db.get_outfit(uid, oid):
            raise HTTPException(404, "Not found")
        dirty = wardrobe.wear(uid, ids, oid)
        return JSONResponse({"to_wash": [item_json(i) for i in dirty], "counts": db.counts(uid)})

    async def history(request: Request):
        uid = current_user(request)
        hist = wardrobe.history(uid)
        for h in hist:
            h["items"] = [item_json(i) for i in h["items"]]
            h.pop("user_id", None)
        return JSONResponse({"history": hist})

    async def http_error(request: Request, exc: HTTPException):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    async def root(request: Request):
        return RedirectResponse("/app/")

    routes = [
        Route("/", root),
        Route("/api/me", me),
        Route("/api/catalog", get_catalog),
        Route("/api/settings", settings, methods=["POST"]),
        Route("/api/items", list_items, methods=["GET"]),
        Route("/api/items", create_item, methods=["POST"]),
        Route("/api/items/{item_id}", item_detail, methods=["GET", "PATCH", "DELETE"]),
        Route("/api/laundry", laundry_bulk, methods=["POST"]),
        Route("/api/outfits/suggest", suggest),
        Route("/api/outfits/feedback", feedback, methods=["POST"]),
        Route("/api/outfits/wear", wear, methods=["POST"]),
        Route("/api/outfits/history", history),
        Mount("/media", StaticFiles(directory=str(wardrobe.media_dir), check_dir=False),
              name="media"),
        Mount("/app", StaticFiles(directory=str(webapp_dir), html=True, check_dir=False),
              name="app"),
    ]
    return Starlette(routes=routes, exception_handlers={HTTPException: http_error})
