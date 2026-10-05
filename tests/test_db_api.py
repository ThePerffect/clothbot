import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path

from starlette.testclient import TestClient

from clothbot.api import create_app
from clothbot.auth import AuthError, sign_init_data, validate_init_data
from clothbot.db import Database
from clothbot.services import Wardrobe

from .helpers import TODAY, fake_processor

TOKEN = "123:ABC"

try:
    import multipart  # noqa: F401
    HAS_MULTIPART = True
except ImportError:
    HAS_MULTIPART = False


def init_data(uid=42, name="Аня", auth_date=None):
    return sign_init_data({
        "auth_date": str(int(auth_date or time.time())),
        "query_id": "AAA",
        "user": json.dumps({"id": uid, "first_name": name}, ensure_ascii=False),
    }, TOKEN)


class AuthTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(validate_init_data(init_data(), TOKEN)["id"], 42)

    def test_tampered(self):
        bad = init_data().replace("42", "43")
        with self.assertRaises(AuthError):
            validate_init_data(bad, TOKEN)

    def test_wrong_token(self):
        with self.assertRaises(AuthError):
            validate_init_data(init_data(), "999:XYZ")

    def test_expired(self):
        with self.assertRaises(AuthError):
            validate_init_data(init_data(auth_date=time.time() - 86400 * 30), TOKEN)


class DbTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "t.db")
        self.db.ensure_user(1, "U")

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, sub=None, cat=None):
        return self.db.add_item(1, image="a.webp", thumb="a_t.webp", colors=[], pattern=False,
                                category=cat, sub=sub)

    def test_draft_then_categorize(self):
        it = self.add()
        self.assertEqual(it["status"], "draft")
        self.assertEqual(self.db.list_items(1), [])
        it = self.db.update_item(1, it["id"], category="top", sub="tshirt")
        self.assertEqual(it["status"], "clean")
        self.assertEqual(it["wash_after"], 1)

    def test_invalid_category(self):
        it = self.add()
        with self.assertRaises(ValueError):
            self.db.update_item(1, it["id"], category="shoes", sub="tshirt")
        with self.assertRaises(ValueError):
            self.db.update_item(1, it["id"], status="dirty")  # черновик без категории

    def test_wear_makes_dirty(self):
        tee = self.add("tshirt", "top")
        jeans = self.add("jeans", "bottom")
        dirty = self.db.wear(1, [tee["id"], jeans["id"]], TODAY)
        self.assertEqual([d["id"] for d in dirty], [tee["id"]])
        self.assertEqual(self.db.get_item(1, tee["id"])["status"], "dirty")
        j = self.db.get_item(1, jeans["id"])
        self.assertEqual((j["status"], j["wears_since_wash"], j["times_worn"]), ("clean", 1, 1))
        self.assertEqual(j["last_worn"], TODAY.isoformat())

    def test_laundry_cycle(self):
        tee = self.add("tshirt", "top")
        self.db.wear(1, [tee["id"]], TODAY)
        self.assertEqual(self.db.set_status_bulk(1, "dirty", "washing"), 1)
        self.db.set_status_bulk(1, "washing", "clean")
        t = self.db.get_item(1, tee["id"])
        self.assertEqual((t["status"], t["wears_since_wash"]), ("clean", 0))

    def test_stylist_items_only_clean(self):
        a = self.add("tshirt", "top")
        self.add("jeans", "bottom")
        self.add()
        self.db.update_item(1, a["id"], status="dirty")
        self.assertEqual([i.sub for i in self.db.stylist_items(1)], ["jeans"])
        self.assertEqual(len(self.db.stylist_items(1, include_ids={a["id"]})), 2)

    def test_isolation_between_users(self):
        it = self.add("tshirt", "top")
        self.db.ensure_user(2)
        self.assertIsNone(self.db.get_item(2, it["id"]))
        self.assertIsNone(self.db.update_item(2, it["id"], status="dirty"))

    def test_pair_weights_clamped(self):
        for _ in range(10):
            self.db.add_pair_weights(1, {(1, 2): 1.0})
        self.assertEqual(self.db.pair_weights(1)[(1, 2)], 5)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db = Database(root / "t.db")
        (root / "media").mkdir()
        self.w = Wardrobe(self.db, root / "media", fake_processor())
        self.client = TestClient(create_app(self.w, TOKEN))
        self.h = {"X-Init-Data": init_data()}

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()

    def seed(self):
        for sub, cat, color in [("tshirt", "top", "#f7f7f5"), ("jeans", "bottom", "#4a6382"),
                                ("sneakers", "shoes", "#f7f7f5"), ("shirt", "top", "#8ec5ea")]:
            self.db.ensure_user(42)
            self.db.add_item(42, image="x.webp", thumb="x_t.webp", pattern=False,
                             colors=[{"hex": color, "share": 1}], category=cat, sub=sub)

    def test_unauthorized(self):
        self.assertEqual(self.client.get("/api/me").status_code, 401)
        r = self.client.get("/api/me", headers={"X-Init-Data": "user=1&hash=00"})
        self.assertEqual(r.status_code, 401)

    def test_me_and_catalog(self):
        r = self.client.get("/api/me", headers=self.h)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["user"]["first_name"], "Аня")
        cat = self.client.get("/api/catalog").json()
        self.assertIn("top", [c["key"] for c in cat["categories"]])

    def test_items_crud_and_outfits(self):
        self.seed()
        items = self.client.get("/api/items", headers=self.h).json()["items"]
        self.assertEqual(len(items), 4)
        self.assertTrue(items[0]["image_url"].startswith("/media/42/"))
        self.assertNotIn("user_id", items[0])

        tid = next(i["id"] for i in items if i["sub"] == "tshirt")
        r = self.client.patch(f"/api/items/{tid}", headers=self.h, json={"favorite": True})
        self.assertTrue(r.json()["item"]["favorite"])
        r = self.client.patch(f"/api/items/{tid}", headers=self.h, json={"sub": "jeans"})
        self.assertEqual(r.status_code, 400)

        r = self.client.get("/api/outfits/suggest?count=3", headers=self.h).json()
        self.assertTrue(r["outfits"])
        self.assertEqual(r["missing"], [])
        o = r["outfits"][0]
        self.assertEqual(len(o["items"]), 3)

        r = self.client.post("/api/outfits/feedback", headers=self.h,
                             json={"outfit_id": o["id"], "verdict": "like"})
        self.assertEqual(r.status_code, 200)
        r = self.client.post("/api/outfits/wear", headers=self.h,
                             json={"outfit_id": o["id"], "item_ids": [i["id"] for i in o["items"]]})
        self.assertEqual(r.status_code, 200)
        expected = sum(1 for i in o["items"] if i["wash_after"] == 1)  # футболка — после 1 носки
        self.assertEqual(len(r.json()["to_wash"]), expected)
        hist = self.client.get("/api/outfits/history", headers=self.h).json()["history"]
        self.assertEqual(hist[0]["id"], o["id"])

        dirty = self.client.get("/api/items?status=dirty", headers=self.h).json()["items"]
        self.assertEqual(len(dirty), r.json()["counts"]["dirty"])
        r = self.client.post("/api/laundry", headers=self.h, json={"action": "start"})
        self.assertEqual(r.json()["counts"]["washing"], len(dirty))
        r = self.client.post("/api/laundry", headers=self.h, json={"action": "finish"})
        self.assertEqual(r.json()["counts"]["dirty"] + r.json()["counts"]["washing"], 0)

        r = self.client.delete(f"/api/items/{tid}", headers=self.h)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get(f"/api/items/{tid}", headers=self.h).status_code, 404)

    def test_other_user_cannot_touch(self):
        self.seed()
        tid = self.db.list_items(42)[0]["id"]
        h2 = {"X-Init-Data": init_data(uid=7)}
        self.assertEqual(self.client.get(f"/api/items/{tid}", headers=h2).status_code, 404)
        self.assertEqual(self.client.delete(f"/api/items/{tid}", headers=h2).status_code, 404)
        r = self.client.post("/api/outfits/wear", headers=h2, json={"item_ids": [tid]})
        self.assertEqual(self.db.get_item(42, tid)["times_worn"], 0)

    def test_missing_pieces(self):
        r = self.client.get("/api/outfits/suggest", headers=self.h).json()
        self.assertEqual(r["outfits"], [])
        self.assertEqual(set(r["missing"]), {"верх", "низ"})

    @unittest.skipUnless(HAS_MULTIPART, "python-multipart не установлен")
    def test_upload(self):
        r = self.client.post("/api/items", headers=self.h, files={"file": ("a.jpg", b"img")},
                             data={"category": "top", "sub": "tshirt"})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()["item"]["status"], "clean")
        r = self.client.post("/api/items", headers=self.h, files={"file": ("a.jpg", b"img")},
                             data={"category": "top", "sub": "jeans"})
        self.assertEqual(r.status_code, 400)

    def test_add_photo_service(self):
        item = asyncio.run(self.w.add_photo(42, b"img"))
        self.assertEqual(item["status"], "draft")
        self.assertTrue(self.w.image_path(item).exists())
        self.assertTrue(self.w.delete_item(42, item["id"]))
        self.assertFalse(self.w.image_path(item).exists())


if __name__ == "__main__":
    unittest.main()
