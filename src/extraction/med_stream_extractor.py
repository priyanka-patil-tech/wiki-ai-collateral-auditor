from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb
import httpx
import mwparserfromhell

DB_PATH = "data/med_audit_warehouse.duckdb"
WIKI_API = "https://en.wikipedia.org/w/api.php"
MED_CATEGORY = "Category:All WikiProject Medicine articles"
HEADERS = {
    "User-Agent": "WikiMedAuditResearch/1.0 (mailto:your.email@university.edu)",
}


class MedicalAuditExtractor:
    """Medical-page filtering and claim extraction for Wikipedia revision streams."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        db_file = Path(self.db_path)
        db_file.parent.mkdir(parents=True, exist_ok=True)
        con = duckdb.connect(str(db_file))
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS fct_med_reverted_claims (
                claim_id VARCHAR PRIMARY KEY,
                user_id BIGINT,
                username VARCHAR,
                page_title VARCHAR,
                rev_id BIGINT,
                rev_timestamp TIMESTAMP,
                claim_text VARCHAR,
                pmid VARCHAR,
                doi VARCHAR,
                journal_title VARCHAR
            );
            """
        )
        con.close()

    async def filter_medical_pages(self, titles: list[str], client: httpx.AsyncClient) -> list[str]:
        """Map article titles to talk pages and keep only those in the WikiProject Medicine category."""
        talk_titles = [f"Talk:{t}" if not t.startswith("Talk:") else t for t in titles]
        medical_talk_pages: set[str] = set()
        for start in range(0, len(talk_titles), 50):
            params = {
                "action": "query",
                "format": "json",
                "titles": "|".join(talk_titles[start : start + 50]),
                "prop": "categories",
                "clcategories": MED_CATEGORY,
            }

            response = await client.get(WIKI_API, params=params, headers=HEADERS, timeout=30.0)
            response.raise_for_status()
            data = response.json().get("query", {}).get("pages", {})
            for page_info in data.values():
                if "categories" in page_info:
                    medical_talk_pages.add(page_info["title"])

        return [
            title
            for title in titles
            if f"Talk:{title}" in medical_talk_pages or title in medical_talk_pages
        ]

    def parse_med_wikitext(self, raw_wikitext: str) -> list[dict[str, str | None]]:
        """Extract clinical claim text and medical identifiers from a raw revision wikitext."""
        parsed = mwparserfromhell.parse(raw_wikitext or "")
        extracted: list[dict[str, str | None]] = []

        for template in parsed.filter_templates():
            pmid = None
            doi = None
            journal = None

            if template.has("pmid"):
                pmid = str(template.get("pmid").value).strip()
            if template.has("doi"):
                doi = str(template.get("doi").value).strip()
            if template.has("journal"):
                journal = str(template.get("journal").value).strip()

            claim = parsed.strip_code().strip()
            if not claim or len(claim) < 10:
                claim = parsed.strip_code().strip()[:400]

            if pmid or doi or "doi.org" in str(template).lower():
                extracted.append(
                    {
                        "claim_text": claim,
                        "pmid": pmid,
                        "doi": doi,
                        "journal": journal,
                    }
                )

        return extracted

    def save_rows(self, rows: list[dict[str, Any]]) -> int:
        """Persist rows into the medical reverted-claims warehouse."""
        if not rows:
            return 0

        con = duckdb.connect(self.db_path)
        con.executemany(
            """
            INSERT INTO fct_med_reverted_claims (
                claim_id, user_id, username, page_title, rev_id, rev_timestamp,
                claim_text, pmid, doi, journal_title
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row.get("claim_id")
                    or f"{row.get('username', 'unknown')}::{row.get('page_title', 'unknown')}::{row.get('rev_id', 0)}::{row.get('claim_text', '')}",
                    row.get("user_id"),
                    row.get("username"),
                    row.get("page_title"),
                    row.get("rev_id"),
                    row.get("rev_timestamp"),
                    row.get("claim_text"),
                    row.get("pmid"),
                    row.get("doi"),
                    row.get("journal_title"),
                )
                for row in rows
            ],
        )
        con.close()
        return len(rows)

    def export_jsonl(self, rows: list[dict[str, Any]], output_path: str | Path) -> Path:
        """Write rows to a JSONL file for debugging or downstream staging."""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        return path
