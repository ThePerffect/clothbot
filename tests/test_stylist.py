import unittest
from datetime import timedelta

from clothbot import stylist
from clothbot.stylist import Context, generate, score_outfit

from .helpers import TODAY, item, wardrobe_items


def cats(outfit):
    return {i.category for i in outfit.items}


def subs(outfit):
    return {i.sub for i in outfit.items}


class StylistTests(unittest.TestCase):
    def test_structure_is_complete(self):
        outs = generate(wardrobe_items(), Context(today=TODAY, temperature=18), count=5)
        self.assertEqual(len(outs), 5)
        for o in outs:
            c = cats(o)
            self.assertTrue({"top", "bottom"} <= c or "dress" in c)
            self.assertFalse("dress" in c and ("top" in c or "bottom" in c))
            self.assertIn("shoes", c)
            self.assertTrue(o.reasons)

    def test_cold_weather_needs_outerwear(self):
        outs = generate(wardrobe_items(), Context(today=TODAY, temperature=-8), count=4)
        for o in outs:
            self.assertIn("outer", cats(o))
            self.assertFalse(subs(o) & {"shorts", "sandals"})
            self.assertGreaterEqual(sum(i.spec.warmth for i in o.items), 7)

    def test_hot_weather_no_coat(self):
        outs = generate(wardrobe_items(), Context(today=TODAY, temperature=29), count=4)
        for o in outs:
            self.assertNotIn("outer", cats(o))
            self.assertFalse(subs(o) & {"sweater", "hoodie", "beanie", "puffer", "chelsea"})

    def test_avoids_color_clash(self):
        ctx = Context(today=TODAY, temperature=18)
        outs = generate(wardrobe_items(), ctx, count=6)
        for o in outs:
            self.assertFalse({5, 9} <= set(o.ids), "оранжевое худи + фуксия — перебор")

    def test_lock_item(self):
        outs = generate(wardrobe_items(), Context(today=TODAY, temperature=15, lock_item=4), count=3)
        self.assertTrue(outs)
        for o in outs:
            self.assertIn(4, o.ids)

    def test_lock_dress(self):
        outs = generate(wardrobe_items(), Context(today=TODAY, temperature=22, lock_item=17), count=2)
        for o in outs:
            self.assertIn(17, o.ids)
            self.assertNotIn("bottom", cats(o))

    def test_diversity(self):
        outs = generate(wardrobe_items(), Context(today=TODAY, temperature=16), count=4)
        keys = {frozenset(o.ids) for o in outs}
        self.assertEqual(len(keys), 4)

    def test_recently_worn_penalized(self):
        a = [item("tshirt", "#f7f7f5", 1), item("jeans", "#4a6382", 2),
             item("sneakers", "#f7f7f5", 3)]
        fresh = score_outfit(a, Context(today=TODAY), final=True)[0]
        a[0].last_worn = TODAY - timedelta(days=1)
        worn = score_outfit(a, Context(today=TODAY), final=True)[0]
        self.assertLess(worn, fresh)

    def test_dislike_feedback(self):
        items = wardrobe_items()
        ctx = Context(today=TODAY, temperature=18, seed=1)
        best = generate(items, ctx, count=1)[0]
        ctx2 = Context(today=TODAY, temperature=18, seed=1,
                       disliked={frozenset(best.ids)},
                       pair_weights=stylist.feedback_updates(best.ids, "dislike"))
        again = generate(items, ctx2, count=3)
        self.assertNotIn(frozenset(best.ids), {frozenset(o.ids) for o in again})

    def test_two_prints_penalized(self):
        a = [item("shirt", "#c8302e", pattern=True), item("trousers", "#1c1c1e"),
             item("loafers", "#1c1c1e")]
        b = [item("shirt", "#c8302e", pattern=True), item("trousers", "#1c1c1e", pattern=True),
             item("loafers", "#1c1c1e")]
        ctx = Context(today=TODAY)
        self.assertGreater(score_outfit(a, ctx, final=True)[0], score_outfit(b, ctx, final=True)[0])

    def test_empty_and_partial_wardrobe(self):
        self.assertEqual(generate([], Context(today=TODAY)), [])
        self.assertEqual(generate([item("tshirt", "#fff000")], Context(today=TODAY)), [])
        outs = generate([item("tshirt", "#ffffff"), item("jeans", "#4a6382")], Context(today=TODAY))
        self.assertEqual(len(outs), 1)

    def test_deterministic_seed(self):
        a = generate(wardrobe_items(), Context(today=TODAY, temperature=12, seed=5), count=3)
        b = generate(wardrobe_items(), Context(today=TODAY, temperature=12, seed=5), count=3)
        self.assertEqual([o.ids for o in a], [o.ids for o in b])


if __name__ == "__main__":
    unittest.main()
