"""
reinsertion_tracer.py — Longitudinal re-addition audit.

Implements Subsystem 3A (Longitudinal Re-Insertion Tracer) from the System
Design Document, §4.3.

Hypothesis:
    Legitimate facts swept away in a mass rollback are subsequently
    re-added by independent, non-sanctioned editors.

Algorithm (per deleted claim C_i on page P_j reverted at T0):
    1. Query page revisions at T0 + 7d, T0 + 30d, and T0 + 90d
       (see config/settings.yaml: reinsertion_tracer.windows_days).
    2. Extract all citations and text blocks added after T0.
    3. Compute Jaccard similarity between C_i and each post-T0 candidate:
           Similarity(C_i, C_post) = |C_i ∩ C_post| / |C_i ∪ C_post|
    4. Check for an exact DOI/ISBN match between C_i and C_post.
    5. If Similarity > similarity_threshold (default 0.80) OR an exact
       DOI/ISBN match is found, flag the claim as a Confirmed Re-Insertion.

Output:
    Feeds the `reinserted_at_30d` (and, at 90d, an equivalent) flag on
    `audit_verifications` (System Design Document §5 / §2.1).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ReinsertionResult:
    claim_id: str
    window_days: int
    similarity_score: float
    doi_matched: bool
    confirmed_reinserted: bool


def jaccard_similarity(claim_text: str, candidate_text: str) -> float:
    """|claim ∩ candidate| / |claim ∪ candidate| over token sets."""
    raise NotImplementedError


def trace_reinsertion(claim, page_id: int, t0, windows_days: list[int] = (7, 30, 90)) -> list[ReinsertionResult]:
    """Check a deleted claim for re-insertion at each configured window."""
    raise NotImplementedError
