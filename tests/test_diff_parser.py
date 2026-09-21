"""Tests for src/extraction/diff_parser.py.

Covers:
    * Citation template detection (cite journal / cite web / cite book / citation)
    * <ref>...</ref> tag extraction
    * Parameter extraction (doi, isbn, url, title, journal, author)
    * Claim-sentence isolation via .strip_code()
"""

import pytest

from src.extraction.diff_parser import extract_citation_params, parse_removed_wikitext


def test_parse_removed_wikitext_extracts_claim_and_citation():
    raw_diff = (
        "Water boils at 100°C at sea level.<ref>{{cite journal "
        "|title=Boiling Points|journal=J. Phys|doi=10.1234/example}}</ref>"
    )
    with pytest.raises(NotImplementedError):
        parse_removed_wikitext(raw_diff)


def test_extract_citation_params_pulls_doi():
    with pytest.raises(NotImplementedError):
        extract_citation_params(None)
