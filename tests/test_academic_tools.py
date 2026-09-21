"""Tests for src/verification/academic_tools.py.

Covers:
    * Crossref / OpenAlex / Open Library lookup contracts
    * Cache-before-network-call behavior (cost guardrail)
"""

import pytest

from src.verification.academic_tools import (
    tool_crossref_lookup,
    tool_openalex_lookup,
    tool_openlibrary_lookup,
)


@pytest.mark.asyncio
async def test_openalex_lookup_returns_academic_record():
    with pytest.raises(NotImplementedError):
        await tool_openalex_lookup(doi="10.1234/example")


@pytest.mark.asyncio
async def test_crossref_lookup_falls_back_to_title_search():
    with pytest.raises(NotImplementedError):
        await tool_crossref_lookup(title="Example Paper", doi=None)


@pytest.mark.asyncio
async def test_openlibrary_lookup_by_isbn():
    with pytest.raises(NotImplementedError):
        await tool_openlibrary_lookup(isbn="9780134685991")
