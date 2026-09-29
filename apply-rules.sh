#!/usr/bin/env bash
# Apply ADR 0003 to ru-dr/riffle. Run from the repo root.
set -euo pipefail
REPO=ru-dr/riffle

gh api -X PATCH "repos/$REPO" \
  -F allow_squash_merge=true -F allow_merge_commit=false -F allow_rebase_merge=false \
  -F delete_branch_on_merge=true -F allow_update_branch=true >/dev/null
echo "repo merge settings updated"

id=$(gh api "repos/$REPO/rulesets" --jq '.[] | select(.name=="protect-main") | .id' || true)
if [ -n "$id" ]; then
  gh api -X PUT "repos/$REPO/rulesets/$id" --input .github/rulesets/main.json >/dev/null
  echo "ruleset protect-main updated ($id)"
else
  gh api -X POST "repos/$REPO/rulesets" --input .github/rulesets/main.json >/dev/null
  echo "ruleset protect-main created"
fi
