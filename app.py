from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path

import streamlit as st
from streamlit_cropper import st_cropper

from paper_notes.exporters import (
    GROUP_EXPORT_SECTIONS,
    export_combined_papers,
    export_docx,
    export_markdown,
    paper_has_selected_content,
)
from paper_notes.pdf_tools import crop_page, page_count, render_page
from paper_notes.storage import (
    SECTION_LABELS,
    blank_data_source,
    blank_empirical_strategy,
    blank_exhibit,
    content_equal,
    create_record,
    delete_record,
    exhibit_image_path,
    list_records,
    load_notes,
    record_dir,
    remove_exhibit_image,
    remove_exhibit_images,
    save_uploaded_image,
    save_notes,
    section_completion,
    strategy_image_path,
    update_record_title,
)


APP_ROOT = Path(__file__).resolve().parent
PAPER_NOTES_STORAGE = Path(
    os.environ.get("WORKFLOW_TOOL_PAPER_NOTES_STORAGE", APP_ROOT / "data")
)
DATA_ROOT = Path(os.environ.get("PAPER_NOTES_DATA_ROOT", PAPER_NOTES_STORAGE / "papers"))
EXTRACTOR_URL = os.environ.get(
    "PAPER_EXTRACTOR_URL",
    os.environ.get("WORKFLOW_TOOL_TABLE_FIGURE_EXTRACTOR_URL", "http://localhost:8502"),
).rstrip("/")

