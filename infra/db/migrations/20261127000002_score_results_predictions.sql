-- migrate:up

-- What the scorer stores per score (contracts/score_result.schema.json, ADR 0019): the
-- outcome probabilities, the rank the queue is sorted by and how it was made, and the miner
-- version of the features (ADR 0027). The reason and label probabilities go in predictions.
ALTER TABLE score_results
  ADD COLUMN trigger              text,
  ADD COLUMN p_lands_clean        double precision CHECK (p_lands_clean BETWEEN 0 AND 1),
  ADD COLUMN p_lands_after_rework double precision CHECK (p_lands_after_rework BETWEEN 0 AND 1),
  ADD COLUMN p_goes_nowhere       double precision CHECK (p_goes_nowhere BETWEEN 0 AND 1),
  ADD COLUMN rank_score           double precision CHECK (rank_score BETWEEN 0 AND 1),
  ADD COLUMN rank_mode            text CHECK (rank_mode IN ('merge_first', 'risk_first', 'custom')),
  ADD COLUMN miner_version        text,
  ADD COLUMN predictions          jsonb;

-- A PR is ranked or unranked with a reason, never half; the rank is now rank_score.
-- risk_score stays, null until a label_bad model is served (ADR 0019).
ALTER TABLE score_results DROP CONSTRAINT ranked_or_unranked_with_reason;
-- Rows ranked before this migration carry their rank in risk_score: move it to rank_score.
UPDATE score_results SET rank_score = risk_score WHERE rank_score IS NULL AND risk_score IS NOT NULL;
ALTER TABLE score_results ADD CONSTRAINT ranked_or_unranked_with_reason CHECK (
  (rank_score IS NOT NULL AND rank_band IS NOT NULL AND model_version IS NOT NULL AND unranked_reason IS NULL)
  OR (rank_score IS NULL AND rank_band IS NULL AND unranked_reason IS NOT NULL)
);

-- Bands are cut from the repo's recent rank scores.
CREATE INDEX score_results_repo_recent ON score_results (tenant_id, repo, scored_at DESC) WHERE rank_score IS NOT NULL;

-- migrate:down

DROP INDEX score_results_repo_recent;
ALTER TABLE score_results DROP CONSTRAINT ranked_or_unranked_with_reason;
-- The old rule needs risk_score on ranked rows: carry the rank over before restoring it.
UPDATE score_results SET risk_score = rank_score WHERE risk_score IS NULL AND rank_score IS NOT NULL;
ALTER TABLE score_results ADD CONSTRAINT ranked_or_unranked_with_reason CHECK (
  (risk_score IS NOT NULL AND rank_band IS NOT NULL AND model_version IS NOT NULL AND unranked_reason IS NULL)
  OR (risk_score IS NULL AND rank_band IS NULL AND unranked_reason IS NOT NULL)
);
ALTER TABLE score_results
  DROP COLUMN predictions, DROP COLUMN miner_version, DROP COLUMN rank_mode, DROP COLUMN rank_score,
  DROP COLUMN p_goes_nowhere, DROP COLUMN p_lands_after_rework, DROP COLUMN p_lands_clean, DROP COLUMN trigger;
