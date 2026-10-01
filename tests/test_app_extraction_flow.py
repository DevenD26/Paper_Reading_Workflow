import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz
from streamlit.testing.v1 import AppTest

from paper_notes.extraction import write_proposal
from paper_notes.extractor_runs import create_extraction_run
from paper_notes.storage import create_record, load_notes


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class AppExtractionFlowTests(unittest.TestCase):
    def test_paper_notes_record_deep_link_opens_exact_record_and_rejects_invalid_id(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "papers"
            document = fitz.open()
            document.new_page()
            first = create_record(data_root, "First", document.tobytes(), "first.pdf")
            second = create_record(data_root, "Second", document.tobytes(), "second.pdf")
            document.close()
            with patch.dict(os.environ, {"PAPER_NOTES_DATA_ROOT": str(data_root)}):
                app = AppTest.from_file(PROJECT_ROOT / "app.py")
                app.query_params["record"] = second["record_id"]
                app.run(timeout=30)
                self.assertIn("Second", [item.value for item in app.title])
                invalid = AppTest.from_file(PROJECT_ROOT / "app.py")
                invalid.query_params["record"] = "../outside"
                invalid.run(timeout=30)
                self.assertTrue(any("invalid" in item.value for item in invalid.error))
            self.assertNotEqual(first["record_id"], second["record_id"])

    def test_linked_extractor_uses_one_record_pdf_and_attaches_approved_crop(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "papers"
            output_root = Path(temporary) / "standalone-extractions"
            document = fitz.open()
            page = document.new_page(width=612, height=792)
            page.insert_text((54, 100), "Figure 1: Linked local result", fontsize=12)
            pdf_bytes = document.tobytes()
            document.close()
            notes = create_record(data_root, "Linked paper", pdf_bytes, "paper.pdf")
            source_pdf = data_root / notes["record_id"] / "source.pdf"

            with patch.dict(
                os.environ,
                {
                    "PAPER_NOTES_DATA_ROOT": str(data_root),
                    "PAPER_EXTRACTOR_OUTPUT_ROOT": str(output_root),
                },
            ):
                app = AppTest.from_file(PROJECT_ROOT / "extractor_app.py")
                app.query_params["record"] = notes["record_id"]
                app.run(timeout=30)
                self.assertEqual(len(app.exception), 0)
                analyze = next(button for button in app.button if button.label == "Analyze PDF")
                analyze.click().run(timeout=30)
                approve = next(
                    button for button in app.button if button.label == "Approve and add to Paper Notes"
                )
                approve.click().run(timeout=30)

            self.assertEqual(len(app.exception), 0)
            loaded = load_notes(data_root, notes["record_id"])
            self.assertEqual(len(loaded["exhibits"]), 1)
            self.assertTrue(
                (data_root / notes["record_id"] / loaded["exhibits"][0]["images"][0]["image"]).is_file()
            )
            self.assertEqual(source_pdf.read_bytes(), pdf_bytes)
            self.assertFalse(output_root.exists())

    def test_extractor_main_menu_lists_runs_and_confirms_trash(self):
        with tempfile.TemporaryDirectory() as temporary:
            output_root = Path(temporary) / "extracted"
            run_folder = create_extraction_run(
                output_root, "Menu Test Paper", "uploaded-paper.pdf"
            )

            with patch.dict(
                os.environ, {"PAPER_EXTRACTOR_OUTPUT_ROOT": str(output_root)}
            ):
                app = AppTest.from_file(PROJECT_ROOT / "extractor_app.py").run(timeout=30)
                self.assertEqual(len(app.exception), 0)
                self.assertTrue(
                    any(item.value == "Menu Test Paper" for item in app.subheader)
                )
                self.assertTrue(
                    any(button.label == "Open approved folder" for button in app.button)
                )
                trash_button = next(
                    button for button in app.button if button.label == "Move paper to Trash"
                )
                self.assertTrue(trash_button.disabled)
                confirmation = next(
                    checkbox
                    for checkbox in app.checkbox
                    if checkbox.label.startswith("Move this paper, its PDF")
                )
                confirmation.check().run(timeout=30)
                trash_button = next(
                    button for button in app.button if button.label == "Move paper to Trash"
                )
                trash_button.click().run(timeout=30)

            self.assertFalse(run_folder.exists())
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(any(".trash" in item.value for item in app.success))

    def test_standalone_app_can_review_and_approve_an_exhibit(self):
        with tempfile.TemporaryDirectory() as temporary:
            run_folder = Path(temporary) / "run"
            run_folder.mkdir()
            document = fitz.open()
            page = document.new_page(width=612, height=792)
            page.insert_text((54, 100), "Figure 1: Local result", fontsize=12)
            source_pdf = run_folder / "source.pdf"
            document.save(source_pdf)
            document.close()
            manifest_path = write_proposal(source_pdf, run_folder / "work")
            (run_folder / "approved").mkdir()

            app = AppTest.from_file(PROJECT_ROOT / "extractor_app.py")
            app.session_state["extractor_run_folder"] = str(run_folder)
            app.session_state["extractor_manifest"] = str(manifest_path)
            app.run(timeout=30)
            self.assertEqual(len(app.exception), 0)
            approve_button = next(button for button in app.button if button.label == "Approve and save")
            approve_button.click().run(timeout=30)
            self.assertEqual(len(app.exception), 0)
            self.assertTrue((run_folder / "approved" / "Figure 1.png").is_file())

    def test_paper_notes_app_starts_with_batch_screenshot_workflow(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "papers"
            document = fitz.open()
            page = document.new_page()
            page.insert_text((50, 100), "Paper")
            pdf_bytes = document.tobytes()
            document.close()
            create_record(data_root, "Batch upload", pdf_bytes, "paper.pdf")

            with patch.dict(os.environ, {"PAPER_NOTES_DATA_ROOT": str(data_root)}):
                app = AppTest.from_file(PROJECT_ROOT / "app.py").run(timeout=30)
            self.assertEqual(len(app.exception), 0)
            self.assertFalse(any(button.label == "Find tables and figures" for button in app.button))
            captions = [element.value for element in app.caption]
            self.assertTrue(any("Each file becomes its own exhibit entry" in value for value in captions))

    def test_main_menu_requires_confirmation_before_deleting_paper(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "papers"
            document = fitz.open()
            document.new_page()
            pdf_bytes = document.tobytes()
            document.close()
            notes = create_record(data_root, "Delete from menu", pdf_bytes, "paper.pdf")
            paper_folder = data_root / notes["record_id"]

            with patch.dict(os.environ, {"PAPER_NOTES_DATA_ROOT": str(data_root)}):
                app = AppTest.from_file(PROJECT_ROOT / "app.py")
                app.session_state["main_menu"] = True
                app.run(timeout=30)
                delete_button = next(button for button in app.button if button.label == "🗑 Delete paper")
                delete_button.click().run(timeout=30)
                self.assertTrue(paper_folder.is_dir())

                confirm_button = next(
                    button for button in app.button if button.label == "Permanently delete this paper"
                )
                self.assertTrue(confirm_button.disabled)
                confirmation = next(
                    checkbox
                    for checkbox in app.checkbox
                    if checkbox.label == "I understand this cannot be undone."
                )
                confirmation.check().run(timeout=30)
                confirm_button = next(
                    button for button in app.button if button.label == "Permanently delete this paper"
                )
                confirm_button.click().run(timeout=30)

            self.assertFalse(paper_folder.exists())
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(any("cannot be recovered" in item.value for item in app.success))


if __name__ == "__main__":
    unittest.main()
