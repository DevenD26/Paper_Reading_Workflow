import tempfile
import unittest
from pathlib import Path

from paper_notes.tool_registry import RegistryError, load_registry


VALID_REGISTRY = """\
schema_version = 1
[workspace]
name = "Hub"
entrypoint = "launcher.py"
port = 8500
url = "http://localhost:8500"
[[tools]]
id = "notes"
name = "Notes"
description = "Write notes"
icon = "N"
entrypoint = "notes.py"
port = 8501
url = "http://localhost:8501"
storage = "notes-data"
[[tools]]
id = "extractor"
name = "Extractor"
description = "Crop exhibits"
icon = "E"
entrypoint = "extractor.py"
port = 8502
url = "http://127.0.0.1:8502"
storage = "extractor-data"
"""


class ToolRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        for name in ("launcher.py", "notes.py", "extractor.py"):
            (self.root / name).write_text("# local app\n", encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def load(self, text: str = VALID_REGISTRY):
        path = self.root / "tools.toml"
        path.write_text(text, encoding="utf-8")
        return load_registry(path)

    def test_preserves_declared_tool_order(self):
        registry = self.load()
        self.assertEqual([tool.id for tool in registry.tools], ["notes", "extractor"])

    def test_duplicate_ids_are_rejected(self):
        with self.assertRaisesRegex(RegistryError, "Duplicate tool ID"):
            self.load(VALID_REGISTRY.replace('id = "extractor"', 'id = "notes"'))

    def test_duplicate_ports_are_rejected(self):
        with self.assertRaisesRegex(RegistryError, "Duplicate application port"):
            self.load(
                VALID_REGISTRY.replace("port = 8502", "port = 8501").replace(
                    "http://127.0.0.1:8502", "http://127.0.0.1:8501"
                )
            )

    def test_invalid_port_and_url_are_rejected(self):
        with self.assertRaisesRegex(RegistryError, "port must be"):
            self.load(VALID_REGISTRY.replace("port = 8502", 'port = "8502"'))
        with self.assertRaisesRegex(RegistryError, "plain local HTTP URL"):
            self.load(VALID_REGISTRY.replace("http://127.0.0.1:8502", "https://example.com:8502"))

    def test_missing_and_unsafe_entrypoints_are_rejected(self):
        with self.assertRaisesRegex(RegistryError, "existing project-local Python file"):
            self.load(VALID_REGISTRY.replace('entrypoint = "extractor.py"', 'entrypoint = "missing.py"'))
        with self.assertRaisesRegex(RegistryError, "project-relative"):
            self.load(VALID_REGISTRY.replace('entrypoint = "extractor.py"', 'entrypoint = "../extractor.py"'))

    def test_malformed_toml_is_reported(self):
        with self.assertRaisesRegex(RegistryError, "Malformed tool registry"):
            self.load("[[tools]\n")

    def test_overlapping_storage_ownership_is_rejected(self):
        with self.assertRaisesRegex(RegistryError, "Storage ownership overlaps"):
            self.load(VALID_REGISTRY.replace('storage = "extractor-data"', 'storage = "notes-data/child"'))


if __name__ == "__main__":
    unittest.main()
