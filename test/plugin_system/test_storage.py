"""插件存储与包解压安全测试。"""

from __future__ import annotations

import io
import stat
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from plugin_bus import storage  # noqa: E402
from plugin_bus.errors import PluginPackageInvalid  # noqa: E402


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


class StorageTest(unittest.TestCase):
    def test_reject_path_traversal(self):
        data = _zip_bytes({"../evil.txt": b"x"})
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(PluginPackageInvalid):
                storage.safe_extract_zip(data, Path(temporary), max_bytes=1024)

    def test_reject_symlink(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            info = zipfile.ZipInfo("link")
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, "target")
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(PluginPackageInvalid):
                storage.safe_extract_zip(
                    buffer.getvalue(), Path(temporary), max_bytes=1024
                )

    def test_find_single_wrapped_package_root(self):
        data = _zip_bytes(
            {
                "demo/manifest.json": b"{}",
                "demo/main.py": b"print(1)",
            }
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = storage.safe_extract_zip(
                data, Path(temporary) / "extracted", max_bytes=1024
            )
            self.assertEqual(root.name, "demo")

    def test_reject_package_without_manifest(self):
        data = _zip_bytes({"demo/main.py": b"print(1)"})
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(PluginPackageInvalid):
                storage.safe_extract_zip(
                    data, Path(temporary) / "extracted", max_bytes=1024
                )

    def test_resolve_ui_file_blocks_traversal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "ui").mkdir()
            (root / "ui" / "index.html").write_text("ok", encoding="utf-8")
            self.assertIsNotNone(storage.resolve_ui_file(root, "ui/index.html"))
            self.assertIsNone(storage.resolve_ui_file(root, "../secret.txt"))
            self.assertIsNone(storage.resolve_ui_file(root, "/etc/passwd"))

    def test_current_json_atomic(self):
        import agent.config as agent_config

        with tempfile.TemporaryDirectory() as temporary:
            original = agent_config.PLUGIN_USERS_ROOT
            agent_config.PLUGIN_USERS_ROOT = Path(temporary)
            try:
                storage.write_current("u1", "demo", "1.0.0")
                self.assertEqual(storage.read_current("u1", "demo"), "1.0.0")
                storage.write_current("u1", "demo", "1.1.0")
                self.assertEqual(storage.read_current("u1", "demo"), "1.1.0")
            finally:
                agent_config.PLUGIN_USERS_ROOT = original


if __name__ == "__main__":
    unittest.main()
