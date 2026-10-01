import os
import unittest
from pathlib import Path

from paper_notes.tool_registry import load_registry


WORKFLOW_ROOT = Path(__file__).resolve().parents[1]
PARENT_ROOT = WORKFLOW_ROOT.parent


class ReorganizedLayoutTests(unittest.TestCase):
    def test_registry_and_storage_are_rooted_in_workflow_folder(self):
        registry = load_registry(WORKFLOW_ROOT / "tools.toml")

        self.assertEqual(registry.root, WORKFLOW_ROOT)
        self.assertEqual(registry.launcher.entrypoint, WORKFLOW_ROOT / "launcher_app.py")
        self.assertEqual(
            {tool.id: tool.storage for tool in registry.tools},
            {
                "paper-notes": WORKFLOW_ROOT / "data",
                "table-figure-extractor": WORKFLOW_ROOT / "extracted_exhibits",
            },
        )

    def test_canonical_launchers_do_not_depend_on_parent_wrappers(self):
        for script_name in ("run.sh", "run_extractor.sh", "run_workspace.sh"):
            script = WORKFLOW_ROOT / script_name
            contents = script.read_text(encoding="utf-8")
            self.assertNotIn("../paper_reading_workflow", contents)

    def test_canonical_launchers_enter_the_workflow_root(self):
        for script_name in ("run.sh", "run_extractor.sh", "run_workspace.sh"):
            script = WORKFLOW_ROOT / script_name
            self.assertTrue(script.is_file())
            self.assertTrue(os.access(script, os.X_OK))
            self.assertIn('cd "$SCRIPT_DIR"', script.read_text(encoding="utf-8"))

    def test_application_implementation_is_not_duplicated_at_parent_level(self):
        for filename in ("app.py", "extractor_app.py", "launcher_app.py", "tools.toml"):
            self.assertFalse((PARENT_ROOT / filename).exists())


if __name__ == "__main__":
    unittest.main()
