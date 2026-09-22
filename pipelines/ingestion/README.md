# ingestion

Download and normalise the source datasets into one schema the feature
extractor can read: AIDev, On the Shoulders of Giants, ApacheJIT, plus live
labels from merge, revert, and follow-up-fix history on connected repos.

Normalisation belongs here so training and serving never disagree about what
a field means.

Raw files are DVC-tracked, not committed.
