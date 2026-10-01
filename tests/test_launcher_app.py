import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class LauncherAppTests(unittest.TestCase):
    def test_launcher_lists_registered_tools_in_order(self):
        app = AppTest.from_file(PROJECT_ROOT / "launcher_app.py").run(timeout=30)
        self.assertEqual(len(app.exception), 0)
        add_button = next(button for button in app.button if button.label == "Add paper")
        self.assertFalse(add_button.disabled)
        subheaders = [item.value for item in app.subheader]
        self.assertEqual(
            subheaders[-2:],
            ["📚 Paper Notes Organizer", "✂️ Table and Figure Extractor"],
        )
        self.assertIn("Add paper", subheaders)
        self.assertTrue(any("No paper content is sent" in item.value for item in app.caption))

    def test_created_record_actions_use_both_validated_record_deep_links(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(
            os.environ, {"PAPER_NOTES_DATA_ROOT": str(Path(temporary) / "papers")}
        ):
            app = AppTest.from_file(PROJECT_ROOT / "launcher_app.py")
            app.session_state["hub_created_record"] = {
                "record_id": "paper-2", "title": "Paper"
            }
            app.run(timeout=30)
        urls = [button.proto.url for button in app.get("link_button")]
        self.assertIn("http://localhost:8501?record=paper-2", urls)
        self.assertIn("http://localhost:8502?record=paper-2", urls)


if __name__ == "__main__":
    unittest.main()
