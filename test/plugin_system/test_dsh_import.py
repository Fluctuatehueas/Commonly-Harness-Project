"""DSH 包转译为技能：解压安全、SKILL.md 定位与落盘。"""

from __future__ import annotations

import hashlib
import io
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import httpx  # noqa: E402

from plugin_bus import dsh_import  # noqa: E402
from plugin_bus.errors import PluginPackageInvalid  # noqa: E402

USER_ID = "__dsh_import_test__"

SKILL_MD = """---
name: dsh-demo-skill
description: 演示用的 DSH 技能
---

# demo
"""


def build_tgz(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, content in files.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def build_link_tgz(target: str) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo("package/link")
        info.type = tarfile.SYMTYPE
        info.linkname = target
        archive.addfile(info)
    return buffer.getvalue()


class DshArchiveTest(unittest.TestCase):
    def test_extract_rejects_path_traversal(self):
        data = build_tgz({"package/../../evil.txt": "x"})
        with tempfile.TemporaryDirectory() as name:
            with self.assertRaises(PluginPackageInvalid):
                dsh_import.extract_tarball(data, Path(name))

    def test_extract_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as name:
            with self.assertRaises(PluginPackageInvalid):
                dsh_import.extract_tarball(build_link_tgz("/etc/passwd"), Path(name))

    def test_locate_skill_directory(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
            self.assertEqual(dsh_import.locate_skill_directory(root), root)

        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            nested = root / "skills" / "demo"
            nested.mkdir(parents=True)
            (nested / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
            self.assertEqual(dsh_import.locate_skill_directory(root), nested)

        with tempfile.TemporaryDirectory() as name:
            self.assertIsNone(dsh_import.locate_skill_directory(Path(name)))

    def test_verify_integrity(self):
        data = b"hello"
        dsh_import.verify_integrity(data, shasum=hashlib.sha1(data).hexdigest())
        with self.assertRaises(PluginPackageInvalid):
            dsh_import.verify_integrity(data, shasum="0" * 40)


class DshImportTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        import agent.config as agent_config

        self.patches = [
            patch.object(agent_config, "HARNESS_HOME", self.root),
            patch.object(agent_config, "PLUGIN_ROOT", self.root / "plugins"),
            patch.object(dsh_import, "_refresh_skills", AsyncMock()),
            patch.object(dsh_import, "_audit", AsyncMock()),
        ]
        for item in self.patches:
            item.start()

    async def asyncTearDown(self):
        for item in reversed(self.patches):
            item.stop()

    def _entry(self, data: bytes) -> dict:
        return {
            "source": "dsh",
            "plugin_id": "dsh-demo-skill",
            "npm_name": "dsh-demo-skill",
            "version": "1.0.0",
            "import_kind": "skill-import",
            "importable": True,
            "npm_dist": {
                "tarball": "https://registry.example.com/dsh-demo-skill-1.0.0.tgz",
                "integrity": "",
                "shasum": hashlib.sha1(data).hexdigest(),
                "size": len(data),
            },
        }

    async def test_import_installs_skill_to_user_library(self):
        data = build_tgz(
            {
                "package/SKILL.md": SKILL_MD,
                "package/scripts/run.py": "print('ok')\n",
            }
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=data)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await dsh_import.import_skill(
                USER_ID, self._entry(data), client=client
            )

        skill = result["skill"]
        self.assertEqual(skill["name"], "dsh-demo-skill")
        installed = (
            self.root
            / "skills"
            / "users"
            / USER_ID
            / "main"
            / "dsh-demo-skill"
            / "SKILL.md"
        )
        self.assertTrue(installed.is_file())
        self.assertEqual(result["npm_name"], "dsh-demo-skill")

    async def test_import_rejects_package_without_skill_md(self):
        data = build_tgz({"package/index.js": "module.exports = {}\n"})

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=data)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(PluginPackageInvalid):
                await dsh_import.import_skill(USER_ID, self._entry(data), client=client)

    async def test_import_rejects_unsupported_entry(self):
        entry = self._entry(b"")
        entry["import_kind"] = "unsupported"
        with self.assertRaises(PluginPackageInvalid):
            await dsh_import.import_skill(USER_ID, entry)


if __name__ == "__main__":
    unittest.main()
