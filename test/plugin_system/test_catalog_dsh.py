"""DSH 目录只读转译测试。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from plugin_bus.catalog_dsh import map_entries, map_entry, normalize_plugin_id  # noqa: E402


class DshCatalogTest(unittest.TestCase):
    def test_normalize_plugin_id(self):
        self.assertEqual(normalize_plugin_id("@scope/awesome-dsh-plugin"), "dsh-awesome-dsh-plugin")
        self.assertEqual(normalize_plugin_id("my_skill.pack"), "dsh-my_skill-pack")
        self.assertTrue(normalize_plugin_id("中文包").startswith("dsh-"))

    def test_skill_like_entry_is_not_installable(self):
        item = map_entry(
            {
                "package": {
                    "name": "@community/dsh-skill-writer",
                    "version": "1.2.3",
                    "description": "一个用于写作的 skill 插件",
                    "keywords": ["dsh", "skill"],
                    "links": {"repository": "https://example.com/repo"},
                    "author": {"name": "alice", "url": "https://example.com"},
                },
                "score": 42,
            }
        )
        assert item is not None
        self.assertFalse(item["installable"])
        self.assertFalse(item["unsupported"])
        self.assertEqual(item["import_kind"], "skill-import")
        self.assertEqual(item["source"], "dsh")
        self.assertEqual(item["author"]["name"], "alice")
        self.assertEqual(item["repository"], "https://example.com/repo")

    def test_non_skill_entry_marked_unsupported(self):
        item = map_entry(
            {"name": "cordis-plugin", "version": "0.1.0", "description": "内核插件"}
        )
        assert item is not None
        self.assertTrue(item["unsupported"])
        self.assertFalse(item["installable"])
        self.assertIn("不支持", item["unsupported_reason"])

    def test_map_search_payload(self):
        items = map_entries(
            {
                "objects": [
                    {
                        "package": {
                            "name": "dsh-skill-a",
                            "version": "1.0.0",
                            "keywords": ["skill"],
                        }
                    },
                    {
                        "package": {
                            "name": "dsh-core-b",
                            "version": "2.0.0",
                            "keywords": ["cordis"],
                        }
                    },
                ]
            }
        )
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["import_kind"], "skill-import")
        self.assertEqual(items[1]["import_kind"], "unsupported")


if __name__ == "__main__":
    unittest.main()
