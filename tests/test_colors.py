import unittest

from clothbot.colors import ColorInfo, extract_palette, guess_pattern, harmony, nearest_named


class ColorTests(unittest.TestCase):
    def test_nearest_named(self):
        self.assertEqual(nearest_named((20, 20, 22)).key, "black")
        self.assertEqual(nearest_named((250, 250, 250)).key, "white")
        self.assertEqual(nearest_named((200, 40, 40)).key, "red")
        self.assertEqual(nearest_named((70, 98, 130)).key, "denim")

    def test_neutrals(self):
        self.assertTrue(ColorInfo("#4a6382").is_neutral)   # деним
        self.assertTrue(ColorInfo("#b5835a").is_neutral)   # кэмел
        self.assertFalse(ColorInfo("#c8302e").is_neutral)  # красный

    def test_palette_two_colors(self):
        px = [(200, 30, 30)] * 700 + [(245, 245, 245)] * 300
        pal = extract_palette(px, k=4)
        self.assertEqual(len(pal), 2)
        self.assertEqual(pal[0].named.key, "red")
        self.assertAlmostEqual(pal[0].share, 0.7, places=2)
        self.assertTrue(guess_pattern(pal))

    def test_palette_shading_merged(self):
        # Складки одной ткани — это один цвет, а не принт.
        px = [(40, 60, 120)] * 500 + [(45, 66, 128)] * 300 + [(36, 54, 110)] * 200
        pal = extract_palette(px)
        self.assertEqual(len(pal), 1)
        self.assertFalse(guess_pattern(pal))

    def test_harmony_ranking(self):
        neutral_accent = harmony([ColorInfo("#f7f7f5"), ColorInfo("#4a6382"), ColorInfo("#c8302e")])
        clash = harmony([ColorInfo("#ec7a2c"), ColorInfo("#c8327f"), ColorInfo("#2f8a4c")])
        mono = harmony([ColorInfo("#2f5fd0"), ColorInfo("#8ec5ea")])
        self.assertGreater(neutral_accent.score, clash.score)
        self.assertGreater(mono.score, clash.score)
        self.assertIn("акцент", neutral_accent.reason)


if __name__ == "__main__":
    unittest.main()
