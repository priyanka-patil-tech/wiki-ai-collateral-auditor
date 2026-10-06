from src.extraction.revert_miner import detect_rollback_revision, extract_reverted_claim_rows


def test_detect_rollback_revision_matches_keywords():
    assert detect_rollback_revision("Reverted edits by ExampleUser") is True
    assert detect_rollback_revision("WP:LLM flagged content") is True
    assert detect_rollback_revision("minor typo fix") is False
    assert detect_rollback_revision("minor edit", tags=["mw-reverted"]) is True
    assert detect_rollback_revision("minor edit", tags=["mw-manual-revert"]) is True


def test_extract_reverted_claim_rows_parses_deleted_wikitext():
    deleted_text = (
        "Water boils at 100°C at sea level. "
        "{{cite journal|title=Boiling Points|journal=J. Phys|doi=10.1234/example}}"
    )

    rows = extract_reverted_claim_rows(deleted_text)

    assert len(rows) == 1
    assert "Water boils at 100°C at sea level." in rows[0]["claim_text"]
    assert rows[0]["doi"] == "10.1234/example"
    assert rows[0]["citation_title"] == "Boiling Points"
