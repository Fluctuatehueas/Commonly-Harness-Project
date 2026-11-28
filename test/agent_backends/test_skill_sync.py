"""技能同步器：清单读取不应再因 self 缺失报错。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agent.backends.skill_sync import (  # noqa: E402
    SANDBOX_MANIFEST_PATH,
    SandboxSkillSynchronizer,
)


class FakeBackend:
    def __init__(self, content: bytes | None, error: str | None = None) -> None:
        self._content = content
        self._error = error
        self.commands: list[str] = []

    def download_files(self, paths: list[str]) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(
                path=path,
                content=self._content,
                error=self._error,
            )
            for path in paths
        ]

    def execute(self, command: str, *, timeout: int | None = None) -> SimpleNamespace:
        self.commands.append(command)
        return SimpleNamespace(exit_code=0, output="")


class SkillSyncManifestTest(unittest.TestCase):
    def _synchronizer(self) -> SandboxSkillSynchronizer:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "main").mkdir(parents=True, exist_ok=True)
        agents = root / "AGENTS.md"
        agents.write_text("# rules", encoding="utf-8")
        return SandboxSkillSynchronizer(
            [root],
            agents,
            sandbox_prefixes=["/skills"],
        )

    def test_read_manifest_accepts_skill_and_agents_paths(self):
        sync = self._synchronizer()
        payload = json.dumps(
            {
                "/AGENTS.md": "a",
                "/skills/main/demo/SKILL.md": "b",
            }
        ).encode("utf-8")
        value = sync._read_manifest(FakeBackend(payload))  # noqa: SLF001
        self.assertIn("/skills/main/demo/SKILL.md", value)

    def test_read_manifest_first_sync_when_missing(self):
        sync = self._synchronizer()
        value = sync._read_manifest(  # noqa: SLF001
            FakeBackend(None, error="file_not_found")
        )
        self.assertEqual(value, {})

    def test_read_manifest_rejects_illegal_path(self):
        sync = self._synchronizer()
        payload = json.dumps({"/etc/passwd": "x"}).encode("utf-8")
        with self.assertRaises(RuntimeError):
            sync._read_manifest(FakeBackend(payload))  # noqa: SLF001

    def test_manifest_path_constant(self):
        self.assertEqual(SANDBOX_MANIFEST_PATH, "/.myagent/skills-manifest.json")

    def test_remove_paths_batches_commands(self):
        sync = self._synchronizer()
        backend = FakeBackend(None)
        removed = [f"/skills/main/demo/file-{index}.py" for index in range(120)]
        sync._remove_paths(backend, removed)  # noqa: SLF001
        self.assertEqual(len(backend.commands), 3)
        self.assertTrue(all(command.startswith("rm -f -- ") for command in backend.commands))
        self.assertEqual(sum(command.count("/skills/") for command in backend.commands), 120)


if __name__ == "__main__":
    unittest.main()
