import json
import tempfile
import unittest
from pathlib import Path

from paper_notes.extractor_runs import (
    create_extraction_run,
    list_extraction_runs,
    move_approved_images_to_trash,
    move_extraction_run_to_trash,
    rename_extraction_run,
)


class ExtractorRunTests(unittest.TestCase):
    def test_create_and_list_run_uses_chosen_title(self):
        with tempfile.TemporaryDirectory() as temporary:
            output_root = Path(temporary) / "extracted"
            run_folder = create_extraction_run(output_root, "My Preferred Title", "input.pdf")

            self.assertEqual(run_folder.name, "my-preferred-title")
            self.assertTrue((run_folder / "approved").is_dir())
            self.assertTrue((run_folder / "work").is_dir())
            runs = list_extraction_runs(output_root)
            self.assertEqual(runs[0]["title"], "My Preferred Title")
            self.assertEqual(runs[0]["original_pdf_name"], "input.pdf")

            duplicate = create_extraction_run(
                output_root, "My Preferred Title", "another.pdf"
            )
            self.assertEqual(duplicate.name, "my-preferred-title-2")

    def test_legacy_run_is_listed_without_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            output_root = Path(temporary) / "extracted"
            legacy = output_root / "older-paper-20261001-083743-97dae7"
            (legacy / "approved").mkdir(parents=True)
            (legacy / "work").mkdir()

            runs = list_extraction_runs(output_root)

            self.assertEqual(runs[0]["title"], "older paper")
            self.assertEqual(runs[0]["path"], legacy)

    def test_rename_changes_title_and_folder_name(self):
        with tempfile.TemporaryDirectory() as temporary:
            output_root = Path(temporary) / "extracted"
            original = create_extraction_run(output_root, "Original", "paper.pdf")
            renamed = rename_extraction_run(output_root, original, "Clear New Name")

            self.assertFalse(original.exists())
            self.assertEqual(renamed.name, "clear-new-name")
            metadata = json.loads((renamed / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["title"], "Clear New Name")

    def test_approved_images_and_runs_are_moved_to_recoverable_trash(self):
        with tempfile.TemporaryDirectory() as temporary:
            output_root = Path(temporary) / "extracted"
            run_folder = create_extraction_run(output_root, "Disposable", "paper.pdf")
            approved = run_folder / "approved" / "Figure 1.png"
            approved.write_bytes(b"image")

            moved_images = move_approved_images_to_trash(
                output_root, run_folder, [approved.name]
            )
            self.assertFalse(approved.exists())
            self.assertTrue(moved_images[0].is_file())
            self.assertEqual(
                moved_images[0].parent.resolve(),
                (run_folder / ".trash" / "approved").resolve(),
            )

            moved_run = move_extraction_run_to_trash(output_root, run_folder)
            self.assertFalse(run_folder.exists())
            self.assertTrue(moved_run.is_dir())
            self.assertEqual(moved_run.parent.resolve(), (output_root / ".trash").resolve())
            self.assertEqual(list_extraction_runs(output_root), [])

    def test_file_removal_rejects_paths_outside_approved_folder(self):
        with tempfile.TemporaryDirectory() as temporary:
            output_root = Path(temporary) / "extracted"
            run_folder = create_extraction_run(output_root, "Safe", "paper.pdf")

            with self.assertRaises(ValueError):
                move_approved_images_to_trash(output_root, run_folder, ["../source.pdf"])


if __name__ == "__main__":
    unittest.main()
