from __future__ import annotations

import io
import os
import subprocess
import tempfile
import zipfile
from pathlib import Path

import streamlit as st
from streamlit_cropper import st_cropper

from paper_notes.extraction import (
    accept_candidate,
    approve_candidate_files,
    create_record_proposal,
    latest_record_proposal,
    load_manifest,
    reject_candidate,
    save_manual_crop,
    save_record_manual_crop,
    write_proposal,
)
from paper_notes.extractor_runs import (
    create_extraction_run,
    list_extraction_runs,
    move_approved_images_to_trash,
    move_extraction_run_to_trash,
    rename_extraction_run,
)
from paper_notes.pdf_tools import page_count, render_page
from paper_notes.storage import load_notes, record_dir


APP_ROOT = Path(__file__).resolve().parent
EXTRACTOR_STORAGE = Path(
    os.environ.get("WORKFLOW_TOOL_TABLE_FIGURE_EXTRACTOR_STORAGE", APP_ROOT / "extracted_exhibits")
)
OUTPUT_ROOT = Path(
    os.environ.get("PAPER_EXTRACTOR_OUTPUT_ROOT", EXTRACTOR_STORAGE)
)
PAPER_NOTES_STORAGE = Path(
    os.environ.get("WORKFLOW_TOOL_PAPER_NOTES_STORAGE", APP_ROOT / "data")
)
DATA_ROOT = Path(os.environ.get("PAPER_NOTES_DATA_ROOT", PAPER_NOTES_STORAGE / "papers"))
ORGANIZER_URL = os.environ.get(
    "PAPER_NOTES_URL",
    os.environ.get("WORKFLOW_TOOL_PAPER_NOTES_URL", "http://localhost:8501"),
).rstrip("/")
EXTRACTOR_URL = os.environ.get(
    "PAPER_EXTRACTOR_URL",
    os.environ.get("WORKFLOW_TOOL_TABLE_FIGURE_EXTRACTOR_URL", "http://localhost:8502"),
).rstrip("/")

st.set_page_config(page_title="Table and Figure Extractor", page_icon="✂️", layout="wide")
st.title("Table and Figure Extractor")
st.caption(
    "A standalone, deterministic PDF cropping tool. Nothing is interpreted or sent anywhere."
)


@st.cache_data(show_spinner=False)
def cached_render(pdf_path: str, modified_ns: int, page_number: int, zoom: float = 1.0):
    del modified_ns
    return render_page(Path(pdf_path), page_number, zoom)


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(handle, "wb") as temporary:
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def crop_selector(
    pdf_path: Path,
    page_number: int,
    crop_metadata: dict,
    key: str,
) -> dict[str, float]:
    modified_ns = pdf_path.stat().st_mtime_ns
    preview = cached_render(str(pdf_path), modified_ns, page_number, 1.0)
    default_coords = (
        round(preview.width * float(crop_metadata.get("left", 0)) / 100),
        round(preview.width * float(crop_metadata.get("right", 100)) / 100),
        round(preview.height * float(crop_metadata.get("top", 0)) / 100),
        round(preview.height * float(crop_metadata.get("bottom", 100)) / 100),
    )
    crop_box = st_cropper(
        preview,
        realtime_update=True,
        default_coords=default_coords,
        box_color="#8A4B2A",
        aspect_ratio=None,
        return_type="box",
        key=key,
        stroke_width=3,
    )
    left = max(0, int(crop_box.get("left", 0)))
    top = max(0, int(crop_box.get("top", 0)))
    right = min(preview.width, left + max(0, int(crop_box.get("width", 0))))
    bottom = min(preview.height, top + max(0, int(crop_box.get("height", 0))))
    if right <= left or bottom <= top:
        st.warning("Choose a crop with some width and height.")
        return {
            crop_key: float(crop_metadata[crop_key])
            for crop_key in ("left", "top", "right", "bottom")
        }
    st.image(preview.crop((left, top, right, bottom)), caption="Selected crop", width="stretch")
    return {
        "left": 100 * left / preview.width,
        "top": 100 * top / preview.height,
        "right": 100 * right / preview.width,
        "bottom": 100 * bottom / preview.height,
    }


def approved_zip(approved_folder: Path) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(approved_folder.glob("*.png")):
            archive.write(path, arcname=path.name)
    return buffer.getvalue()


