"""
academic_tools.py — OpenAlex, Crossref & Open Library API connectors.

Implements the tool registry for Subsystem 3B (Agentic Academic Grounding
Auditor), System Design Document §4.3 / §6.

Tools:
    tool_crossref_lookup(title, doi)
        GET https://api.crossref.org/works/{doi}
        or  https://api.crossref.org/works?query.bibliographic={title}&rows=1
        -> indexed title, authors, journal, publication year

    tool_openalex_lookup(doi)
        GET https://api.openalex.org/works/https://doi.org/{doi}
        -> inverted abstract text, citation count, open-access URL

    tool_openlibrary_lookup(isbn)
        GET https://openlibrary.org/isbn/{isbn}.json
        -> book title, publisher, publication date

Cost guardrail:
    Every lookup MUST be cached (see config/settings.yaml: storage.cache_table)
    before the network call is made, so repeated citations to the same
    source only ever hit the network once. Both OpenAlex and Crossref are
    free "polite pool" APIs — always send `mailto` per config/settings.yaml.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AcademicRecord:
    academic_record_found: bool
    source: str  # "crossref" | "openalex" | "openlibrary"
    title: str | None
    authors: list[str] | None
    journal: str | None
    publication_year: int | None
    abstract_text: str | None = None


async def tool_crossref_lookup(title: str | None, doi: str | None) -> AcademicRecord:
    """Verify journal metadata via the Crossref public polite pool."""
    raise NotImplementedError


async def tool_openalex_lookup(doi: str) -> AcademicRecord:
    """Fetch inverted abstract + metadata for a DOI via OpenAlex."""
    raise NotImplementedError


async def tool_openlibrary_lookup(isbn: str) -> AcademicRecord:
    """Verify a book citation via Open Library's ISBN endpoint."""
    raise NotImplementedError


async def cached_lookup(cache_key: str, fetch_fn) -> AcademicRecord:
    """Check the local DuckDB/SQLite cache before calling `fetch_fn`."""
    raise NotImplementedError
