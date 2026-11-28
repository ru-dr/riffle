"""From the model's probabilities to the queue's order and band.

rank_score (merge_first, the default ranking mode): p_lands_clean + 0.6 x p_lands_after_rework, so PRs likely to
land, clean first, come first. Bands are percentiles of the repo's recent rank scores, with org_config's defaults
(contracts/org_config.schema.json, rank_bands): the top 5% senior_recommended, the top 20% review_first, the rest
standard. Per-tenant settings come with the config compiler; until then every tenant gets the defaults. Settings
never change what the model sees, only the order and bands.
"""
from __future__ import annotations

from typing import Sequence

RANK_MODE = "merge_first"
REWORK_WEIGHT = 0.6
SENIOR_RECOMMENDED_TOP_FRACTION = 0.05     # org_config rank_bands.percentile defaults
REVIEW_FIRST_TOP_FRACTION = 0.2
MIN_SCORES_FOR_BANDS = 20                  # fewer recent scores in the repo: no percentile yet, band standard
RECENT_DAYS = 90
RECENT_LIMIT = 1000


def rank_score(p_lands_clean: float, p_lands_after_rework: float) -> float:
    return min(1.0, max(0.0, p_lands_clean + REWORK_WEIGHT * p_lands_after_rework))


def band(score: float, recent: Sequence[float],
         senior_top: float = SENIOR_RECOMMENDED_TOP_FRACTION, review_top: float = REVIEW_FIRST_TOP_FRACTION) -> str:
    """The band of `score` among the repo's recent rank scores (other PRs, latest score of each)."""
    if len(recent) < MIN_SCORES_FOR_BANDS:
        return "standard"
    above = sum(1 for r in recent if r > score) / len(recent)
    if above < senior_top:
        return "senior_recommended"
    if above < review_top:
        return "review_first"
    return "standard"
