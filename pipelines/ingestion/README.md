# ingestion

Download and normalise the source datasets into one schema the feature
extractor can read: the repo miner's own rows (`repo_miner/`, the primary
training data), On the Shoulders of Giants, ApacheJIT, plus live labels from
merge, revert, and follow-up-fix history on connected repos. AIDev is being
added in `aidev/`, which so far pins one revision and checks it has not changed
(see its README); it is not yet part of the training data, and the miner still
produces the same labels from scratch.

Normalisation belongs here so training and serving never disagree about what
a field means.

Raw files are DVC-tracked, not committed.
