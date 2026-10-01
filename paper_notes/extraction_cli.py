from __future__ import annotations

import argparse
from pathlib import Path

from .extraction import load_manifest, write_proposal


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Propose table and figure crops from a PDF using local deterministic rules."
    )
    parser.add_argument("pdf", type=Path, help="Path to the source PDF")
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Folder in which to create a new extraction proposal",
    )
    parser.add_argument(
        "--include-appendix",
        action="store_true",
        help="Include exhibits that match appendix rules",
    )
    parser.add_argument(
        "--appendix-start",
        type=int,
        metavar="PAGE",
        help="Treat this PDF page and later pages as appendix pages",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    manifest_path = write_proposal(
        args.pdf,
        args.output,
        include_appendix=args.include_appendix,
        appendix_start_page=args.appendix_start,
    )
    manifest = load_manifest(manifest_path)
    print(f"Proposal: {manifest_path}")
    print(f"Candidates: {len(manifest['candidates'])}")
    print(f"Appendix candidates excluded: {manifest['excluded_appendix_candidates']}")
    for warning in manifest.get("warnings", []):
        print(f"Warning: {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
