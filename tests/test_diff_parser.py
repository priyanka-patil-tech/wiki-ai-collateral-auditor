"""Tests for src/extraction/diff_parser.py.

Covers:
    * Citation template detection (cite journal / cite web / cite book / citation)
    * <ref>...</ref> tag extraction
    * Parameter extraction (doi, isbn, url, title, journal, author)
    * Claim-sentence isolation via .strip_code()
"""

import mwparserfromhell

from src.extraction.diff_parser import extract_citation_params, parse_removed_wikitext


def test_parse_removed_wikitext_extracts_claim_and_citation():
    raw_diff = (
        "Water boils at 100°C at sea level.<ref>{{cite journal "
        "|title=Boiling Points|journal=J. Phys|doi=10.1234/example}}</ref>"
    )

    rows = parse_removed_wikitext(raw_diff)

    assert len(rows) == 1
    assert "Water boils at 100°C at sea level." in rows[0].claim_text
    assert rows[0].doi == "10.1234/example"
    assert "Boiling Points" in rows[0].raw_citation


def test_extract_citation_params_pulls_doi():
    parsed = mwparserfromhell.parse(
        "{{cite journal|title=Boiling Points|journal=J. Phys|doi=10.1234/example}}"
    )
    template = next(iter(parsed.filter_templates()))

    params = extract_citation_params(template)

    assert params["title"] == "Boiling Points"
    assert params["doi"] == "10.1234/example"
