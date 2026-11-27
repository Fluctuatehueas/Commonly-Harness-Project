"""插件存储维护：旧版本、日志轮转、临时目录。"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from plugin_bus import storage, storage_maintenance  # noqa: E402


class StorageMaintenanceTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        import agent.config as agent_config

        self.config = agent_config
        self.patch_root = patch.object(agent_config, "PLUGIN_USERS_ROOT", self.root)
        self.patch_keep = patch.object(agent_config, "PLUGIN_VERSION_KEEP", 2)
        self.patch_root.start()
        self.patch_keep.start()
        self.addCleanup(self.patch_root.stop)
        self.addCleanup(self.patch_keep.stop)

    def _make_versions(self, *versions: str, current: str = "") -> None:
        for version in versions:
            directory = self.root / "u1" / "demo" / "versions" / version
            directory.mkdir(parents=True)
            (directory / "manifest.json").write_text("{}", encoding="utf-8")
        if current:
            (self.root / "u1" / "demo" / "current.json").write_text(
                f'{{"version": "{current}"}}', encoding="utf-8"
            )

    def test_cleanup_old_versions_protects_current(self):
        self._make_versions("1.0.0", "1.1.0", "1.2.0", "1.3.0", current="1.0.0")
        removed = storage_maintenance.cleanup_old_versions(keep=2)
        self.assertEqual(removed, 1)
        versions = sorted(
            item.name for item in (self.root / "u1" / "demo" / "versions").iterdir()
        )
        self.assertEqual(versions, ["1.0.0", "1.2.0", "1.3.0"])

    def test_cleanup_old_versions_noop(self):
        self._make_versions("1.0.0", current="1.0.0")
        self.assertEqual(storage_maintenance.cleanup_old_versions(keep=2), 0)

    def test_rotate_log(self):
        logs = self.root / "u1" / "demo" / "logs"
        logs.mkdir(parents=True)
        log = logs / "stdio.log"
        log.write_bytes(b"x" * 100)
        self.assertTrue(storage_maintenance.rotate_log(log, max_bytes=10, keep=2))
        self.assertFalse(log.exists())
        self.assertTrue((logs / "stdio.log.1").is_file())
        self.assertFalse((logs / "stdio.log.2").is_file())

    def test_cleanup_tmp_only_old(self):
        tmp = self.root / "u1" / ".tmp"
        old = tmp / "old"
        fresh = tmp / "fresh"
        old.mkdir(parents=True)
        fresh.mkdir(parents=True)
        two_days_ago = time.time() - 2 * 24 * 3600
        os.utime(old, (two_days_ago, two_days_ago))
        removed = storage_maintenance.cleanup_tmp()
        self.assertEqual(removed, 1)
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())

    def test_run_once_result_shape(self):
        self._make_versions("1.0.0", "1.1.0", "1.2.0")
        result = storage_maintenance.run_once()
        self.assertEqual(
            set(result), {"versions_removed", "logs_rotated", "tmp_removed"}
        )


if __name__ == "__main__":
    unittest.main()
