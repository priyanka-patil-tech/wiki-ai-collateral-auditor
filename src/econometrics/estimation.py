"""
estimation.py — Fixed-effects DiD and Event-Study regressions.

Implements the empirical estimator from the System Design Document §6.2,
run via `pyfixest` against the panel produced by
src/econometrics/panel_builder.py.

Panel structure (System Design Document §6.1):
    N(treated) ≈ 150-300 accounts flagged on WP:AINB
    N(control) ≈ 300-500 accounts sanctioned on WP:ANI
    Time window: T0 ± 6 months
    Clustered standard errors at the user level (config: econometrics.cluster_var)
"""

from __future__ import annotations

import pyfixest as pf


def fit_difference_in_differences(panel_df):
    """Two-way fixed-effects DiD:

        type_1_error_rate ~ treated:post | user_id + month_year
    """
    return pf.feols(
        "type_1_error_rate ~ treated:post | user_id + month_year",
        data=panel_df,
        cluster="user_id",
    )


def fit_event_study(panel_df):
    """Dynamic event-study specification:

        type_1_error_rate ~ i(rel_month, treated, ref=-1) | user_id + month_year
    """
    return pf.feols(
        "type_1_error_rate ~ i(rel_month, treated, ref=-1) | user_id + month_year",
        data=panel_df,
        cluster="user_id",
    )