st.set_page_config(page_title="Paper Notes", page_icon="📚", layout="wide")
st.markdown(
    """
    <style>
    .block-container {padding-top: 1.4rem; padding-bottom: 4rem;}
    [data-testid="stSidebar"] {border-right: 1px solid #e7e4dc;}
    .note-card {padding: .7rem .9rem; background: #f7f5ef; border-radius: .65rem; margin-bottom: .7rem;}
    [data-testid="InputInstructions"] {display: none;}
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_data(show_spinner=False)
def cached_page_count(pdf_path: str, modified_ns: int) -> int:
    del modified_ns
    return page_count(Path(pdf_path))


@st.cache_data(show_spinner=False)
def cached_render(pdf_path: str, modified_ns: int, page_number: int, zoom: float = 1.35):
    del modified_ns
    return render_page(Path(pdf_path), page_number, zoom)


def status_label(complete: bool) -> str:
    return ":green[Complete]" if complete else ":orange[Empty]"


def section_heading(label: str, complete: bool) -> None:
    st.markdown(f"### {label} · {status_label(complete)}")


def multiline(label: str, value: str, key: str, height: int = 170, help_text: str | None = None) -> str:
    return st.text_area(label, value=value, key=key, height=height, help=help_text)


def move_reader_page(page_key: str, change: int, total_pages: int) -> None:
    current = int(st.session_state.get(page_key, 1))
    st.session_state[page_key] = min(total_pages, max(1, current + change))


def set_reader_page(page_key: str, page_number: int) -> None:
    st.session_state[page_key] = int(page_number)


def parse_categories(value: str) -> list[str]:
    categories = []
    seen = set()
    for item in value.split(","):
        category = item.strip()
        normalized = category.casefold()
        if category and normalized not in seen:
            categories.append(category)
            seen.add(normalized)
    return categories


def clear_paper_selection(record_ids: list[str]) -> None:
    for record_id in record_ids:
        st.session_state[f"menu_select_{record_id}"] = False


def save_record_categories(record_id: str, widget_key: str) -> None:
    current = load_notes(DATA_ROOT, record_id)
    updated_categories = parse_categories(str(st.session_state.get(widget_key, "")))
    if updated_categories != current.get("categories", []):
        current["categories"] = updated_categories
        save_notes(DATA_ROOT, current)


def save_record_title(record_id: str, widget_key: str) -> None:
    current = load_notes(DATA_ROOT, record_id)
    updated_title = str(st.session_state.get(widget_key, "")).strip()
    if not updated_title:
        st.session_state[widget_key] = current["title"]
        st.session_state["title_edit_message"] = ("error", "A paper title cannot be blank.")
        return
    if updated_title != current["title"]:
        update_record_title(DATA_ROOT, record_id, updated_title)
        st.session_state["title_edit_message"] = ("success", "Paper title updated.")


def add_category_to_records(record_ids: list[str], category: str) -> None:
    new_category = category.strip()
    if not record_ids or not new_category:
        return
    for record_id in record_ids:
        current = load_notes(DATA_ROOT, record_id)
        existing = current.get("categories", [])
        if new_category.casefold() not in {str(item).casefold() for item in existing}:
            current["categories"] = [*existing, new_category]
            save_notes(DATA_ROOT, current)
        st.session_state[f"menu_categories_{record_id}"] = ", ".join(current["categories"])
    st.session_state["menu_batch_category"] = ""
    st.session_state["category_saved_message"] = (
        f'Added “{new_category}” to {len(record_ids)} '
        f'paper{"s" if len(record_ids) != 1 else ""}.'
    )


def show_paper_deleted_message() -> None:
    message = st.session_state.pop("paper_deleted_message", None)
    if message:
        st.success(message)


def clear_deleted_record_state(record_id: str) -> None:
    for key in [
        f"menu_select_{record_id}",
        f"menu_title_{record_id}",
        f"menu_categories_{record_id}",
        f"confirm_delete_{record_id}",
    ]:
        st.session_state.pop(key, None)
    if st.session_state.get("active_record") == record_id:
        st.session_state.pop("active_record", None)
    st.session_state.pop("delete_candidate", None)


def image_editor(
    *,
    item: dict,
    item_id: str,
    item_kind: str,
    record_id: str,
    page_number: int,
    total_pages: int,
    pdf_path: Path,
    pdf_modified: int,
    folder: Path,
    output_path: Path,
) -> tuple[str | None, dict | None, bool]:
    image_relative = item.get("image")
    crop_metadata = item.get("crop")
    changed = False
    key_prefix = f"image_{item_kind}_{record_id}_{item_id}"

    if image_relative:
        saved_image = folder / image_relative
        if saved_image.is_file():
            image_caption = "Saved crop" if crop_metadata else "Uploaded screenshot"
            st.image(str(saved_image), caption=image_caption, width="stretch")
        if st.button("Remove saved image", key=f"remove_image_{item_kind}_{record_id}_{item_id}"):
            remove_exhibit_image(DATA_ROOT, record_id, image_relative)
            image_relative = None
            crop_metadata = None
            changed = True

    st.markdown("**Use your own screenshot**")
    uploaded = st.file_uploader(
        "Upload a screenshot instead of cropping",
        type=["png", "jpg", "jpeg", "webp"],
        key=f"{key_prefix}_upload",
        help="Choose an image already saved on your computer. It will replace the current image for this entry.",
    )
    if uploaded is not None:
        st.image(uploaded, caption="Screenshot preview", width="stretch")
        if st.button("Use this screenshot", type="primary", key=f"{key_prefix}_use_upload"):
            try:
                image_relative = save_uploaded_image(
                    DATA_ROOT,
                    record_id,
                    item_kind,
                    item_id,
                    uploaded.getvalue(),
                )
            except (OSError, ValueError) as error:
                st.error(str(error))
            else:
                crop_metadata = None
                changed = True
                st.success("Screenshot saved.")

    crop_enabled = st.checkbox(
        "✂️ Or open the manual cropping tool",
        key=f"{key_prefix}_enabled",
        help="Drag and resize the box directly over the part of the PDF page you want to save.",
    )
    if crop_enabled:
        previous = crop_metadata if isinstance(crop_metadata, dict) else {}
        same_page = int(previous.get("page_number", -1)) == int(page_number)
        preview = cached_render(str(pdf_path), pdf_modified, min(max(page_number, 1), total_pages), 1.0)
        default_coords = None
        if same_page:
            default_coords = (
                round(preview.width * float(previous.get("left", 0)) / 100),
                round(preview.width * float(previous.get("right", 100)) / 100),
                round(preview.height * float(previous.get("top", 0)) / 100),
                round(preview.height * float(previous.get("bottom", 100)) / 100),
            )
        st.caption("Drag the corners or edges of the box to select the area you want.")
        crop_box = st_cropper(
            preview,
            realtime_update=True,
            default_coords=default_coords,
            box_color="#8A4B2A",
            aspect_ratio=None,
            return_type="box",
            key=f"{key_prefix}_drag_box",
            stroke_width=3,
        )
        left = max(0, int(crop_box.get("left", 0)))
        top = max(0, int(crop_box.get("top", 0)))
        right = min(preview.width, left + max(0, int(crop_box.get("width", 0))))
        bottom = min(preview.height, top + max(0, int(crop_box.get("height", 0))))
        if right > left and bottom > top:
            st.image(preview.crop((left, top, right, bottom)), caption="Selected crop preview", width="stretch")
            if st.button("Save this crop", type="primary", key=f"{key_prefix}_save"):
                crop_values = (
                    100 * left / preview.width,
                    100 * top / preview.height,
                    100 * right / preview.width,
                    100 * bottom / preview.height,
                )
                crop_page(pdf_path, page_number, crop_values, output_path)
                image_relative = str(output_path.relative_to(folder))
                crop_metadata = {
                    "page_number": int(page_number),
                    "left": crop_values[0],
                    "top": crop_values[1],
                    "right": crop_values[2],
                    "bottom": crop_values[3],
                }
                changed = True
                st.success("Crop saved.")
        else:
            st.warning("Choose a crop with some width and height.")
    return image_relative, crop_metadata, changed


def crop_selector(
    *,
    pdf_path: Path,
    pdf_modified: int,
    page_number: int,
    total_pages: int,
    crop_metadata: dict,
    key: str,
) -> dict[str, float]:
    preview = cached_render(str(pdf_path), pdf_modified, min(max(page_number, 1), total_pages), 1.0)
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
            key: float(crop_metadata[key])
            for key in ("left", "top", "right", "bottom")
        }
    st.image(preview.crop((left, top, right, bottom)), caption="Selected crop preview", width="stretch")
    return {
        "left": 100 * left / preview.width,
        "top": 100 * top / preview.height,
        "right": 100 * right / preview.width,
        "bottom": 100 * bottom / preview.height,
    }


def multi_image_editor(
    *,
    exhibit: dict,
    record_id: str,
    total_pages: int,
    pdf_path: Path,
    pdf_modified: int,
    folder: Path,
) -> tuple[list[dict], bool]:
    edited_parts: list[dict] = []
    changed = False
    for index, part in enumerate(exhibit.get("images", [])):
        panel_label = str(part.get("label") or f"image_{index + 1}")
        friendly_label = panel_label.replace("_", " ").replace("panel ", "Panel ", 1)
        st.markdown(f"**{friendly_label}**")
        saved_image = folder / str(part.get("image", ""))
        if saved_image.is_file():
            st.image(str(saved_image), caption=f"Saved {friendly_label.lower()}", width="stretch")
        page_number = st.number_input(
            f"PDF page for {friendly_label}",
            min_value=1,
            max_value=total_pages,
            value=min(max(int(part.get("page_number", 1)), 1), total_pages),
            key=f"part_page_{record_id}_{exhibit['id']}_{index}",
        )
        remove_col, show_col = st.columns(2)
        remove_clicked = remove_col.button(
            f"Remove {friendly_label}", key=f"remove_part_{record_id}_{exhibit['id']}_{index}"
        )
        show_col.button(
            "Show page in reader",
            key=f"show_part_{record_id}_{exhibit['id']}_{index}",
            on_click=set_reader_page,
            args=(f"reader_page_{record_id}", int(page_number)),
        )
        if remove_clicked:
            remove_exhibit_image(DATA_ROOT, record_id, part.get("image"))
            changed = True
            continue

        updated_part = {**part, "page_number": int(page_number)}
        crop_metadata = part.get("crop") if isinstance(part.get("crop"), dict) else {}
        adjust = st.checkbox(
            f"Adjust crop for {friendly_label}",
            key=f"adjust_part_{record_id}_{exhibit['id']}_{index}",
        )
        if adjust:
            crop_values = crop_selector(
                pdf_path=pdf_path,
                pdf_modified=pdf_modified,
                page_number=int(page_number),
                total_pages=total_pages,
                crop_metadata=crop_metadata,
                key=f"crop_part_{record_id}_{exhibit['id']}_{index}",
            )
            if st.button(
                f"Save {friendly_label} crop",
                type="primary",
                key=f"save_part_{record_id}_{exhibit['id']}_{index}",
            ):
                crop_page(
                    pdf_path,
                    int(page_number),
                    (crop_values["left"], crop_values["top"], crop_values["right"], crop_values["bottom"]),
                    saved_image,
                )
                updated_part["crop"] = {"page_number": int(page_number), **crop_values}
                changed = True
        if updated_part != part:
            changed = True
        edited_parts.append(updated_part)
        st.divider()
    return edited_parts, changed


def show_new_record() -> None:
    if list_records(DATA_ROOT) and st.button("← Back to main menu"):
        st.session_state["creating_record"] = False
        st.session_state["main_menu"] = True
        st.rerun()
    st.title("Create a reading record")
    st.caption("Your paper and notes stay in this project folder. Nothing is sent to an AI service.")
    show_paper_deleted_message()
    with st.form("create_record", clear_on_submit=False):
        title = st.text_input("Paper title", placeholder="Enter the title you want shown in your notes")
        uploaded = st.file_uploader("Paper PDF", type=["pdf"])
        submitted = st.form_submit_button("Create record", type="primary", width="stretch")
    if submitted:
        if not title.strip() or uploaded is None:
            st.error("Add both a paper title and a PDF.")
            return
        try:
            notes = create_record(DATA_ROOT, title, uploaded.getvalue(), uploaded.name)
        except (OSError, ValueError) as error:
            st.error(str(error))
            return
        st.session_state["active_record"] = notes["record_id"]
        st.session_state["creating_record"] = False
        st.session_state["main_menu"] = False
        st.rerun()


def show_main_menu(records: list[dict]) -> None:
    heading_col, new_col = st.columns([4, 1])
    with heading_col:
        st.title("Your papers")
        st.caption("Open a paper to continue its notes, or start a new reading record.")
    with new_col:
        st.markdown("<div style='height: 1.35rem;'></div>", unsafe_allow_html=True)
        if st.button("＋ New paper", type="primary", width="stretch"):
            st.session_state["creating_record"] = True
            st.session_state["main_menu"] = False
            st.rerun()

    show_paper_deleted_message()

    title_message = st.session_state.pop("title_edit_message", None)
    if title_message:
        message_kind, message_text = title_message
        (st.success if message_kind == "success" else st.error)(message_text)

    loaded_records = [load_notes(DATA_ROOT, record["record_id"]) for record in records]
    known_categories = sorted(
        {
            str(category).strip()
            for record in loaded_records
            for category in record.get("categories", [])
            if str(category).strip()
        },
        key=str.casefold,
    )
    category_filter = st.selectbox(
        "Show category",
        ["All papers", *known_categories],
        help="Choose a category to show only papers with that label.",
    )

    visible_records = [
        record
        for record in loaded_records
        if category_filter == "All papers"
        or category_filter.casefold()
        in {str(category).strip().casefold() for category in record.get("categories", [])}
    ]
    if not visible_records:
        st.info("No papers currently have this category.")

    for current in visible_records:
        status = section_completion(current)
        started = sum(status.values())
        with st.container(border=True):
            select_col, details_col, open_col = st.columns([0.7, 4.3, 1])
            with select_col:
                st.checkbox(
                    "Select",
                    key=f"menu_select_{current['record_id']}",
                    help="Select this paper for category changes or a combined summary export.",
                )
            with details_col:
                st.subheader(current["title"])
                st.caption(
                    f"{started} of {len(SECTION_LABELS)} sections started · "
                    f"Last saved {current['updated_at'].replace('T', ' ')[:19]} UTC"
                )
                title_key = f"menu_title_{current['record_id']}"
                st.text_input(
                    "Paper title",
                    value=current["title"],
                    key=title_key,
                    on_change=save_record_title,
                    args=(current["record_id"], title_key),
                )
                category_key = f"menu_categories_{current['record_id']}"
                st.text_input(
                    "Categories",
                    value=", ".join(current.get("categories", [])),
                    key=category_key,
                    placeholder="e.g., Week 6, Lobbying",
                    help="Separate multiple categories with commas.",
                    on_change=save_record_categories,
                    args=(current["record_id"], category_key),
                )
            with open_col:
                if st.button("Open paper", key=f"menu_open_{current['record_id']}", width="stretch"):
                    st.session_state["active_record"] = current["record_id"]
                    st.session_state["creating_record"] = False
                    st.session_state["main_menu"] = False
                    st.rerun()
                st.link_button(
                    "Extract tables and figures ↗",
                    f"{EXTRACTOR_URL}/?record={current['record_id']}",
                    width="stretch",
                    help="Opens this paper's existing PDF in the standalone local extractor.",
                )
                if st.button(
                    "🗑 Delete paper",
                    key=f"menu_delete_{current['record_id']}",
                    width="stretch",
                ):
                    st.session_state["delete_candidate"] = current["record_id"]
                    st.rerun()

            if st.session_state.get("delete_candidate") == current["record_id"]:
                st.warning(
                    f'Permanently delete “{current["title"]}”? This removes its source PDF, '
                    "all notes, screenshots/crops, and individual formatted exports. "
                    "This cannot be undone. Existing combined export files will not be changed."
                )
                confirmation_key = f"confirm_delete_{current['record_id']}"
                confirmed = st.checkbox(
                    "I understand this cannot be undone.",
                    key=confirmation_key,
                )
                delete_col, cancel_col = st.columns(2)
                with delete_col:
                    permanently_delete = st.button(
                        "Permanently delete this paper",
                        type="primary",
                        disabled=not confirmed,
                        key=f"permanently_delete_{current['record_id']}",
                        width="stretch",
                    )
                with cancel_col:
                    cancel_delete = st.button(
                        "Cancel",
                        key=f"cancel_delete_{current['record_id']}",
                        width="stretch",
                    )
                if cancel_delete:
                    st.session_state.pop("delete_candidate", None)
                    st.session_state.pop(confirmation_key, None)
                    st.rerun()
                if permanently_delete:
                    try:
                        deleted = delete_record(DATA_ROOT, current["record_id"])
                    except (OSError, ValueError) as error:
                        st.error(f"The paper could not be deleted: {error}")
                    else:
                        clear_deleted_record_state(current["record_id"])
                        st.session_state["paper_deleted_message"] = (
                            f'Deleted “{deleted["title"]}” and its entire saved folder. '
                            "It cannot be recovered from the app."
                        )
                        st.rerun()

    all_record_ids = [record["record_id"] for record in loaded_records]
    selected_ids = [
        record_id for record_id in all_record_ids if st.session_state.get(f"menu_select_{record_id}", False)
    ]
    st.caption(f"{len(selected_ids)} paper{'s' if len(selected_ids) != 1 else ''} selected")

    category_col, add_col, clear_col = st.columns([3, 1, 1])
    with category_col:
        batch_category = st.text_input(
            "Add one category to all selected papers",
            key="menu_batch_category",
            placeholder="e.g., Week 6",
        )
    with add_col:
        st.write("")
        st.write("")
        st.button(
            "Add category",
            width="stretch",
            disabled=not selected_ids or not batch_category.strip(),
            on_click=add_category_to_records,
            args=(selected_ids, batch_category),
        )
    with clear_col:
        st.write("")
        st.write("")
        st.button(
            "Clear checks",
            width="stretch",
            on_click=clear_paper_selection,
            args=(all_record_ids,),
        )

    category_message = st.session_state.pop("category_saved_message", None)
    if category_message:
        st.success(category_message)

    st.divider()
    st.subheader("Combined paper export")
    st.caption("Creates one compact Word review document from the selected papers and sections.")
    with st.expander("Sections to include", expanded=True):
        section_columns = st.columns(2)
        selected_export_sections = []
        for index, (section_key, section_label) in enumerate(GROUP_EXPORT_SECTIONS):
            with section_columns[index % 2]:
                if st.checkbox(
                    section_label,
                    value=True,
                    key=f"group_export_section_{section_key}",
                ):
                    selected_export_sections.append(section_key)
    export_name_col, export_button_col = st.columns([4, 1])
    with export_name_col:
        combined_title = st.text_input(
            "Document title and filename",
            value="Combined paper notes",
            key="combined_export_title",
        )
    with export_button_col:
        st.write("")
        st.write("")
        combined_export_clicked = st.button("Create Word file", type="primary", width="stretch")

    if combined_export_clicked:
        if not selected_ids:
            st.warning("Select at least one paper first.")
        elif not selected_export_sections:
            st.warning("Choose at least one section to export.")
        else:
            selected_notes = [load_notes(DATA_ROOT, record_id) for record_id in selected_ids]
            missing_content = [
                item["title"]
                for item in selected_notes
                if not paper_has_selected_content(item, selected_export_sections)
            ]
            try:
                combined_path = export_combined_papers(
                    DATA_ROOT,
                    selected_notes,
                    combined_title,
                    selected_export_sections,
                )
            except ValueError as error:
                st.error(str(error))
            else:
                st.session_state["combined_export_path"] = str(combined_path)
                if missing_content:
                    st.warning(
                        "Skipped papers without content in the chosen sections: "
                        + ", ".join(missing_content)
                    )

    combined_export = st.session_state.get("combined_export_path")
    if combined_export and Path(combined_export).is_file():
        st.download_button(
            "Download combined Word file",
            data=Path(combined_export).read_bytes(),
            file_name=Path(combined_export).name,
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            width="stretch",
        )


records = list_records(DATA_ROOT)

linked_record = st.query_params.get("record")
if linked_record:
    try:
        linked_notes = load_notes(DATA_ROOT, str(linked_record))
    except (OSError, ValueError):
        st.error("That Paper Notes link is invalid or the paper no longer exists.")
        st.stop()
    st.session_state["active_record"] = linked_notes["record_id"]
    st.session_state.pop("main_menu", None)

with st.sidebar:
    st.title("Paper Notes")
    st.caption("Structured notes, written entirely by you")
    st.divider()
    st.caption("Autosaves when you leave a field or press Ctrl/⌘+Enter.")

if st.session_state.get("creating_record") or not records:
    show_new_record()
    st.stop()

if st.session_state.get("main_menu"):
    show_main_menu(records)
    st.stop()

record_id = st.session_state.get("active_record") or records[0]["record_id"]
notes = load_notes(DATA_ROOT, record_id)
edited = deepcopy(notes)
folder = record_dir(DATA_ROOT, record_id)
pdf_path = folder / "source.pdf"
pdf_modified = pdf_path.stat().st_mtime_ns
total_pages = cached_page_count(str(pdf_path), pdf_modified)
completion = section_completion(notes)
completed_count = sum(completion.values())

with st.sidebar:
    st.metric("Sections started", f"{completed_count} / {len(SECTION_LABELS)}")
    st.progress(completed_count / len(SECTION_LABELS))
    st.caption(f"Last saved: {notes['updated_at'].replace('T', ' ')[:19]} UTC")

title_col, download_col, done_col = st.columns([4, 1, 1])
with title_col:
    st.title(notes["title"])
    st.caption(f"Source: {notes['original_pdf_name']} · {total_pages} pages")
with download_col:
    st.markdown("<div style='height: 1.35rem;'></div>", unsafe_allow_html=True)
    st.download_button(
        "Download PDF",
        data=pdf_path.read_bytes(),
        file_name=notes["original_pdf_name"],
        mime="application/pdf",
        width="stretch",
    )
with done_col:
    st.markdown("<div style='height: 1.35rem;'></div>", unsafe_allow_html=True)
    done_requested = st.button(
        "✓ Done",
        type="primary",
        width="stretch",
        help="Save your latest changes and return to the main menu.",
    )

reader_col, notes_col = st.columns([0.95, 1.25], gap="large")
page_key = f"reader_page_{record_id}"
if page_key not in st.session_state:
    st.session_state[page_key] = 1
rerun_requested = False
saved_in_run = False

with reader_col:
    st.subheader("Paper")
    previous_col, page_col, next_col = st.columns([1, 2, 1])
    with previous_col:
        st.button(
            "← Previous",
            key=f"prev_{record_id}",
            help="Previous PDF page",
            width="stretch",
            on_click=move_reader_page,
            args=(page_key, -1, total_pages),
        )
    with page_col:
        selected_page = st.number_input(
            "Page",
            min_value=1,
            max_value=total_pages,
            step=1,
            key=page_key,
            label_visibility="collapsed",
        )
    with next_col:
        st.button(
            "Next →",
            key=f"next_{record_id}",
            help="Next PDF page",
            width="stretch",
            on_click=move_reader_page,
            args=(page_key, 1, total_pages),
        )
    with st.spinner("Rendering page…"):
        page_image = cached_render(str(pdf_path), pdf_modified, int(selected_page))
    st.image(page_image, width="stretch", caption=f"Page {selected_page} of {total_pages}")

with notes_col:
    st.subheader("Your notes")
    st.caption("Use plain text, bullets, or multiple paragraphs. The app does not complete or rewrite your content.")

    section_heading("1 Minute Summary", completion["one_minute_summary"])
    edited["sections"]["one_minute_summary"] = multiline(
        "1 Minute Summary",
        notes["sections"]["one_minute_summary"],
        f"summary_{record_id}",
        help_text="Write your own concise account of the paper.",
    )

    section_heading("Contribution to the Literature and Broader Context", completion["contribution"])
    edited["sections"]["contribution"] = multiline(
        "Contribution to the Literature and Broader Context",
        notes["sections"]["contribution"],
        f"contribution_{record_id}",
    )

    section_heading("Background Details / Context", completion["background"])
    edited["sections"]["background"] = multiline(
        "Background Details / Context",
        notes["sections"]["background"],
        f"background_{record_id}",
    )

    section_heading("Data Sources", completion["data_sources"])
    edited_sources = []
    remove_source_id = None
    for index, source in enumerate(notes.get("data_sources", []), start=1):
        source_label = source.get("name", "").strip() or f"Source {index}"
        with st.expander(source_label, expanded=index == 1):
            header_col, remove_col = st.columns([5, 1])
            header_col.markdown(f"**Source {index}**")
            if remove_col.button("Remove", key=f"remove_source_{record_id}_{source['id']}"):
                remove_source_id = source["id"]
            edited_sources.append(
                {
                    "id": source["id"],
                    "name": st.text_input(
                        "Source name",
                        value=source.get("name", ""),
                        key=f"source_name_{record_id}_{source['id']}",
                    ),
                    "description": multiline(
                        "What is used from this source",
                        source.get("description", ""),
                        f"source_description_{record_id}_{source['id']}",
                        height=120,
                    ),
                }
            )
    if remove_source_id:
        edited_sources = [item for item in edited_sources if item["id"] != remove_source_id]
        rerun_requested = True
    if st.button("＋ Add data source", key=f"add_source_{record_id}"):
        edited_sources.append(blank_data_source())
        rerun_requested = True
    edited["data_sources"] = edited_sources

    section_heading("Empirical Strategy and Identification Concerns", completion["empirical_strategy"])
    edited["sections"]["empirical_strategy"] = multiline(
        "Overall strategy / identification notes (optional)",
        notes["sections"]["empirical_strategy"],
        f"empirical_{record_id}",
        height=150,
    )
    edited_strategies = []
    remove_strategy_id = None
    for index, strategy in enumerate(notes.get("empirical_strategies", []), start=1):
        strategy_label = strategy.get("title", "").strip() or f"Empirical Strategy {index}"
        with st.expander(strategy_label, expanded=index == 1):
            top_col, remove_col = st.columns([5, 1])
            strategy_title = top_col.text_input(
                "Strategy name / title",
                value=strategy.get("title", ""),
                key=f"strategy_title_{record_id}_{strategy['id']}",
                placeholder="e.g., Difference-in-differences specification",
            )
            if remove_col.button("Remove", key=f"remove_strategy_{record_id}_{strategy['id']}"):
                remove_strategy_id = strategy["id"]
            strategy_page = st.number_input(
                "PDF page number",
                min_value=1,
                max_value=total_pages,
                value=min(max(int(strategy.get("page_number", 1)), 1), total_pages),
                key=f"strategy_page_{record_id}_{strategy['id']}",
            )
            st.button(
                "Show this page in reader",
                key=f"show_strategy_page_{record_id}_{strategy['id']}",
                on_click=set_reader_page,
                args=(page_key, int(strategy_page)),
            )
            strategy_description = multiline(
                "Brief description",
                strategy.get("description", ""),
                f"strategy_description_{record_id}_{strategy['id']}",
                height=140,
            )
            latex_equation = multiline(
                "LaTeX equation (optional)",
                strategy.get("latex_equation", ""),
                f"strategy_latex_{record_id}_{strategy['id']}",
                height=110,
                help_text="Enter LaTeX without the surrounding $$ symbols.",
            )
            if latex_equation.strip():
                st.caption("Equation preview")
                st.latex(latex_equation)

            strategy_image, strategy_crop, strategy_image_changed = image_editor(
                item=strategy,
                item_id=strategy["id"],
                item_kind="strategy",
                record_id=record_id,
                page_number=int(strategy_page),
                total_pages=total_pages,
                pdf_path=pdf_path,
                pdf_modified=pdf_modified,
                folder=folder,
                output_path=strategy_image_path(DATA_ROOT, record_id, strategy["id"]),
            )
            if strategy_image_changed:
                rerun_requested = True
            edited_strategies.append(
                {
                    **strategy,
                    "title": strategy_title,
                    "page_number": int(strategy_page),
                    "description": strategy_description,
                    "latex_equation": latex_equation,
                    "image": strategy_image,
                    "crop": strategy_crop,
                }
            )

    if remove_strategy_id:
        removed = next(item for item in edited_strategies if item["id"] == remove_strategy_id)
        remove_exhibit_image(DATA_ROOT, record_id, removed.get("image"))
        edited_strategies = [item for item in edited_strategies if item["id"] != remove_strategy_id]
        rerun_requested = True
    if st.button("＋ Add empirical strategy", key=f"add_strategy_{record_id}"):
        edited_strategies.append(blank_empirical_strategy())
        rerun_requested = True
    edited["empirical_strategies"] = edited_strategies

    section_heading("Tables and Figures", completion["exhibits"])
    edited_exhibits = []
    remove_exhibit_id = None
    for index, exhibit in enumerate(notes.get("exhibits", []), start=1):
        expander_title = exhibit.get("identifier_title", "").strip() or f"Exhibit {index}"
        with st.expander(expander_title, expanded=index == 1):
            top_col, remove_col = st.columns([5, 1])
            identifier_title = top_col.text_input(
                "Exhibit identifier / title",
                value=exhibit.get("identifier_title", ""),
                key=f"exhibit_title_{record_id}_{exhibit['id']}",
                placeholder="e.g., Table 2 — Main estimates",
            )
            if remove_col.button("Remove", key=f"remove_exhibit_{record_id}_{exhibit['id']}"):
                remove_exhibit_id = exhibit["id"]
            page_number = st.number_input(
                "PDF page number",
                min_value=1,
                max_value=total_pages,
                value=min(max(int(exhibit.get("page_number", 1)), 1), total_pages),
                key=f"exhibit_page_{record_id}_{exhibit['id']}",
            )
            st.button(
                "Show this page in reader",
                key=f"show_page_{record_id}_{exhibit['id']}",
                on_click=set_reader_page,
                args=(page_key, int(page_number)),
            )
            updated_exhibit = {
                **exhibit,
                "identifier_title": identifier_title,
                "page_number": int(page_number),
                "convey": multiline(
                    "What it is trying to convey",
                    exhibit.get("convey", ""),
                    f"exhibit_convey_{record_id}_{exhibit['id']}",
                    height=110,
                ),
                "empirical_strategy": multiline(
                    "Empirical strategy used",
                    exhibit.get("empirical_strategy", ""),
                    f"exhibit_strategy_{record_id}_{exhibit['id']}",
                    height=110,
                ),
                "interpretation": multiline(
                    "Interpretation / notes",
                    exhibit.get("interpretation", ""),
                    f"exhibit_interpretation_{record_id}_{exhibit['id']}",
                    height=130,
                ),
                "qualifications": multiline(
                    "Qualifications / concerns",
                    exhibit.get("qualifications", ""),
                    f"exhibit_qualifications_{record_id}_{exhibit['id']}",
                    height=110,
                ),
            }

            if exhibit.get("images"):
                exhibit_parts, parts_changed = multi_image_editor(
                    exhibit=exhibit,
                    record_id=record_id,
                    total_pages=total_pages,
                    pdf_path=pdf_path,
                    pdf_modified=pdf_modified,
                    folder=folder,
                )
                updated_exhibit["images"] = exhibit_parts
                if parts_changed:
                    rerun_requested = True
            else:
                exhibit_image, exhibit_crop, exhibit_image_changed = image_editor(
                    item=exhibit,
                    item_id=exhibit["id"],
                    item_kind="exhibit",
                    record_id=record_id,
                    page_number=int(page_number),
                    total_pages=total_pages,
                    pdf_path=pdf_path,
                    pdf_modified=pdf_modified,
                    folder=folder,
                    output_path=exhibit_image_path(DATA_ROOT, record_id, exhibit["id"]),
                )
                updated_exhibit["image"] = exhibit_image
                updated_exhibit["crop"] = exhibit_crop
                if exhibit_image_changed:
                    rerun_requested = True
            edited_exhibits.append(updated_exhibit)

    if remove_exhibit_id:
        removed = next(item for item in edited_exhibits if item["id"] == remove_exhibit_id)
        remove_exhibit_images(DATA_ROOT, record_id, removed)
        edited_exhibits = [item for item in edited_exhibits if item["id"] != remove_exhibit_id]
        rerun_requested = True

    st.markdown("**Add several saved screenshots**")
    st.caption("Select multiple PNG, JPG, or WEBP files. Each file becomes its own exhibit entry.")
    st.caption(
        f"New entries start on the currently displayed PDF page ({int(selected_page)}); "
        "you can edit each page number afterward."
    )
    batch_generation_key = f"batch_upload_generation_{record_id}"
    batch_generation = int(st.session_state.get(batch_generation_key, 0))
    batch_uploads = st.file_uploader(
        "Choose screenshots",
        type=["png", "jpg", "jpeg", "webp"],
        accept_multiple_files=True,
        key=f"batch_exhibit_upload_{record_id}_{batch_generation}",
    )
    if st.button(
        "Add selected screenshots",
        key=f"batch_exhibit_add_{record_id}_{batch_generation}",
        disabled=not batch_uploads,
    ):
        new_exhibits = []
        created_images = []
        try:
            for uploaded in batch_uploads:
                exhibit = blank_exhibit()
                exhibit["identifier_title"] = Path(uploaded.name).stem
                exhibit["page_number"] = int(selected_page)
                relative_path = save_uploaded_image(
                    DATA_ROOT,
                    record_id,
                    "exhibit",
                    exhibit["id"],
                    uploaded.getvalue(),
                )
                exhibit["image"] = relative_path
                created_images.append(relative_path)
                new_exhibits.append(exhibit)
        except (OSError, ValueError) as error:
            for relative_path in created_images:
                remove_exhibit_image(DATA_ROOT, record_id, relative_path)
            st.error(f"No screenshots were added: {error}")
        else:
            edited_exhibits.extend(new_exhibits)
            st.session_state[batch_generation_key] = batch_generation + 1
            rerun_requested = True
    if st.button("＋ Add table or figure", key=f"add_exhibit_{record_id}"):
        edited_exhibits.append(blank_exhibit())
        rerun_requested = True
    edited["exhibits"] = edited_exhibits

    section_heading("Concerns and Extensions", completion["concerns_extensions"])
    edited["sections"]["concerns_extensions"] = multiline(
        "Concerns and Extensions",
        notes["sections"]["concerns_extensions"],
        f"concerns_{record_id}",
        height=220,
    )

    section_heading("Additional Notes", completion["additional_notes"])
    edited["sections"]["additional_notes"] = multiline(
        "Additional Notes (optional)",
        notes["sections"]["additional_notes"],
        f"additional_notes_{record_id}",
        height=180,
    )

    if not content_equal(notes, edited):
        edited = save_notes(DATA_ROOT, edited)
        saved_in_run = True
        rerun_requested = True
        st.caption("✓ Changes saved locally")

    st.divider()
    st.subheader("Export your notes")
    markdown_col, word_col = st.columns(2)
    if markdown_col.button("Prepare Markdown", width="stretch"):
        path = export_markdown(DATA_ROOT, edited)
        st.session_state[f"markdown_export_{record_id}"] = str(path)
    if word_col.button("Prepare Word document", width="stretch"):
        path = export_docx(DATA_ROOT, edited)
        st.session_state[f"docx_export_{record_id}"] = str(path)

    markdown_export = st.session_state.get(f"markdown_export_{record_id}")
    docx_export = st.session_state.get(f"docx_export_{record_id}")
    if markdown_export and Path(markdown_export).is_file():
        st.download_button(
            "Download notes.md",
            data=Path(markdown_export).read_bytes(),
            file_name=Path(markdown_export).name,
            mime="text/markdown",
            width="stretch",
        )
    if docx_export and Path(docx_export).is_file():
        st.download_button(
            "Download notes.docx",
            data=Path(docx_export).read_bytes(),
            file_name=Path(docx_export).name,
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            width="stretch",
        )

if done_requested:
    if not saved_in_run and not content_equal(notes, edited):
        save_notes(DATA_ROOT, edited)
    st.session_state["creating_record"] = False
    st.session_state["main_menu"] = True
    st.rerun()

if rerun_requested:
    if not saved_in_run and not content_equal(notes, edited):
        save_notes(DATA_ROOT, edited)
    st.rerun()
