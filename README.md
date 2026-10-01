# Paper Notes

Paper Notes is a private, local workspace for structured notes on economics papers fully created by Codex. It stores and formats only what you write. A separate Table and Figure Extractor prepares screenshots using deterministic PDF text and layout rules on your computer. Neither app calls an AI service, summarizes papers, infers answers, or completes academic content.

## Project location

The complete offline workflow lives in this `paper_reading_workflow/` folder. Its code, registry, tests, and launch scripts are relocatable. Each checkout creates its own machine-local `.venv`; Paper Notes records and standalone extractor runs remain under the Git-ignored storage roots described below.

Run the canonical `run.sh`, `run_extractor.sh`, and `run_workspace.sh` scripts directly from this folder. Keep unrelated source materials outside the workflow root.

## Start the full workspace

From this folder, run the canonical launcher:

```bash
./run_workspace.sh
```

This prepares the existing local Python environment, starts the Workflow Hub at `http://localhost:8500`, Paper Notes at `http://localhost:8501`, and the extractor at `http://localhost:8502`, then opens the hub once. Choose a tool card to open that standalone application in a new browser tab.

Stop the complete workspace with `Ctrl+C` in the terminal that started it. The workspace stops only the processes it created. If any required port is already occupied, it exits safely and identifies the port instead of stopping the unrelated process.

## Start Paper Notes

You need Python 3.9 or newer and an internet connection the first time, so the app can install its small set of dependencies.

From this folder, run the canonical launcher:

```bash
./run.sh
```

The first launch creates a private `.venv` environment and installs the required packages. Later launches use the same one-command shortcut. Your browser should open automatically. If it does not, open the local address printed in the terminal (usually `http://localhost:8501`). Stop the app with `Ctrl+C` in the terminal.

## Start the standalone extractor

From this folder, run the canonical launcher:

```bash
./run_extractor.sh
```

The extractor opens separately at `http://localhost:8502`. You may run it by itself or alongside Paper Notes.

### Extract from a Paper Notes record without copying its PDF

For the unified workflow, start the full workspace and create or upload a paper in Paper Notes. On that paper's main-menu card, choose **Extract tables and figures**. The extractor opens the paper's existing `source.pdf` directly. Approved automatic and manual crops are saved in the paper's `exhibits/` folder and immediately attached to its Tables and Figures notes. Reopen or refresh the paper in Paper Notes to see them.

Direct uploads made from the extractor's own main menu remain independent and continue to use `extracted_exhibits/`.

Extractor workflow:

1. The extractor opens on a main menu containing every previously uploaded paper. Use **Open review**, **Open approved folder**, or **Open work folder** to return to its review queue or files.
2. Choose **New paper**, upload a PDF, and set the paper title/output-folder name before analysis.
3. Choose **Main text only** or **Include appendix exhibits**. You can also supply the appendix starting page, then select **Analyze PDF**.
4. Review proposed screenshots one at a time inside the app. Rename, manually adjust, approve, or reject each one. Approved screenshots can also be viewed inside the app.
5. Approved screenshots are saved in the run's `approved` folder under `extracted_exhibits/`. You can also download all approved images as a ZIP file.
6. Split exhibits receive names such as `Figure 4_[panel_A].png` and `Figure 4_[panel_B].png`.
7. If an exhibit is not detected, open **Add a manual crop from any PDF page**, select its page and bounds, choose any needed rotation, and save it directly to the approved folder.
8. Expand **Rename or remove files** on the main menu to rename both the paper and its output folder, remove selected approved screenshots, or remove an entire paper. Removed items are moved into a local `.trash` folder instead of being erased immediately.

If the detection rules have been updated while a run is open, select **Re-analyze this PDF with the current detector** to rebuild its review queue without uploading the PDF again. Existing approved screenshots are preserved.

### Manual setup (optional)

Run these commands from the `paper_reading_workflow/` folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m streamlit run app.py
# In another terminal, for the standalone extractor:
python -m streamlit run extractor_app.py --server.port 8502
```

## How to use it

1. Choose **New reading record**, enter a title, and upload the paper PDF.
2. Read the PDF page by page in the left pane and write in the ordered sections on the right.
3. Add as many collapsible data sources, empirical strategies, and table/figure entries as needed.
4. For a strategy or exhibit image, either upload a screenshot already saved on your computer or use the manual cropping tool on a PDF page. Both options remain available for every entry.
5. To import extractor output, choose several screenshots at once under **Add several saved screenshots** and select **Add selected screenshots**. Each selected file becomes a separate exhibit whose initial title comes from its filename.
6. Empirical-strategy entries can include a description and an optional LaTeX equation with a live preview.
7. Prepare and download Markdown or Word exports at the bottom of the notes pane. Individual Word exports place review notes (including concerns and extensions) first in two columns, empirical strategies next in two columns, and tables and figures last on full-width pages.
8. When you finish working on a paper, choose **Done** to save and return to the main menu. From there, reopen any paper or create a new one.
9. On the main menu, add comma-separated categories to individual papers, or check several papers and add one category to all of them.
10. Edit a saved paper title directly from the main menu. The stable storage folder remains unchanged, while future exports use the new title.
11. To remove a paper, choose **Delete paper** on the main menu and complete the confirmation. This permanently deletes that paper's PDF, notes, screenshots/crops, and individual formatted exports. Existing combined export files are not changed.
12. Check multiple papers, choose the sections to include, and use **Combined paper export** to create a compact Word review document. Review and empirical-strategy content uses two columns; tables and figures use full-width pages. Papers without content in the chosen sections are skipped.

Text changes save locally when you leave a field or press `Ctrl+Enter` / `⌘+Enter`. Empty/started indicators are informational only; the app never fills a section.

## Where records are stored

Each paper receives its own safe, uniquely named folder:

```text
data/papers/<paper-name>/
├── source.pdf
├── notes.json
├── exhibits/
│   ├── exhibit-<unique-id>.png
│   └── strategy-<unique-id>.png
├── extraction_proposals/
│   └── extraction-<run>/
│       ├── manifest.json
│       └── <review previews>.png
└── formatted_notes/
    ├── <paper title>_notes.md
    └── <paper title>_notes.docx
