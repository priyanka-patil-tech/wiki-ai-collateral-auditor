"""
panel_builder.py — Assembles (User, Revision, Time) panel.

Implements Layer 4's storage/assembly step, System Design Document §3.1
("Econometric Estimation Engine") and §5 (Storage Schema).

Joins the four core tables into a single analysis-ready panel:
    cohorts             (user_id, cohort_type, t0_timestamp, status)
    deleted_revisions   (revision_id, user_id, latency_seconds, ...)
    extracted_claims    (claim_id, revision_id, claim_text, doi, isbn)
    audit_verifications (claim_id, classification, is_type_1_error, ...)

Unit of analysis: (User i, Revision r, Month t) — aggregated for the
fixed-effects / event-study specifications in src/econometrics/estimation.py.

Derived panel columns:
    treated              1 if cohort_type == 'TREATED_AI' else 0
    post                 1 if timestamp >= t0_timestamp else 0
    rel_month            months relative to t0_timestamp (event-study time)
    type_1_error_rate    share of a user-month's claims with is_type_1_error
    review_latency       log(latency_seconds)

Storage engine: DuckDB / Parquet (config/settings.yaml: storage.*).
"""

from __future__ import annotations


def build_panel(cohorts_df, deleted_revisions_df, extracted_claims_df, audit_verifications_df):
    """Join the four tables into a (User, Revision, Month) panel DataFrame.

    Returns a DataFrame with, at minimum: user_id, month_year, treated,
    post, rel_month, type_1_error_rate, review_latency — ready for
    src/econometrics/estimation.py.
    """
    raise NotImplementedError


def compute_collateral_damage_ratio(panel_df) -> float:
    """CDR = sum(Type I Errors) / total reverted claims under presumptive policy."""
    raise NotImplementedError


def compute_review_latency_delta(panel_df) -> float:
    """Δτ = median(revert - add)_post - median(revert - add)_pre."""
    raise NotImplementedError
