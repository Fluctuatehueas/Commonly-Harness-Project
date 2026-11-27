"""市场目录：本地、远程分片、优先级、白名单与 stale 回退。"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import httpx  # noqa: E402

from plugin_bus.catalog import CatalogService  # noqa: E402


def _entry(plugin_id: str, version: str, **overrides):
    payload = {
        "plugin_id": plugin_id,
        "name": plugin_id,
        "version": version,
        "description": "demo",
        "dist": {"url": f"https://example.com/{plugin_id}-{version}.zip", "sha256": "a" * 64},
    }
    payload.update(overrides)
    return payload


class CatalogTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        import agent.config as agent_config

        self.config = agent_config
        self.patches = [
            patch.object(agent_config, "HARNESS_HOME", self.root),
            patch.object(agent_config, "PLUGIN_CATALOG_URL", ""),
            patch.object(agent_config, "PLUGIN_REGISTRY_URL", ""),
            patch.object(agent_config, "PLUGIN_REGISTRY_WHITELIST", []),
            patch.object(agent_config, "PLUGIN_DSH_ENABLED", False),
            patch.object(agent_config, "PLUGIN_DSH_CATALOG_URL", ""),
            patch.object(agent_config, "PLUGIN_MARKET_ENABLED", True),
            patch.object(agent_config, "PLUGIN_CATALOG_CACHE_SECONDS", 600),
        ]
        for item in self.patches:
            item.start()

    async def asyncTearDown(self):
        for item in reversed(self.patches):
            item.stop()

    async def test_local_catalog_latest_and_versions(self):
        path = self.root / "catalog.json"
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "plugins": [
                        _entry("demo", "1.0.0"),
                        _entry("demo", "1.2.0"),
                        _entry("other", "2.0.0"),
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.config.PLUGIN_CATALOG_PATH = path
        service = CatalogService()
        result = await service.get(force=True)
        self.assertFalse(result.stale)
        by_id = {item["plugin_id"]: item for item in result.items}
        self.assertEqual(by_id["demo"]["version"], "1.2.0")
        self.assertEqual(by_id["demo"]["versions"], ["1.0.0", "1.2.0"])
        self.assertEqual(by_id["demo"]["source"], "local")
        entry = await service.find("demo", "1.0.0")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["version"], "1.0.0")

    async def test_http_shards_and_priority(self):
        shard = json.dumps({"plugins": [_entry("demo", "1.5.0")]}).encode("utf-8")
        digest = hashlib.sha256(shard).hexdigest()
        main = {
            "version": 1,
            "plugins": [_entry("demo", "9.0.0")],
            "shards": [
                {"url": "https://example.com/shard.json", "sha256": digest}
            ],
        }

        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url).endswith("shard.json"):
                return httpx.Response(200, content=shard)
            return httpx.Response(200, json=main)

        service = CatalogService(transport=httpx.MockTransport(handler))
        self.config.PLUGIN_CATALOG_URL = "https://example.com/catalog.json"
        self.config.PLUGIN_REGISTRY_URL = "https://private.example.com/catalog.json"

        result = await service.get(force=True)
        # 私有源与公共源都没有私有条目，最终仍取公共源最新版本。
        by_id = {item["plugin_id"]: item for item in result.items}
        self.assertEqual(by_id["demo"]["version"], "9.0.0")
        self.assertEqual(by_id["demo"]["versions"], ["1.5.0", "9.0.0"])

    async def test_private_source_wins(self):
        public = {"plugins": [_entry("demo", "9.0.0")]}
        private = {"plugins": [_entry("demo", "2.0.0", source="private")]}

        def handler(request: httpx.Request) -> httpx.Response:
            if "private" in str(request.url):
                return httpx.Response(200, json=private)
            return httpx.Response(200, json=public)

        service = CatalogService(transport=httpx.MockTransport(handler))
        self.config.PLUGIN_CATALOG_URL = "https://example.com/catalog.json"
        self.config.PLUGIN_REGISTRY_URL = "https://private.example.com/catalog.json"
        result = await service.get(force=True)
        self.assertEqual(result.items[0]["source"], "private")
        self.assertEqual(result.items[0]["version"], "2.0.0")

    async def test_stale_fallback_keeps_previous_items(self):
        state = {"fail": False}

        def handler(request: httpx.Request) -> httpx.Response:
            if state["fail"]:
                return httpx.Response(500)
            return httpx.Response(200, json={"plugins": [_entry("demo", "1.0.0")]})

        service = CatalogService(transport=httpx.MockTransport(handler))
        self.config.PLUGIN_CATALOG_URL = "https://example.com/catalog.json"
        first = await service.get(force=True)
        self.assertFalse(first.stale)
        state["fail"] = True
        second = await service.get(force=True)
        self.assertTrue(second.stale)
        self.assertEqual([item["plugin_id"] for item in second.items], ["demo"])
        self.assertFalse(second.sources["public"]["ok"])

    async def test_whitelist_filters_all_sources(self):
        public = {"plugins": [_entry("allow-me", "1.0.0"), _entry("deny-me", "1.0.0")]}

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=public)

        service = CatalogService(transport=httpx.MockTransport(handler))
        self.config.PLUGIN_CATALOG_URL = "https://example.com/catalog.json"
        self.config.PLUGIN_REGISTRY_WHITELIST = ["allow-me"]
        result = await service.get(force=True)
        self.assertEqual([item["plugin_id"] for item in result.items], ["allow-me"])

    async def test_market_disabled_only_private(self):
        private = {"plugins": [_entry("private-only", "1.0.0")]}
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return httpx.Response(200, json=private)

        service = CatalogService(transport=httpx.MockTransport(handler))
        self.config.PLUGIN_MARKET_ENABLED = False
        self.config.PLUGIN_CATALOG_URL = "https://example.com/catalog.json"
        self.config.PLUGIN_REGISTRY_URL = "https://private.example.com/catalog.json"
        result = await service.get(force=True)
        self.assertEqual([item["plugin_id"] for item in result.items], ["private-only"])
        self.assertTrue(all("private" in url for url in calls))


if __name__ == "__main__":
    unittest.main()