def return_to_main_menu() -> None:
    st.session_state.pop("extractor_run_folder", None)
    st.session_state.pop("extractor_manifest", None)
    st.session_state.pop("extractor_creating", None)
    st.session_state.pop("standalone_review_candidate", None)


def open_in_finder(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(["open", str(folder)], check=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as error:
        st.error(f"The folder could not be opened: {error}")


def open_run(run: dict) -> None:
    st.session_state["extractor_run_folder"] = str(run["path"])
    if run.get("latest_manifest"):
        st.session_state["extractor_manifest"] = str(run["latest_manifest"])
    else:
        st.session_state.pop("extractor_manifest", None)


def linked_record_images(notes: dict, folder: Path) -> list[Path]:
    images: list[Path] = []
    seen: set[Path] = set()
    for exhibit in notes.get("exhibits", []):
        relative_paths = [exhibit.get("image")]
        relative_paths.extend(
            part.get("image")
            for part in exhibit.get("images", [])
            if isinstance(part, dict)
        )
        for relative in relative_paths:
            if not relative:
                continue
            candidate = (folder / str(relative)).resolve()
            exhibits = (folder / "exhibits").resolve()
            if candidate.is_file() and (candidate == exhibits or exhibits in candidate.parents):
                if candidate not in seen:
                    images.append(candidate)
                    seen.add(candidate)
    return images


def show_linked_record(record_id: str) -> None:
    try:
        notes = load_notes(DATA_ROOT, record_id)
        folder = record_dir(DATA_ROOT, record_id)
    except (OSError, ValueError) as error:
        st.error(f"This Paper Notes record could not be opened: {error}")
        st.link_button("Open the extractor library", EXTRACTOR_URL)
        return

    source_pdf = folder / "source.pdf"
    if not source_pdf.is_file():
        st.error("This paper record does not contain its source PDF.")
        return

    st.subheader(notes["title"])
    st.caption(
        "Linked Paper Notes record · the existing source PDF is used in place; "
        "approved screenshots are added directly to this paper's Tables and Figures section."
    )
    action_col, location_col = st.columns([1, 3])
    with action_col:
        st.link_button("Open Paper Notes ↗", ORGANIZER_URL, width="stretch")
    with location_col:
        st.caption(f"Unified paper folder: {folder}")

    manifest_key = f"linked_manifest_{record_id}"
    manifest_value = st.session_state.get(manifest_key)
    manifest_path = Path(manifest_value) if manifest_value else latest_record_proposal(DATA_ROOT, record_id)
    current_manifest = (
        load_manifest(manifest_path)
        if manifest_path is not None and manifest_path.is_file()
        else {}
    )
    current_settings = current_manifest.get("settings", {})
    current_boundary = current_settings.get("appendix_start_page")

    with st.expander("Analyze or re-analyze this PDF", expanded=manifest_path is None):
        mode = st.radio(
            "Extraction mode",
            ["Main text only", "Include appendix exhibits"],
            horizontal=True,
            index=1 if current_settings.get("include_appendix") else 0,
            key=f"linked_mode_{record_id}",
        )
        manual_boundary = st.checkbox(
            "I want to specify the appendix starting page",
            value=current_boundary is not None,
            key=f"linked_boundary_enabled_{record_id}",
        )
        appendix_start = None
        if manual_boundary:
            appendix_start = int(
                st.number_input(
                    "Appendix begins on PDF page",
                    min_value=1,
                    max_value=page_count(source_pdf),
                    value=int(current_boundary or 1),
                    step=1,
                    key=f"linked_boundary_{record_id}",
                )
            )
        analyze_label = "Analyze PDF" if manifest_path is None else "Create a fresh review queue"
        if st.button(analyze_label, type="primary", key=f"linked_analyze_{record_id}"):
            try:
                manifest_path = create_record_proposal(
                    DATA_ROOT,
                    record_id,
                    include_appendix=mode == "Include appendix exhibits",
                    appendix_start_page=appendix_start,
                )
            except (OSError, RuntimeError, ValueError) as error:
                st.error(f"The PDF could not be analyzed: {error}")
            else:
                st.session_state[manifest_key] = str(manifest_path)
                st.session_state.pop(f"linked_candidate_{record_id}", None)
                st.rerun()

    if manifest_path is not None and manifest_path.is_file():
        manifest = load_manifest(manifest_path)
        settings = manifest.get("settings", {})
        st.info(
            "Appendix exhibits are "
            + ("included." if settings.get("include_appendix") else "excluded (main text only).")
        )
        appendix = manifest.get("appendix", {})
        if appendix.get("page_number"):
            st.caption(
                f"Appendix boundary: page {appendix['page_number']} "
                f"({appendix.get('confidence', 'unknown')}). {appendix.get('reason', '')}"
            )
        else:
            st.caption(f"Appendix boundary: not established. {appendix.get('reason', '')}")
        if manifest.get("excluded_appendix_candidates"):
            st.info(
                f"Excluded {manifest['excluded_appendix_candidates']} appendix candidate(s)."
            )
        for warning in manifest.get("warnings", []):
            st.warning(warning)

        accepted = [item for item in manifest.get("candidates", []) if item.get("status") == "accepted"]
        rejected = [item for item in manifest.get("candidates", []) if item.get("status") == "rejected"]
        pending = [item for item in manifest.get("candidates", []) if item.get("status") == "pending"]
        st.progress((len(accepted) + len(rejected)) / max(len(manifest.get("candidates", [])), 1))
        st.caption(
            f"{len(accepted)} approved · {len(rejected)} rejected · "
            f"{len(pending)} awaiting review"
        )

        if pending:
            st.subheader("Review proposed screenshots")
            candidate_by_id = {candidate["id"]: candidate for candidate in pending}
            selected_id = st.selectbox(
                "Screenshot to review",
                list(candidate_by_id),
                format_func=lambda candidate_id: (
                    candidate_by_id[candidate_id].get("title")
                    or candidate_by_id[candidate_id].get("identifier")
                    or "Unlabeled candidate"
                ),
                key=f"linked_candidate_{record_id}",
            )
            candidate = candidate_by_id[selected_id]
            label = candidate.get("title") or candidate.get("identifier") or "Unlabeled candidate"
            st.caption(
                f"Confidence: {candidate.get('confidence', 'review')} · "
                + " ".join(candidate.get("reasons", []))
            )
            title = st.text_input(
                "Approved title",
                value=label,
                key=f"linked_title_{record_id}_{candidate['id']}",
            )
            crop_values: list[dict[str, float]] = []
            for index, part in enumerate(candidate.get("parts", [])):
                panel = part.get("panel_label")
                panel_text = (
                    panel.replace("_", " ").replace("panel ", "Panel ", 1)
                    if panel
                    else "Image"
                )
                st.markdown(f"**{panel_text} · PDF page {part['page_number']}**")
                preview_path = manifest_path.parent / str(part.get("preview", ""))
                if preview_path.is_file():
                    st.image(str(preview_path), caption="Proposed screenshot", width="stretch")
                adjust = st.checkbox(
                    f"Manually adjust {panel_text.lower()}",
                    key=f"linked_adjust_{record_id}_{candidate['id']}_{index}",
                )
                if adjust:
                    crop = crop_selector(
                        source_pdf,
                        int(part["page_number"]),
                        part["crop"],
                        f"linked_crop_{record_id}_{candidate['id']}_{index}",
                    )
                else:
                    crop = {
                        key: float(part["crop"][key])
                        for key in ("left", "top", "right", "bottom")
                    }
                crop_values.append(crop)

            approve_col, reject_col = st.columns(2)
            if approve_col.button(
                "Approve and add to Paper Notes", type="primary", width="stretch"
            ):
                try:
                    accept_candidate(
                        DATA_ROOT,
                        record_id,
                        manifest_path,
                        candidate["id"],
                        title,
                        crop_values,
                    )
                except (OSError, RuntimeError, ValueError) as error:
                    st.error(f"The crop was not saved: {error}")
                else:
                    st.session_state.pop(f"linked_candidate_{record_id}", None)
                    st.rerun()
            if reject_col.button("Reject", width="stretch"):
                try:
                    reject_candidate(manifest_path, candidate["id"])
                except (OSError, ValueError) as error:
                    st.error(f"The proposal was not rejected: {error}")
                else:
                    st.session_state.pop(f"linked_candidate_{record_id}", None)
                    st.rerun()
        else:
            st.info("This review queue is complete. You can re-analyze or add a manual crop.")
    else:
        st.info("Analyze this paper to create a review queue, or add a manual crop below.")

    with st.expander("Add a manual crop from any PDF page", expanded=manifest_path is None):
        manual_page = int(
            st.number_input(
                "PDF page",
                min_value=1,
                max_value=page_count(source_pdf),
                value=1,
                key=f"linked_manual_page_{record_id}",
            )
        )
        manual_title = st.text_input(
            "Manual screenshot title",
            placeholder="e.g., Figure I or Table I_[panel_A]",
            key=f"linked_manual_title_{record_id}",
        )
        rotation_label = st.selectbox(
            "Rotate saved screenshot",
            ["Keep original orientation", "90° clockwise", "90° counterclockwise", "180°"],
            key=f"linked_manual_rotation_{record_id}",
        )
        rotation = {
            "Keep original orientation": 0,
            "90° clockwise": 90,
            "90° counterclockwise": 270,
            "180°": 180,
        }[rotation_label]
        open_manual_crop = st.checkbox(
            "Open manual cropper", key=f"linked_manual_open_{record_id}"
        )
        if open_manual_crop:
            manual_crop = crop_selector(
                source_pdf,
                manual_page,
                {"left": 0, "top": 0, "right": 100, "bottom": 100},
                f"linked_manual_crop_{record_id}_{manual_page}",
            )
            if st.button(
                "Save and add to Paper Notes",
                type="primary",
                disabled=not manual_title.strip(),
                key=f"linked_manual_save_{record_id}",
            ):
                try:
                    save_record_manual_crop(
                        DATA_ROOT,
                        record_id,
                        manual_title,
                        manual_page,
                        manual_crop,
                        rotate_degrees=rotation,
                    )
                except (OSError, RuntimeError, ValueError) as error:
                    st.error(f"The manual crop was not saved: {error}")
                else:
                    st.success("Manual screenshot added to this paper's Tables and Figures section.")
                    st.rerun()

    approved_paths = linked_record_images(load_notes(DATA_ROOT, record_id), folder)
    if approved_paths:
        with st.expander("View screenshots already attached to this paper", expanded=False):
            columns = st.columns(2)
            for index, path in enumerate(approved_paths):
                with columns[index % 2]:
                    st.image(str(path), caption=path.name, width="stretch")


linked_record = st.query_params.get("record")
if linked_record:
    show_linked_record(str(linked_record))
    st.stop()


notice = st.session_state.pop("extractor_notice", None)
if notice:
    st.success(notice)

run_value = st.session_state.get("extractor_run_folder")
creating = bool(st.session_state.get("extractor_creating"))

if run_value or creating:
    if st.button("← Main menu"):
        return_to_main_menu()
        st.rerun()

if not run_value and not creating:
    new_col, location_col = st.columns([1, 3])
    if new_col.button("＋ New paper", type="primary", width="stretch"):
        st.session_state["extractor_creating"] = True
        st.rerun()
    location_col.caption(f"Extractor library: {OUTPUT_ROOT}")

    runs = list_extraction_runs(OUTPUT_ROOT)
    if not runs:
        st.info("No papers have been uploaded yet. Choose New paper to begin.")

    for run in runs:
        with st.container(border=True):
            st.subheader(run["title"])
            st.caption(
                f"{run['approved_count']} approved screenshot(s) · "
                f"{run['proposal_count']} analysis run(s) · Uploaded as {run['original_pdf_name']}"
            )
            open_col, approved_col, work_col = st.columns(3)
            if open_col.button(
                "Open review",
                key=f"open_run_{run['folder_name']}",
                type="primary",
                width="stretch",
                disabled=run["latest_manifest"] is None,
            ):
                open_run(run)
                st.rerun()
            if approved_col.button(
                "Open approved folder",
                key=f"open_approved_{run['folder_name']}",
                width="stretch",
            ):
                open_in_finder(run["approved_folder"])
            if work_col.button(
                "Open work folder",
                key=f"open_work_{run['folder_name']}",
                width="stretch",
            ):
                open_in_finder(run["work_folder"])
            st.caption(f"Approved: {run['approved_folder']}")
            st.caption(f"Work: {run['work_folder']}")

            with st.expander("Rename or remove files"):
                renamed_title = st.text_input(
                    "Paper title and output-folder name",
                    value=run["title"],
                    key=f"rename_title_{run['folder_name']}",
                )
                if st.button("Save title", key=f"save_title_{run['folder_name']}"):
                    try:
                        rename_extraction_run(OUTPUT_ROOT, run["path"], renamed_title)
                    except (OSError, ValueError) as error:
                        st.error(f"The title could not be changed: {error}")
                    else:
                        st.session_state["extractor_notice"] = (
                            f"Renamed the paper and output folder to “{renamed_title.strip()}”."
                        )
                        st.rerun()

                approved_names = [path.name for path in run["approved_files"]]
                selected_files = st.multiselect(
                    "Approved screenshots to remove",
                    approved_names,
                    key=f"remove_files_{run['folder_name']}",
                    placeholder="Choose one or more screenshots",
                )
                confirm_files = st.checkbox(
                    "Move the selected screenshots to this paper’s Trash folder.",
                    key=f"confirm_files_{run['folder_name']}",
                    disabled=not selected_files,
                )
                if st.button(
                    "Move selected screenshots to Trash",
                    key=f"trash_files_{run['folder_name']}",
                    disabled=not selected_files or not confirm_files,
                ):
                    try:
                        moved = move_approved_images_to_trash(
                            OUTPUT_ROOT, run["path"], selected_files
                        )
                    except (OSError, ValueError) as error:
                        st.error(f"The screenshots could not be moved: {error}")
                    else:
                        st.session_state["extractor_notice"] = (
                            f"Moved {len(moved)} screenshot(s) to this paper’s Trash folder."
                        )
                        st.rerun()

                st.divider()
                confirm_run = st.checkbox(
                    "Move this paper, its PDF, and all extractor files to the library Trash.",
                    key=f"confirm_run_{run['folder_name']}",
                )
                if st.button(
                    "Move paper to Trash",
                    key=f"trash_run_{run['folder_name']}",
                    disabled=not confirm_run,
                ):
                    try:
                        move_extraction_run_to_trash(OUTPUT_ROOT, run["path"])
                    except (OSError, ValueError) as error:
                        st.error(f"The paper could not be moved: {error}")
                    else:
                        st.session_state["extractor_notice"] = (
                            "Moved the paper to the extractor library’s .trash folder."
                        )
                        st.rerun()
    st.stop()

if creating and not run_value:
    st.subheader("Add a paper")
    uploaded = st.file_uploader("Upload a paper PDF", type=["pdf"])
    proposed_title = Path(uploaded.name).stem if uploaded is not None else ""
    paper_title = st.text_input(
        "Paper title and output-folder name",
        value=proposed_title,
        key=f"extractor_new_title_{uploaded.name if uploaded is not None else 'empty'}",
        disabled=uploaded is None,
        help="You can change this later from the main menu.",
    )
    mode = st.radio(
        "Extraction mode",
        ["Main text only", "Include appendix exhibits"],
        horizontal=True,
    )
    manual_boundary = st.checkbox("I want to specify the appendix starting page")
    appendix_start = None
    if manual_boundary:
        appendix_start = int(
            st.number_input("Appendix begins on PDF page", min_value=1, value=1, step=1)
        )
    if st.button(
        "Analyze PDF",
        type="primary",
        disabled=uploaded is None or not paper_title.strip(),
    ):
        assert uploaded is not None
        payload = uploaded.getvalue()
        if not payload.startswith(b"%PDF-"):
            st.error("The uploaded file does not appear to be a PDF.")
        else:
            run_folder = None
            try:
                run_folder = create_extraction_run(
                    OUTPUT_ROOT, paper_title, uploaded.name
                )
                source_pdf = run_folder / "source.pdf"
                atomic_write(source_pdf, payload)
                if appendix_start and appendix_start > page_count(source_pdf):
                    raise ValueError(
                        f"Appendix start must be between 1 and {page_count(source_pdf)}."
                    )
                manifest_path = write_proposal(
                    source_pdf,
                    run_folder / "work",
                    include_appendix=mode == "Include appendix exhibits",
                    appendix_start_page=appendix_start,
                )
            except (OSError, RuntimeError, ValueError) as error:
                if run_folder is not None and run_folder.is_dir():
                    try:
                        move_extraction_run_to_trash(OUTPUT_ROOT, run_folder)
                    except (OSError, ValueError):
                        pass
                st.error(f"The PDF could not be analyzed: {error}")
            else:
                st.session_state["extractor_run_folder"] = str(run_folder)
                st.session_state["extractor_manifest"] = str(manifest_path)
                st.session_state.pop("extractor_creating", None)
                st.rerun()
    st.stop()

run_folder = Path(run_value)
source_pdf = run_folder / "source.pdf"
manifest_path = Path(st.session_state.get("extractor_manifest", ""))
if not manifest_path.is_file():
    manifests = sorted((run_folder / "work").glob("extraction-*/manifest.json"), reverse=True)
    if not manifests:
        st.error("This extraction run has no readable proposal manifest.")
        st.stop()
    manifest_path = manifests[0]
approved_folder = run_folder / "approved"
manifest = load_manifest(manifest_path)

st.success(f"Approved screenshots are saved in: {approved_folder}")
settings = manifest.get("settings", {})
st.info(
    "Appendix exhibits are "
    + ("included." if settings.get("include_appendix") else "excluded (main text only).")
)
appendix = manifest.get("appendix", {})
if appendix.get("page_number"):
    st.caption(
        f"Appendix boundary: page {appendix['page_number']} "
        f"({appendix.get('confidence', 'unknown')}). {appendix.get('reason', '')}"
    )
else:
    st.caption(f"Appendix boundary: not established. {appendix.get('reason', '')}")
if manifest.get("excluded_appendix_candidates"):
    st.info(f"Excluded {manifest['excluded_appendix_candidates']} appendix candidate(s).")
for warning in manifest.get("warnings", []):
    st.warning(warning)

with st.expander("Re-analyze this PDF", expanded=False):
    st.caption("Creates a fresh review queue. Existing approved screenshots are left untouched.")
    rerun_mode = st.radio(
        "Extraction mode",
        ["Main text only", "Include appendix exhibits"],
        horizontal=True,
        index=1 if settings.get("include_appendix") else 0,
        key=f"standalone_rerun_mode_{manifest.get('proposal_id', '')}",
    )
    previous_boundary = settings.get("appendix_start_page")
    rerun_manual_boundary = st.checkbox(
        "I want to specify the appendix starting page",
        value=previous_boundary is not None,
        key=f"standalone_rerun_boundary_enabled_{manifest.get('proposal_id', '')}",
    )
    rerun_appendix_start = None
    if rerun_manual_boundary:
        rerun_appendix_start = int(
            st.number_input(
                "Appendix begins on PDF page",
                min_value=1,
                max_value=page_count(source_pdf),
                value=int(previous_boundary or 1),
                step=1,
                key=f"standalone_rerun_boundary_{manifest.get('proposal_id', '')}",
            )
        )
    if st.button("Create a fresh review queue", type="primary"):
        try:
            refreshed_manifest = write_proposal(
                source_pdf,
                run_folder / "work",
                include_appendix=rerun_mode == "Include appendix exhibits",
                appendix_start_page=rerun_appendix_start,
            )
        except (OSError, RuntimeError, ValueError) as error:
            st.error(f"The PDF could not be re-analyzed: {error}")
        else:
            st.session_state["extractor_manifest"] = str(refreshed_manifest)
            st.session_state.pop("standalone_review_candidate", None)
            st.rerun()

accepted = [item for item in manifest.get("candidates", []) if item.get("status") == "accepted"]
rejected = [item for item in manifest.get("candidates", []) if item.get("status") == "rejected"]
pending = [item for item in manifest.get("candidates", []) if item.get("status") == "pending"]
st.progress((len(accepted) + len(rejected)) / max(len(manifest.get("candidates", [])), 1))
st.caption(f"{len(accepted)} approved · {len(rejected)} rejected · {len(pending)} awaiting review")

if any(approved_folder.glob("*.png")):
    st.download_button(
        "Download all approved screenshots (.zip)",
        data=approved_zip(approved_folder),
        file_name=f"{run_folder.name}-approved.zip",
        mime="application/zip",
    )

    with st.expander("View approved screenshots", expanded=False):
        approved_paths = sorted(approved_folder.glob("*.png"))
        columns = st.columns(2)
        for index, path in enumerate(approved_paths):
            with columns[index % 2]:
                st.image(str(path), caption=path.name, width="stretch")

if not pending:
    st.info("Review complete. Use the approved folder above, or download the ZIP file.")

if pending:
    st.subheader("Review proposed screenshots")
    candidate_by_id = {candidate["id"]: candidate for candidate in pending}
    selected_id = st.selectbox(
        "Screenshot to review",
        list(candidate_by_id),
        format_func=lambda candidate_id: (
            candidate_by_id[candidate_id].get("title")
            or candidate_by_id[candidate_id].get("identifier")
            or "Unlabeled candidate"
        ),
        key="standalone_review_candidate",
    )
    candidate = candidate_by_id[selected_id]
    label = candidate.get("title") or candidate.get("identifier") or "Unlabeled candidate"
    st.caption(
        f"Confidence: {candidate.get('confidence', 'review')} · "
        + " ".join(candidate.get("reasons", []))
    )
    title = st.text_input(
        "Approved filename/title",
        value=label,
        key=f"standalone_title_{candidate['id']}",
        help="The filename will use the table/figure identifier, or this explicit title if needed.",
    )
    crop_values: list[dict[str, float]] = []
    for index, part in enumerate(candidate.get("parts", [])):
        panel = part.get("panel_label")
        panel_text = panel.replace("_", " ").replace("panel ", "Panel ", 1) if panel else "Image"
        st.markdown(f"**{panel_text} · PDF page {part['page_number']}**")
        preview_path = manifest_path.parent / str(part.get("preview", ""))
        if preview_path.is_file():
            st.image(str(preview_path), caption="Proposed screenshot", width="stretch")
        adjust = st.checkbox(
            f"Manually adjust {panel_text.lower()}",
            key=f"standalone_adjust_{candidate['id']}_{index}",
        )
        if adjust:
            if part.get("rotate_degrees"):
                st.caption("The source page is sideways; the saved screenshot will be rotated upright.")
            crop = crop_selector(
                source_pdf,
                int(part["page_number"]),
                part["crop"],
                f"standalone_crop_{candidate['id']}_{index}",
            )
        else:
            crop = {
                crop_key: float(part["crop"][crop_key])
                for crop_key in ("left", "top", "right", "bottom")
            }
        crop_values.append(crop)

    approve_col, reject_col = st.columns(2)
    if approve_col.button("Approve and save", type="primary", width="stretch"):
        try:
            approve_candidate_files(
                source_pdf,
                manifest_path,
                approved_folder,
                candidate["id"],
                title,
                crop_values,
            )
        except (OSError, RuntimeError, ValueError) as error:
            st.error(f"The crop was not saved: {error}")
        else:
            st.session_state.pop("standalone_review_candidate", None)
            st.rerun()
    if reject_col.button("Reject", width="stretch"):
        try:
            reject_candidate(manifest_path, candidate["id"])
        except (OSError, ValueError) as error:
            st.error(f"The proposal was not rejected: {error}")
        else:
            st.session_state.pop("standalone_review_candidate", None)
            st.rerun()

with st.expander("Add a manual crop from any PDF page", expanded=not pending):
    st.caption("Use this if an exhibit was not detected or you want an additional screenshot.")
    manual_page = int(
        st.number_input(
            "PDF page",
            min_value=1,
            max_value=page_count(source_pdf),
            value=1,
            key="standalone_manual_page",
        )
    )
    manual_title = st.text_input(
        "Manual screenshot filename/title",
        placeholder="e.g., Figure I or Table I_[panel_A]",
        key="standalone_manual_title",
    )
    rotation_label = st.selectbox(
        "Rotate saved screenshot",
        ["Keep original orientation", "90° clockwise", "90° counterclockwise", "180°"],
        key="standalone_manual_rotation",
    )
    rotation = {
        "Keep original orientation": 0,
        "90° clockwise": 90,
        "90° counterclockwise": 270,
        "180°": 180,
    }[rotation_label]
    open_manual_crop = st.checkbox("Open manual cropper", key="standalone_manual_crop_open")
    if open_manual_crop:
        manual_crop = crop_selector(
            source_pdf,
            manual_page,
            {"left": 0, "top": 0, "right": 100, "bottom": 100},
            f"standalone_manual_crop_{manual_page}",
        )
        if st.button("Save manual screenshot", type="primary", disabled=not manual_title.strip()):
            try:
                save_manual_crop(
                    source_pdf,
                    approved_folder,
                    manual_title,
                    manual_page,
                    manual_crop,
                    rotate_degrees=rotation,
                )
            except (OSError, RuntimeError, ValueError) as error:
                st.error(f"The manual screenshot was not saved: {error}")
            else:
                st.success("Manual screenshot saved in the approved folder.")
                st.rerun()
