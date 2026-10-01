import tempfile
import unittest
from pathlib import Path

import fitz
from PIL import Image

from paper_notes.exporters import build_markdown, export_docx
from paper_notes.extraction import (
    accept_candidate,
    approve_candidate_files,
    extract_pdf,
    load_manifest,
    reject_candidate,
    save_manual_crop,
    save_record_manual_crop,
    write_proposal,
)
from paper_notes.storage import create_record, load_notes, save_notes


def fixture_pdf(pages: list[list[tuple[str, float, float]]]) -> bytes:
    document = fitz.open()
    for lines in pages:
        page = document.new_page(width=612, height=792)
        for text, y, size in lines:
            page.insert_text((54, y), text, fontsize=size)
    payload = document.tobytes()
    document.close()
    return payload


def save_fixture(path: Path, pages: list[list[tuple[str, float, float]]]) -> Path:
    path.write_bytes(fixture_pdf(pages))
    return path


class ExtractionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_caption_recognition_and_appendix_filtering(self):
        pdf = save_fixture(
            self.root / "main-and-appendix.pdf",
            [
                [("Table 1: Main estimates", 90, 12), ("Coefficient  Standard error", 150, 10)],
                [("Figure 2 - Event study", 600, 12)],
                [("Appendix", 55, 18), ("Table A1: Robustness", 120, 12)],
            ],
        )
        result = extract_pdf(pdf)
        self.assertEqual([item["identifier"] for item in result["candidates"]], ["Table 1", "Figure 2"])
        self.assertEqual(result["appendix"]["page_number"], 3)
        self.assertEqual(result["excluded_appendix_candidates"], 1)

        included = extract_pdf(pdf, include_appendix=True)
        self.assertEqual(len(included["candidates"]), 3)
        self.assertTrue(included["candidates"][-1]["appendix"])

    def test_fresh_queues_can_add_change_and_remove_explicit_appendix_boundary(self):
        pdf = save_fixture(
            self.root / "rerun-settings.pdf",
            [
                [("Table 1: Main", 90, 12)],
                [("Table 2: Later", 90, 12)],
                [("Table A1: Appendix", 90, 12)],
            ],
        )
        proposal_root = self.root / "queues"
        first = write_proposal(pdf, proposal_root, include_appendix=False)
        second = write_proposal(
            pdf, proposal_root, include_appendix=True, appendix_start_page=3
        )
        third = write_proposal(
            pdf, proposal_root, include_appendix=False, appendix_start_page=2
        )
        fourth = write_proposal(pdf, proposal_root, include_appendix=True)
        self.assertEqual(len({first.parent, second.parent, third.parent, fourth.parent}), 4)
        self.assertEqual(load_manifest(second)["settings"], {
            "include_appendix": True, "appendix_start_page": 3
        })
        self.assertEqual(load_manifest(third)["appendix"]["page_number"], 2)
        self.assertIsNone(load_manifest(fourth)["settings"]["appendix_start_page"])
        self.assertEqual(len(list(proposal_root.glob("extraction-*/manifest.json"))), 4)

    def test_invalid_explicit_appendix_page_creates_no_queue(self):
        pdf = save_fixture(self.root / "short.pdf", [[("Table 1: Main", 90, 12)]])
        proposal_root = self.root / "queues"
        with self.assertRaises(ValueError):
            write_proposal(pdf, proposal_root, appendix_start_page=2)
        self.assertEqual(list(proposal_root.glob("extraction-*")), [])

    def test_explicit_appendix_identifier_is_excluded_before_boundary(self):
        pdf = save_fixture(
            self.root / "appendix-identifier.pdf",
            [[("Table A.1: Supplemental result", 100, 12), ("Table 1: Main result", 400, 12)]],
        )
        result = extract_pdf(pdf)
        self.assertEqual([item["identifier"] for item in result["candidates"]], ["Table 1"])

    def test_repeated_continued_caption_becomes_ordered_panels(self):
        pdf = save_fixture(
            self.root / "continued-table.pdf",
            [
                [("Table 2: Main results", 80, 12), ("First page rows", 180, 10)],
                [("Table 2 (continued)", 80, 12), ("Second page rows", 180, 10)],
            ],
        )
        result = extract_pdf(pdf)
        self.assertEqual(len(result["candidates"]), 1)
        parts = result["candidates"][0]["parts"]
        self.assertEqual([part["panel_label"] for part in parts], ["panel_A", "panel_B"])
        self.assertEqual([part["page_number"] for part in parts], [1, 2])

    def test_identical_figure_caption_on_consecutive_pages_becomes_panels(self):
        pdf = save_fixture(
            self.root / "split-figure.pdf",
            [
                [("Figure 4: Outcomes by year", 650, 12)],
                [("Figure 4: Outcomes by year", 650, 12)],
            ],
        )

    def test_roman_numerals_and_sideways_continuation_are_detected_and_rotated(self):
        pdf = self.root / "roman-sideways.pdf"
        document = fitz.open()
        for _ in range(2):
            page = document.new_page(width=430, height=650)
            page.insert_text((70, 350), "TABLE I", fontsize=12, rotate=90)
            page.insert_text((150, 500), "Table values", fontsize=11, rotate=90)
        document.save(pdf)
        document.close()

        manifest_path = write_proposal(pdf, self.root / "roman-proposals")
        manifest = load_manifest(manifest_path)
        self.assertEqual(len(manifest["candidates"]), 1)
        candidate = manifest["candidates"][0]
        self.assertEqual(candidate["identifier"], "Table I")
        self.assertEqual([part["rotate_degrees"] for part in candidate["parts"]], [90, 90])

        approved = approve_candidate_files(
            pdf,
            manifest_path,
            self.root / "roman-approved",
            candidate["id"],
            "Table I",
        )
        self.assertEqual(
            [path.name for path in approved],
            ["Table I_[panel_A].png", "Table I_[panel_B].png"],
        )
        with Image.open(approved[0]) as image:
            self.assertGreater(image.width, image.height)
        result = extract_pdf(pdf)
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(
            [part["panel_label"] for part in result["candidates"][0]["parts"]],
            ["panel_A", "panel_B"],
        )

    def test_duplicate_nonconsecutive_labels_remain_separate_for_review(self):
        pdf = save_fixture(
            self.root / "duplicates.pdf",
            [
                [("Figure 1: First occurrence", 100, 12)],
                [("Body text only", 100, 11)],
                [("Figure 1: Duplicate occurrence", 100, 12)],
            ],
        )
        result = extract_pdf(pdf)
        self.assertEqual(len(result["candidates"]), 2)
        self.assertNotEqual(result["candidates"][0]["id"], result["candidates"][1]["id"])

    def test_figure_reference_inside_body_paragraph_is_not_a_caption(self):
        pdf = self.root / "body-reference.pdf"
        document = fitz.open()
        body_page = document.new_page(width=612, height=792)
        body_page.insert_textbox(
            fitz.Rect(60, 100, 552, 300),
            "The design uses the statutory limit.\n"
            "Figure 1. For municipalities in the estimation sample, the result is stable.\n"
            "This paragraph continues with the empirical discussion.\n"
            "A final sentence completes the paragraph.",
            fontsize=11,
        )
        caption_page = document.new_page(width=612, height=792)
        caption_page.insert_text((170, 650), "Figure 1: Campaign Spending", fontsize=12)
        document.save(pdf)
        document.close()

        result = extract_pdf(pdf)
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(result["candidates"][0]["parts"][0]["page_number"], 2)

    def test_image_only_page_is_reported_instead_of_guessed(self):
        pdf = save_fixture(self.root / "image-only.pdf", [[]])
        result = extract_pdf(pdf)
        self.assertEqual(result["candidates"], [])
        self.assertTrue(any("manual review" in warning for warning in result["warnings"]))

    def test_proposal_acceptance_creates_panel_files_and_preserves_notes(self):
        pdf_bytes = fixture_pdf(
            [
                [("Table 2: Main results", 80, 12), ("First page rows", 180, 10)],
                [("Table 2 (continued)", 80, 12), ("Second page rows", 180, 10)],
            ]
        )
        data_root = self.root / "papers"
        notes = create_record(data_root, "Multipage", pdf_bytes, "paper.pdf")
        notes["sections"]["one_minute_summary"] = "Keep my exact words."
        save_notes(data_root, notes)
        folder = data_root / notes["record_id"]
        unrelated = folder / "exhibits" / "Table 2_[panel_A].png"
        unrelated.write_bytes(b"unrelated file")

        manifest_path = write_proposal(folder / "source.pdf", folder / "extraction_proposals")
        manifest = load_manifest(manifest_path)
        candidate = manifest["candidates"][0]
        accepted = accept_candidate(
            data_root,
            notes["record_id"],
            manifest_path,
            candidate["id"],
            "Table 2: Main results",
        )

        loaded = load_notes(data_root, notes["record_id"])
        self.assertEqual(loaded["sections"]["one_minute_summary"], "Keep my exact words.")
        self.assertEqual(len(loaded["exhibits"]), 1)
        self.assertEqual(len(accepted["images"]), 2)
        names = [Path(part["image"]).name for part in accepted["images"]]
        self.assertEqual(names, ["Table 2_[panel_A]_2.png", "Table 2_[panel_B].png"])
        self.assertEqual(unrelated.read_bytes(), b"unrelated file")
        for part in accepted["images"]:
            self.assertTrue((folder / part["image"]).is_file())

        markdown = build_markdown(loaded)
        self.assertIn("panel A", markdown)
        self.assertIn("panel B", markdown)
        output = export_docx(data_root, loaded)
        self.assertTrue(output.is_file())

    def test_acceptance_is_idempotent(self):
        data_root = self.root / "papers"
        notes = create_record(
            data_root,
            "Idempotent",
            fixture_pdf([[("Figure 1: Result", 100, 12)]]),
            "paper.pdf",
        )
        folder = data_root / notes["record_id"]
        manifest_path = write_proposal(folder / "source.pdf", folder / "extraction_proposals")
        candidate = load_manifest(manifest_path)["candidates"][0]
        first = accept_candidate(data_root, notes["record_id"], manifest_path, candidate["id"], "Figure 1")
        second = accept_candidate(data_root, notes["record_id"], manifest_path, candidate["id"], "Figure 1")
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(load_notes(data_root, notes["record_id"])["exhibits"]), 1)

    def test_standalone_approval_saves_only_reviewed_image_files(self):
        pdf = save_fixture(
            self.root / "standalone.pdf",
            [[("Figure 7: Standalone result", 650, 12)]],
        )
        manifest_path = write_proposal(pdf, self.root / "proposals")
        candidate = load_manifest(manifest_path)["candidates"][0]
        approved = approve_candidate_files(
            pdf,
            manifest_path,
            self.root / "approved",
            candidate["id"],
            "Figure 7: Standalone result",
        )
        self.assertEqual([path.name for path in approved], ["Figure 7.png"])
        self.assertTrue(approved[0].is_file())
        self.assertEqual(load_manifest(manifest_path)["candidates"][0]["status"], "accepted")

    def test_manual_crop_is_collision_safe(self):
        pdf = save_fixture(self.root / "manual.pdf", [[("Manual page", 100, 12)]])
        output = self.root / "manual-approved"
        crop = {"left": 0, "top": 0, "right": 100, "bottom": 100}
        first = save_manual_crop(pdf, output, "Figure I", 1, crop)
        second = save_manual_crop(pdf, output, "Figure I", 1, crop)
        self.assertEqual(first.name, "Figure I.png")
        self.assertEqual(second.name, "Figure I_2.png")

    def test_record_manual_crop_is_saved_and_attached_atomically(self):
        data_root = self.root / "papers"
        notes = create_record(
            data_root,
            "Manual linked crop",
            fixture_pdf([[("Manual page", 100, 12)]]),
            "paper.pdf",
        )
        exhibit = save_record_manual_crop(
            data_root,
            notes["record_id"],
            "Figure M",
            1,
            {"left": 0, "top": 0, "right": 100, "bottom": 100},
        )
        loaded = load_notes(data_root, notes["record_id"])
        self.assertEqual(loaded["exhibits"][0]["id"], exhibit["id"])
        self.assertTrue((data_root / notes["record_id"] / exhibit["image"]).is_file())

    def test_stale_organizer_save_preserves_new_linked_exhibit(self):
        data_root = self.root / "papers"
        notes = create_record(
            data_root,
            "Concurrent linked crop",
            fixture_pdf([[("Figure 1: Result", 100, 12)]]),
            "paper.pdf",
        )
        stale_organizer_copy = load_notes(data_root, notes["record_id"])
        save_record_manual_crop(
            data_root,
            notes["record_id"],
            "Figure 1",
            1,
            {"left": 0, "top": 0, "right": 100, "bottom": 100},
        )
        stale_organizer_copy["sections"]["additional_notes"] = "Keep this new note too."
        save_notes(data_root, stale_organizer_copy)

        merged = load_notes(data_root, notes["record_id"])
        self.assertEqual(merged["sections"]["additional_notes"], "Keep this new note too.")
        self.assertEqual([item["identifier_title"] for item in merged["exhibits"]], ["Figure 1"])

    def test_fresh_review_queue_can_approve_a_new_version(self):
        data_root = self.root / "papers"
        notes = create_record(
            data_root,
            "Fresh review",
            fixture_pdf([[("Figure 1: Result", 100, 12)]]),
            "paper.pdf",
        )
        folder = data_root / notes["record_id"]
        first_manifest = write_proposal(folder / "source.pdf", folder / "extraction_proposals")
        first_candidate = load_manifest(first_manifest)["candidates"][0]
        accept_candidate(
            data_root, notes["record_id"], first_manifest, first_candidate["id"], "Figure 1"
        )

        second_manifest = write_proposal(folder / "source.pdf", folder / "extraction_proposals")
        second_candidate = load_manifest(second_manifest)["candidates"][0]
        accept_candidate(
            data_root,
            notes["record_id"],
            second_manifest,
            second_candidate["id"],
            "Figure 1 revised",
        )

        loaded = load_notes(data_root, notes["record_id"])
        self.assertEqual(
            [item["identifier_title"] for item in loaded["exhibits"]],
            ["Figure 1", "Figure 1 revised"],
        )

    def test_changed_source_fails_without_modifying_record(self):
        data_root = self.root / "papers"
        notes = create_record(
            data_root,
            "Changed source",
            fixture_pdf([[("Table 1: Original", 100, 12)]]),
            "paper.pdf",
        )
        folder = data_root / notes["record_id"]
        manifest_path = write_proposal(folder / "source.pdf", folder / "extraction_proposals")
        candidate = load_manifest(manifest_path)["candidates"][0]
        folder.joinpath("source.pdf").write_bytes(fixture_pdf([[("Table 9: Replacement", 100, 12)]]))

        with self.assertRaisesRegex(ValueError, "source PDF changed"):
            accept_candidate(data_root, notes["record_id"], manifest_path, candidate["id"], "Table 1")
        self.assertEqual(load_notes(data_root, notes["record_id"])["exhibits"], [])
        self.assertEqual(list((folder / "exhibits").iterdir()), [])

    def test_rejection_changes_only_proposal_manifest(self):
        data_root = self.root / "papers"
        notes = create_record(
            data_root,
            "Reject",
            fixture_pdf([[("Figure 3: Candidate", 100, 12)]]),
            "paper.pdf",
        )
        folder = data_root / notes["record_id"]
        manifest_path = write_proposal(folder / "source.pdf", folder / "extraction_proposals")
        candidate = load_manifest(manifest_path)["candidates"][0]
        reject_candidate(manifest_path, candidate["id"])
        self.assertEqual(load_manifest(manifest_path)["candidates"][0]["status"], "rejected")
        self.assertEqual(load_notes(data_root, notes["record_id"])["exhibits"], [])


if __name__ == "__main__":
    unittest.main()
