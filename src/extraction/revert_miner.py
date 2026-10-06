from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.extraction.diff_parser import parse_removed_wikitext

ROLLBACK_KEYWORDS = (
    "reverted",
    "rollback",
    "presumptive removal",
    "wp:llm",
    "rvv",
)
ROLLBACK_TAGS = (
    "mw-reverted",
    "mw-manual-revert",
    "visualeditor",
)


def detect_rollback_revision(comment: str | None, tags: list[str] | None = None) -> bool:
    """Return True when a revision comment or tag indicates a revert/rollback event."""
    tag_list = tags or []
    normalized_tags = {str(tag).lower() for tag in tag_list}
    if any(tag in normalized_tags for tag in ROLLBACK_TAGS):
        return True
    if not comment:
        return False
    normalized = comment.lower()
    return any(keyword in normalized for keyword in ROLLBACK_KEYWORDS)


def extract_reverted_claim_rows(deleted_text: str) -> list[dict[str, Any]]:
    """Parse deleted wikitext and return claim rows with citation metadata."""
    rows: list[dict[str, Any]] = []
    parsed_rows = parse_removed_wikitext(deleted_text)
    for item in parsed_rows:
        citation_title = None
        if item.raw_citation:
            citation_title = item.raw_citation.split("|title=")[-1].split("|")[0].strip() if "|title=" in item.raw_citation else None
        rows.append(
            {
                "claim_text": item.claim_text,
                "doi": item.doi,
                "isbn": item.isbn,
                "citation_title": citation_title,
                "raw_citation": item.raw_citation,
                "url": None,
            }
        )
    return rows


def write_json_log(rows: list[dict[str, Any]], output_path: str | Path | None = None) -> Path:
    """Write the extracted claim rows to a JSON log file inside logs/ by default."""
    if output_path is None:
        output_path = Path(__file__).resolve().parents[2] / "logs" / "reverted_claims_minimal.json"
    else:
        output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(rows, handle, ensure_ascii=False, indent=2)
    return output_path