```

Creating another paper with the same title adds a numeric suffix instead of overwriting the first record. The original filename is kept as metadata, while the durable local copy is always named `source.pdf` inside its record folder.

Back up the `data` folder to preserve all PDFs, notes, crops, individual exports, and combined exports. Paper records are excluded from Git by default because they may contain copyrighted or private material.

Combined paper documents are stored in `data/combined_exports/`. Repeated exports with the same title receive a numeric suffix rather than replacing an earlier file.

Standalone extractor runs are stored separately:

```text
extracted_exhibits/<paper-and-run>/
├── source.pdf
├── run.json
├── approved/
│   ├── Table 1.png
│   └── Figure 4_[panel_A].png
└── work/
    └── extraction-<run>/
        ├── manifest.json
        └── <review previews>.png
```

The visible paper title is stored in `run.json`. Renaming from the extractor main menu also renames the `<paper-and-run>` folder. Deleted papers are moved to `extracted_exhibits/.trash/`; deleted approved screenshots are moved to a hidden `.trash/approved/` folder inside that paper's run, so they can be recovered manually if needed.

All Word exports use Arial. The code also includes a tested, spreadsheet-ready tracker row mapping keyed by each paper's stable record ID; no Excel tracker or tracker button is exposed yet.

## Optional command-line extraction

The standalone extractor and command line use the same local extraction module. To prepare reviewable proposals directly from any PDF:

```bash
.venv/bin/python -m paper_notes.extraction_cli /path/to/paper.pdf --output /path/to/proposals
```

Use `--include-appendix` to retain appendix candidates, or `--appendix-start PAGE` to supply the appendix boundary. The command creates a new proposal folder and does not edit a Paper Notes record.

The optional project-local Codex skill only documents this command. Running the app or CLI directly does not consume tokens. Asking Codex to invoke the optional skill may consume Codex tokens.

## Tests

After the first launch/setup, run:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

The tests cover caption recognition, appendix filtering, split exhibits, collision-safe filenames, proposal acceptance and rejection, failure behavior, offline extraction, safe storage, exact wording round-trips, and Markdown/Word export creation.

## Add another local tool

Create a directly runnable Streamlit entrypoint, give it a unique port and storage folder, add its metadata to `tools.toml`, and add tests. The launcher reads cards from the registry, so its presentation code does not need to change. See [`docs/workflow-hub.md`](docs/workflow-hub.md) for the complete registry and lifecycle contract.

## Pilot notes and current limits

- Images can be batch-uploaded as PNG, JPG, or WEBP screenshots, or created with the manual visual cropper. Uploaded files are validated and saved as PNG files inside the paper record.
- The reader renders one page at a time rather than embedding a full browser PDF viewer. This keeps navigation consistent and supports crop extraction from the same source.
- Autosave happens on Streamlit's normal field commit event (leaving the field or pressing `Ctrl/⌘+Enter`), not on every keystroke.
- Caption detection works only when the PDF contains extractable text. Image-only/scanned pages are reported for manual review; there is no OCR dependency.
- Arabic and Roman-numbered labels are supported, including sideways table pages that need rotation.
- Crop bounds are layout-based proposals. Ambiguous layouts receive broad crops for review instead of guessed precise bounds.
- A repeated or explicitly continued caption on consecutive pages can become ordered panel images. Unlabeled continuation pages require manual review.
- Appendix boundaries are applied only from a user-supplied page or a heading-like appendix label. Uncertain appendix wording is surfaced in the review panel.
- There is no automatic citation lookup, summarization, interpretation, or content generation.
- Blank sections are omitted from exports rather than being filled with placeholder text.
- Markdown links to saved images are relative to the saved export. Keep the `formatted_notes` and `exhibits` folders together when moving a record. Word exports embed the images.
- The supplied `.sh` launchers and workspace process cleanup support the current macOS setup and other POSIX systems; native Windows launch and process management are not currently provided.
