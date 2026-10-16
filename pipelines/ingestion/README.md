# ingestion

Download and normalise the source datasets into one schema the feature
extractor can read: the repo miner's own rows (`repo_miner/`, the primary
training data), On the Shoulders of Giants, ApacheJIT, plus live labels from
merge, revert, and follow-up-fix history on connected repos. AIDev is not
ingested; the miner produces the same labels from scratch.

Normalisation belongs here so training and serving never disagree about what
a field means.

Raw files are DVC-tracked, not committed.
