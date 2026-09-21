"""
diff_parser.py — wikitextparser / mwparserfromhell extraction.

Implements Module 2 (Diff Extraction & Citation AST Parser) from the System
Design Document, §4.2.

Pipeline:
    1. For each cohort member, page `WikiClient.user_contribs` (rev_id,
       parent_id, timestamp, title) and locate the presumptive-revert
       revision pairs via `WikiClient.compare_revisions`.
    2. Feed the removed diff chunk into `mwparserfromhell.parse(...)`.
    3. Walk the parsed nodes for:
         - Tag nodes named "ref"
         - Template nodes named "cite journal" / "cite web" / "cite book" /
           "citation"
    4. Pull citation parameters: doi, isbn, url, title, journal, author.
    5. Isolate the associated encyclopedic claim — the plain-text sentence
       immediately preceding the citation — via `.strip_code()`.

Output:
    Rows conforming to `deleted_revisions` + `extracted_claims`
    (see System Design Document §5 / §2.1):
        deleted_revisions: revision_id, user_id, page_id, page_title,
            timestamp, revert_revision_id, revert_timestamp,
            latency_seconds, raw_diff_text
        extracted_claims: claim_id, revision_id, claim_text, doi, isbn,
            raw_citation
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, List

import mwparserfromhell

CITATION_TEMPLATE_NAMES = {"cite journal", "cite web", "cite book", "citation"}
CITATION_PARAMS = ("doi", "isbn", "url", "title", "journal", "author")


@dataclass
class DeletedRevision:
    revision_id: int
    user_id: str
    page_id: int
    page_title: str
    timestamp: str
    revert_revision_id: int
    revert_timestamp: str
    latency_seconds: int
    raw_diff_text: str


@dataclass
class ExtractedClaim:
    claim_id: str
    revision_id: int
    claim_text: str
    doi: str | None
    isbn: str | None
    raw_citation: str


@dataclass
class ExtractedCitation:
    raw_text: str
    doi: Optional[str] = None
    isbn: Optional[str] = None
    title: Optional[str] = None


@dataclass
class WikipediaClaim:
    encyclopedic_text: str
    citations: List[ExtractedCitation]


class DiffParser:
    """Parses wikitext diffs to extract claims and their associated citations."""

    @staticmethod
    def extract_param(template, param_name: str) -> Optional[str]:
        if template.has(param_name):
            return str(template.get(param_name).value).strip()
        return None

    def parse_removed_chunk(self, wikitext_chunk: str) -> WikipediaClaim:
        """Parses a deleted chunk of wikitext into text and structured citations."""
        parsed = mwparserfromhell.parse(wikitext_chunk)
        citations: list[ExtractedCitation] = []

        for node in parsed.filter_tags(matches=lambda t: t.tag in ["ref", "citation"]):
            templates = node.contents.filter_templates()
            if templates:
                for template in templates:
                    name = str(template.name).lower()
                    if "cite" in name or "citation" in name:
                        citations.append(
                            ExtractedCitation(
                                raw_text=str(template),
                                doi=self.extract_param(template, "doi"),
                                isbn=self.extract_param(template, "isbn"),
                                title=self.extract_param(template, "title"),
                            )
                        )
            else:
                citations.append(ExtractedCitation(raw_text=str(node.contents)))

        clean_text = parsed.strip_code().strip()
        return WikipediaClaim(encyclopedic_text=clean_text, citations=citations)


def parse_removed_wikitext(raw_diff_text: str) -> list[ExtractedClaim]:
    """Parse a deleted diff chunk into (claim, citation) pairs via mwparserfromhell."""
    parser = DiffParser()
    claim = parser.parse_removed_chunk(raw_diff_text)

    results: list[ExtractedClaim] = []
    for citation in claim.citations:
        results.append(
            ExtractedClaim(
                claim_id="",
                revision_id=-1,
                claim_text=claim.encyclopedic_text,
                doi=citation.doi,
                isbn=citation.isbn,
                raw_citation=citation.raw_text,
            )
        )
    return results


def extract_citation_params(template) -> dict[str, str | None]:
    """Pull doi/isbn/url/title/journal/author off a cite template node."""
    parser = DiffParser()
    params = {}
    for key in CITATION_PARAMS:
        params[key] = parser.extract_param(template, key) if template is not None else None
    return params
