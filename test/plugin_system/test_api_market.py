"""插件管理 API 的只读接口测试（不启动完整 FastAPI lifespan）。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agent import identity  # noqa: E402
from agent.config import POSTGRES_URI  # noqa: E402,F401 - 导入即设置 Windows Selector 事件循环
from api import plugins as api_plugins  # noqa: E402
from plugin_bus.runtime import shutdown_plugin_runtimes  # noqa: E402

USER_ID = "__plugin_api_test__"


class PluginApiReadOnlyTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        catalog_path = self.root / "catalog.json"
        catalog_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "plugins": [
                        {
                            "plugin_id": "demo-plugin",
                            "name": "演示插件",
                            "version": "1.0.0",
                            "description": "来自本地目录",
                            "source": "local",
                            "capabilities": ["tool"],
                            "permissions": [],
                            "dist": {"url": "https://example.com/demo.zip", "sha256": "a" * 64},
                            "tag": ["demo"],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        import agent.config as agent_config

        self.patches = [
            patch.object(agent_config, "HARNESS_HOME", self.root),
            patch.object(agent_config, "PLUGIN_CATALOG_PATH", catalog_path),
            patch.object(agent_config, "PLUGIN_CATALOG_URL", ""),
            patch.object(agent_config, "PLUGIN_REGISTRY_URL", ""),
            patch.object(agent_config, "PLUGIN_REGISTRY_WHITELIST", []),
            patch.object(agent_config, "PLUGIN_DSH_ENABLED", False),
            patch.object(agent_config, "PLUGIN_MARKET_ENABLED", True),
            patch.object(agent_config, "PLUGIN_CATALOG_CACHE_SECONDS", 600),
        ]
        for item in self.patches:
            item.start()
        self.token = identity.set_current_user_id(USER_ID)

    async def asyncTearDown(self):
        await shutdown_plugin_runtimes()
        identity.reset_current_user_id(self.token)
        from plugin_bus import catalog as catalog_module

        catalog_module._CATALOG = None
        for item in reversed(self.patches):
            item.stop()

    async def test_market_catalog_and_detail(self):
        page = await api_plugins.market_catalog(
            keyword="",
            capability="",
            source="",
            sort="popular",
            page=1,
            page_size=12,
            refresh=True,
        )
        self.assertEqual(page["total"], 1)
        self.assertFalse(page["stale"])
        self.assertEqual(page["items"][0]["plugin_id"], "demo-plugin")
        self.assertFalse(page["items"][0]["installed"])

        detail = await api_plugins.market_detail("demo-plugin")
        self.assertEqual(detail["market"]["name"], "演示插件")
        self.assertFalse(detail["installed"])
        self.assertIsNone(detail["health"])

    async def test_installed_list_and_audit_are_user_scoped(self):
        result = await api_plugins.list_plugins(
            keyword="", enabled=None, source=""
        )
        # 系统内置插件对所有用户可见；测试用户自己没有安装记录。
        self.assertTrue(
            all(item["builtin"] for item in result["items"]), result["items"]
        )

        audit = await api_plugins.list_plugin_audit(plugin_id="", limit=10)
        self.assertEqual(audit["items"], [])

    async def test_unknown_plugin_detail_404(self):
        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as context:
            await api_plugins.get_plugin("missing-plugin")
        self.assertEqual(context.exception.status_code, 404)

    async def test_dsh_import_rejects_non_dsh_entry(self):
        from fastapi import HTTPException

        entry = {
            "source": "local",
            "plugin_id": "demo-plugin",
            "version": "1.0.0",
            "import_kind": "skill-import",
            "importable": True,
        }
        with patch.object(api_plugins, "_catalog_entry", AsyncMock(return_value=entry)):
            with self.assertRaises(HTTPException) as context:
                await api_plugins.import_dsh_skill(
                    api_plugins.DshImportRequest(plugin_id="demo-plugin")
                )
        self.assertEqual(context.exception.status_code, 409)

    async def test_dsh_import_starts_job(self):
        entry = {
            "source": "dsh",
            "plugin_id": "dsh-demo-skill",
            "name": "dsh-demo-skill",
            "version": "1.0.0",
            "import_kind": "skill-import",
            "importable": True,
            "npm_dist": {"tarball": "https://registry.example.com/demo.tgz"},
        }

        async def fake_import(user_id: str, item: dict, *, job_id: str = "") -> dict:
            return {"skill": {"name": "dsh-demo-skill"}}

        with patch.object(
            api_plugins, "_catalog_entry", AsyncMock(return_value=entry)
        ), patch.object(api_plugins.dsh_import, "import_skill", side_effect=fake_import):
            result = await api_plugins.import_dsh_skill(
                api_plugins.DshImportRequest(plugin_id="dsh-demo-skill")
            )
        self.assertTrue(result["job_id"])
        self.assertEqual(result["job"]["kind"], "dsh-skill")


if __name__ == "__main__":
    unittest.main()
