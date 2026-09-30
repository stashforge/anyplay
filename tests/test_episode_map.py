import unittest

from allmanga_cli.domain.episodes import (
    build_episode_map,
    create_episode_entry,
    extract_episode_tags,
)
from allmanga_cli.providers.shared.schema import build_catalog


class TestEpisodeMap(unittest.TestCase):
    def test_extract_episode_tags(self):
        clean, tags = extract_episode_tags("Episode 40 [4K]")
        self.assertEqual(clean, "Episode 40")
        self.assertEqual(tags, ["4K"])

        clean2, tags2 = extract_episode_tags("Episode 12 [1080p] [DUB]")
        self.assertEqual(clean2, "Episode 12")
        self.assertEqual(tags2, ["1080P", "DUB"])

    def test_create_episode_entry_lucifer_with_tags(self):
        entry = create_episode_entry(
            {
                "id": "https://luciferdonghua.in/beyond-the-timescape-episode-40-sub/",
                "label": "Episode 40 [4K]",
                "url": "https://luciferdonghua.in/beyond-the-timescape-episode-40-sub/",
            }
        )
        self.assertEqual(entry["id"], "https://luciferdonghua.in/beyond-the-timescape-episode-40-sub/")
        self.assertEqual(entry["number"], 40.0)
        self.assertEqual(entry["display_number"], "40")
        self.assertIsNone(entry["franchise_number"])
        self.assertEqual(entry["tags"], ["4K"])
        self.assertEqual(entry["label"], "Episode 40 [4K]")

    def test_create_episode_entry_dual_number(self):
        entry = create_episode_entry(
            {
                "id": "92 [156]",
                "label": "Episode 92 [156]",
            }
        )
        self.assertEqual(entry["number"], 92.0)
        self.assertEqual(entry["display_number"], "92")
        self.assertEqual(entry["franchise_number"], "156")
        self.assertEqual(entry["tags"], [])
        self.assertIn("92 [156]", entry["label"])

    def test_build_episode_map_deduplication_and_sorting(self):
        # Simulates Lucifer's scrambled DOM with duplicate episode 42 inserted between 33 and 34
        raw = [
            {"id": "https://lucifer/ep-35", "label": "Episode 35 [4K]"},
            {"id": "https://lucifer/ep-34", "label": "Episode 34 [4K]"},
            {"id": "https://lucifer/ep-42", "label": "Episode 42 [4K]"},  # DUPLICATE 1
            {"id": "https://lucifer/ep-33", "label": "Episode 33 [4K]"},
            {"id": "https://lucifer/ep-42", "label": "Episode 42 [4K]"},  # DUPLICATE 2
            {"id": "https://lucifer/ep-32", "label": "Episode 32 [4K]"},
        ]

        ep_map, by_num = build_episode_map(raw)

        # 1. Deduplication verified: exactly 5 items (32, 33, 34, 35, 42)
        self.assertEqual(len(ep_map), 5)
        numbers = [ep["number"] for ep in ep_map.values()]
        self.assertEqual(numbers, [32.0, 33.0, 34.0, 35.0, 42.0])

        # 2. Strict numerical sorting verified
        display_numbers = [ep["display_number"] for ep in ep_map.values()]
        self.assertEqual(display_numbers, ["32", "33", "34", "35", "42"])

        # 3. by_number fast lookup map verified
        self.assertEqual(by_num["42"], "https://lucifer/ep-42")
        self.assertEqual(by_num["32"], "https://lucifer/ep-32")

    def test_build_catalog_backwards_compatibility(self):
        raw = [
            {"id": "https://lucifer/ep-1", "label": "1 [4K]"},
            {"id": "https://lucifer/ep-2", "label": "2 [4K]"},
        ]
        catalog = build_catalog(
            provider="lucifer",
            provider_id="test",
            episodes={"sub": raw},
        )

        # Backwards compatibility check
        self.assertIn("ids", catalog)
        self.assertIn("labels", catalog)
        self.assertEqual(catalog["ids"], ["https://lucifer/ep-1", "https://lucifer/ep-2"])
        self.assertEqual(catalog["labels"]["https://lucifer/ep-1"], "Episode 1 [4K]")

        # New map structures
        self.assertIn("episodes_map", catalog)
        self.assertIn("by_number", catalog)
        self.assertEqual(catalog["by_number"]["1"], "https://lucifer/ep-1")
        self.assertEqual(catalog["episodes_map"]["https://lucifer/ep-1"]["number"], 1.0)


if __name__ == "__main__":
    unittest.main()
