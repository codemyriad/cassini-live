#!/usr/bin/env bash
# check-tag-branch.sh — is a release tag's commit on the branch its version is
# cut on (docs/release.md#branches: X.Y.0-alpha.N on main, the rest of an X.Y
# line on release/X.Y)?
#
#   ./scripts/check-tag-branch.sh 0.2.1 "$GITHUB_SHA"
#
# The one copy of this rule for the workflows: publish-exapp-image.yml runs it
# before any immutable :X.Y.Z image is pushed, release.yml before the App
# Store. It asks GitHub's compare API rather than git, so a shallow checkout
# works: <branch>...<sha> is "behind" or "identical" exactly when the commit is
# on the branch. Needs gh with GH_TOKEN, and GITHUB_REPOSITORY.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib-release-version.sh
source "$SCRIPT_DIR/lib-release-version.sh"

die() {
  if [[ "${GITHUB_ACTIONS:-}" == true ]]; then echo "::error::$*" >&2; else echo "error: $*" >&2; fi
  exit 1
}

main() {
  local version="${1:?usage: $0 <version> <commit-sha>}" sha="${2:?usage: $0 <version> <commit-sha>}"
  local repo="${GITHUB_REPOSITORY:?GITHUB_REPOSITORY must be set (owner/repo)}"
  local branch status
  branch="$(rv_release_branch "$version")" || exit 1

  if ! status="$(gh api "repos/${repo}/compare/${branch}...${sha}" --jq .status 2>&1)"; then
    # A missing branch (or commit) is a 404; anything else is the API failing,
    # not the tag being off its branch, so say which.
    if [[ "$status" == *"HTTP 404"* ]]; then
      die "v${version} is cut on ${branch}, which GitHub doesn't have (or doesn't have ${sha}); see docs/release.md#branches"
    fi
    die "could not compare ${sha} with ${branch}: ${status}"
  fi
  case "$status" in
    behind|identical) echo "v${version} is on ${branch}" ;;
    *) die "v${version} is cut on ${branch}, but ${sha} is not on it (compare: ${status}); see docs/release.md#branches" ;;
  esac
}

main "$@"
