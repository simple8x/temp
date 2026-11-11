#!/usr/bin/env bash
# Isolation gate: every commit on this branch after the common root may touch ONLY
#   claude/**   and   .github/workflows/claude-*.yml
# Anything else (including the original upload and the DeepSeek/Manus work) must be untouched.
set -euo pipefail
ROOT_SHA="${ROOT_SHA:-506c0bc49dd44062cb71f5235f82f3ed4e73c242}"

if ! git cat-file -e "${ROOT_SHA}^{commit}" 2>/dev/null; then
  echo "FAIL: root commit ${ROOT_SHA} not present (need fetch-depth: 0)"; exit 2
fi
if ! git merge-base --is-ancestor "${ROOT_SHA}" HEAD; then
  echo "FAIL: HEAD does not descend from the common root ${ROOT_SHA}"; exit 2
fi

violations=0
while IFS= read -r commit; do
  [ -z "$commit" ] && continue
  while IFS= read -r path; do
    [ -z "$path" ] && continue
    case "$path" in
      claude/*) ;;
      .github/workflows/claude-*.yml) ;;
      *) echo "VIOLATION in ${commit:0:10}: $path"; violations=$((violations+1));;
    esac
  done < <(git diff-tree --no-commit-id --name-only -r -m --root "$commit")
done < <(git rev-list "${ROOT_SHA}..HEAD")

# The only first-parent history allowed below HEAD must be linear from root: no foreign merges
merges=$(git rev-list --merges "${ROOT_SHA}..HEAD" | wc -l)
if [ "$merges" -ne 0 ]; then echo "VIOLATION: ${merges} merge commit(s) on branch"; violations=$((violations+1)); fi

# Ground-truth guard: the root upload must be byte-identical to the root commit
if ! git diff --quiet "${ROOT_SHA}" HEAD -- . ':(exclude)claude' ':(exclude).github/workflows/claude-*.yml'; then
  echo "VIOLATION: files outside the allowed paths differ from the root commit"; violations=$((violations+1))
fi

echo "commits checked: $(git rev-list --count "${ROOT_SHA}..HEAD"), violations: ${violations}"
[ "$violations" -eq 0 ] && echo "ISOLATION: PASS" || { echo "ISOLATION: FAIL"; exit 1; }
