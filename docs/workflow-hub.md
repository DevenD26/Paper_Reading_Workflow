# Workflow Hub architecture

## Project layout

The workflow root is the `paper_reading_workflow/` folder. `tools.toml`, all three
application entrypoints, shared deterministic code, tests, the machine-local
environment, and both durable storage roots live beneath it. Registry paths and
runtime storage defaults are resolved relative to this workflow root, so the
folder can move as a unit without changing stored records or manifests. The
`.venv` and runtime storage contents are deliberately excluded from version
control and should be created separately in each checkout.

Deployments may provide thin compatibility wrappers outside the workflow root,
but they are not part of this project. Run the canonical `run.sh`,
`run_extractor.sh`, and `run_workspace.sh` scripts from this folder. Unrelated
PDFs, documents, presentation bundles, and working images stay outside the
workflow root.

## Purpose

The Workflow Hub is a small local directory of independent research tools. It starts the registered Streamlit applications together and links to each one in a separate browser tab. Its Add paper form delegates validation and unified-record creation to `paper_notes.storage`; the launcher contains no duplicated paper-processing or storage implementation.

The current ports are:

- Workflow Hub: `8500`
- Paper Notes Organizer: `8501`
- Table and Figure Extractor: `8502`

All servers bind to `127.0.0.1`. Streamlit usage collection is disabled in `.streamlit/config.toml` and again on the workspace command line.

## Registry contract

`tools.toml` is the source of launcher cards and workspace startup metadata. Its `[workspace]` table declares the launcher. Each `[[tools]]` entry requires:

- `id`: unique lowercase identifier using letters, numbers, and hyphens
- `name`: user-facing tool name
- `description`: concise purpose shown by the launcher
- `icon`: user-facing symbol
- `entrypoint`: project-relative standalone Python file
- `port`: unique local TCP port from 1 through 65535
- `url`: plain `http://localhost:<port>` or `http://127.0.0.1:<port>` URL
- `storage`: project-relative folder owned by the tool's standalone workflow

Registry declaration order controls card and startup order. The loader rejects malformed TOML, duplicate IDs or ports, non-local or mismatched URLs, missing entrypoints, path traversal, and overlapping storage ownership.

To add a normal tool:

1. Create a directly runnable Streamlit entrypoint.
2. Give it a unique local port and storage folder.
3. Add one `[[tools]]` entry to `tools.toml`.
4. Add registry, application, and offline-behavior tests.

Launcher presentation code does not need to change.

## Unified paper records

Papers created in Paper Notes use one durable workspace:

```text
data/papers/<paper-id>/
├── source.pdf
├── notes.json
├── exhibits/
├── extraction_proposals/
└── formatted_notes/
```

The Paper Notes main menu opens the extractor with a validated record ID. In this linked mode, the extractor reads `source.pdf` in place, stores its review queue under `extraction_proposals/`, saves approved images under `exhibits/`, and appends those images to the Tables and Figures entries in `notes.json`. It never makes a second PDF copy.

The hub can create the same record and then deep-link that exact validated record into either Paper Notes or the linked extractor. Uploads are limited to readable PDFs of at most 100 MB. Validation happens before record allocation, and failed creation removes the partial record. Duplicate titles receive collision-safe suffixed IDs.

The extractor remains independently runnable. PDFs uploaded directly to it continue to use its separately owned `extracted_exhibits/` library. Existing standalone runs are not migrated.

Every linked or standalone re-analysis creates a new, non-overwriting manifest from the already saved `source.pdf`. The user may include or exclude appendix exhibits and add, change, or remove an explicit appendix starting page; the page must exist in the PDF. Earlier queues, approved screenshots, and authored Paper Notes content remain untouched.

Only shared functions in `paper_notes.storage` and `paper_notes.extraction` should mutate linked paper records. Other tools must not write arbitrary files into another tool's storage.

## Process lifecycle

The canonical `run_workspace.sh` prepares the `.venv` in the workflow root, then
runs `paper_notes.workspace`. The supervisor:

1. Validates the complete registry.
2. Checks every required port before starting any process.
3. Starts each Streamlit server headlessly in its own process group.
4. Waits for each server to accept local connections.
5. Opens the launcher once after all applications are ready.
6. Monitors every child and reports unexpected exits.
7. On Ctrl+C or termination, stops only the process groups it created.

An occupied required port causes a safe preflight failure. The supervisor never kills a process merely because it owns a required port.

For automated smoke tests, set `WORKFLOW_HUB_NO_BROWSER=1` to suppress the one-time browser opening.

The supplied launch scripts and process-group cleanup target the project's current macOS setup and other POSIX systems. Native Windows process management is not currently supported.

## Offline contract

Runtime code may read and write local files and may listen or connect on the declared loopback ports for process readiness. It must not contact external hosts, APIs, model providers, analytics, or telemetry services. No API keys or AI dependencies belong in this project. Extraction is deterministic and must not interpret or summarize academic material.
