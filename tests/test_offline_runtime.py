import ast
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz

from paper_notes.extraction import extract_pdf


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class OfflineRuntimeTests(unittest.TestCase):
    def test_extractor_runs_with_network_connections_blocked(self):
        with tempfile.TemporaryDirectory() as temporary:
            pdf_path = Path(temporary) / "offline.pdf"
            document = fitz.open()
            page = document.new_page()
            page.insert_text((50, 100), "Table 1: Offline result")
            document.save(pdf_path)
            document.close()

            with patch.object(socket.socket, "connect", side_effect=AssertionError("network attempted")), patch(
                "socket.create_connection", side_effect=AssertionError("network attempted")
            ):
                result = extract_pdf(pdf_path)
            self.assertEqual(result["candidates"][0]["identifier"], "Table 1")

    def test_runtime_source_has_no_ai_or_network_client_imports(self):
        prohibited = {
            "openai",
            "langchain",
            "transformers",
            "torch",
            "tensorflow",
            "requests",
            "urllib3",
            "aiohttp",
            "httpx",
        }
        source_files = [
            PROJECT_ROOT / "app.py",
            PROJECT_ROOT / "extractor_app.py",
            PROJECT_ROOT / "launcher_app.py",
            *sorted((PROJECT_ROOT / "paper_notes").glob("*.py")),
        ]
        imported: set[str] = set()
        for path in source_files:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module.split(".")[0])
        self.assertFalse(imported & prohibited, imported & prohibited)

    def test_declared_dependencies_have_no_ai_packages(self):
        requirements = (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8").casefold()
        for package in ("openai", "langchain", "transformers", "torch", "tensorflow"):
            self.assertNotIn(package, requirements)

    def test_streamlit_usage_collection_is_disabled(self):
        config = (PROJECT_ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8")
        self.assertIn("gatherUsageStats = false", config)
        self.assertIn('serverAddress = "localhost"', config)
        self.assertIn('address = "127.0.0.1"', config)


if __name__ == "__main__":
    unittest.main()
