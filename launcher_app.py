import os
from pathlib import Path
from urllib.parse import quote

import streamlit as st

from paper_notes.tool_registry import RegistryError, load_registry
from paper_notes.storage import create_record


APP_ROOT = Path(__file__).resolve().parent
PAPER_NOTES_STORAGE = Path(
    os.environ.get("WORKFLOW_TOOL_PAPER_NOTES_STORAGE", APP_ROOT / "data")
)
DATA_ROOT = Path(os.environ.get("PAPER_NOTES_DATA_ROOT", PAPER_NOTES_STORAGE / "papers"))

st.set_page_config(page_title="Workflow Hub", page_icon="🧰", layout="wide")
st.title("Workflow Hub")
st.caption("Private, local tools for reading papers and preparing your own research notes.")

try:
    registry = load_registry(APP_ROOT / "tools.toml")
except RegistryError as error:
    st.error(f"The local tool registry could not be loaded: {error}")
    st.stop()

tools_by_id = {tool.id: tool for tool in registry.tools}
organizer = tools_by_id.get("paper-notes")
extractor = tools_by_id.get("table-figure-extractor")

st.subheader("Add paper")
st.caption("Create one unified Paper Notes record from a local PDF.")
with st.form("hub_add_paper", clear_on_submit=False):
    title = st.text_input("Paper title")
    uploaded = st.file_uploader("Upload one PDF", type=["pdf"])
    submitted = st.form_submit_button("Add paper", type="primary")

if submitted:
    if not title.strip():
        st.warning("Enter a paper title.")
    elif uploaded is None:
        st.warning("Choose one PDF to upload.")
    else:
        try:
            created = create_record(DATA_ROOT, title, uploaded.getvalue(), uploaded.name)
        except (OSError, ValueError) as error:
            st.error(f"The paper could not be added: {error}")
        else:
            st.session_state["hub_created_record"] = created

created = st.session_state.get("hub_created_record")
if created:
    st.success(f'Created “{created["title"]}”. The PDF is stored once in its unified record.')
    record_query = quote(str(created["record_id"]), safe="")
    action_columns = st.columns(2)
    if organizer is not None:
        action_columns[0].link_button(
            "Open in Paper Notes ↗",
            f"{organizer.url.rstrip('/')}?record={record_query}",
            width="stretch",
        )
    if extractor is not None:
        action_columns[1].link_button(
            "Open in Table and Figure Extractor ↗",
            f"{extractor.url.rstrip('/')}?record={record_query}",
            width="stretch",
        )

st.divider()
st.subheader("Tools")

columns = st.columns(2)
for index, tool in enumerate(registry.tools):
    with columns[index % len(columns)]:
        with st.container(border=True):
            st.subheader(f"{tool.icon} {tool.name}")
            st.write(tool.description)
            st.link_button("Open in a new tab ↗", tool.url, width="stretch")

st.caption("All tools run on this computer. No paper content is sent to an external service.")
