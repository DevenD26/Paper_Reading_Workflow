---
name: extract-exhibits
description: Run the Paper Notes project's deterministic local table-and-figure proposal command for a PDF. Use for offline exhibit crop proposals, not academic interpretation or summarization.
---

# Extract exhibits locally

Locate the workflow root (the folder containing `tools.toml`), change to that
folder, and invoke the shared extractor with its local environment:

```bash
.venv/bin/python -m paper_notes.extraction_cli /path/to/paper.pdf --output /path/to/proposals
```

Add `--include-appendix` only when requested. Add `--appendix-start PAGE` when the user supplies the PDF page where the appendix begins.

The command is deterministic and performs no network or model calls. Do not infer, summarize, or interpret paper content. Report the proposal folder and warnings produced by the command. The Paper Notes app and CLI do not consume tokens; using Codex to invoke this skill may consume Codex tokens.
