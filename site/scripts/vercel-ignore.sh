#!/usr/bin/env bash
# Vercel "Ignored Build Step" (see vercel.json). Exit 0 skips the deployment, exit 1 builds it.
#
# A deployment is only needed when something the site is built from changed: site/ itself,
# and the files scripts/build-docs.mjs reads from outside it (its SOURCES). Keep the paths
# below in step with SOURCES; contracts/tests/test_vercel_ignore.py fails if they drift.
#
# Anything unexpected (no base commit in a shallow clone, git failing) builds, because a
# needless deployment costs less than a missed one.
set -u

root=$(git rev-parse --show-toplevel 2>/dev/null) || { echo "not in a git checkout: building"; exit 1; }
cd "$root" || exit 1

# the last deployed commit of this branch when Vercel knows it, else the parent commit
base="${VERCEL_GIT_PREVIOUS_SHA:-HEAD^}"
if ! git cat-file -e "${base}^{commit}" 2>/dev/null; then
  echo "base commit ${base} is not available: building"
  exit 1
fi

if git diff --quiet "$base" HEAD -- \
    site \
    contracts/REFERENCE.md \
    pipelines/ingestion/repo_miner/ARCHITECTURE.md; then
  echo "nothing the site is built from changed since ${base}: skipping the deployment"
  exit 0
fi

echo "the site or its docs sources changed since ${base}: building"
exit 1
