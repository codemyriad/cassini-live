#!/usr/bin/env bash
# sync-release-to-main.sh — bring a release cut on release/X.Y back to main.
#
# prepare-release.sh on a release branch folds that branch's changelog.d/
# fragments into its CHANGELOG.md, deletes them, and bumps its info.xml — on
# the release branch only. The fixes were cherry-picked from main with their
# fragments, so main still holds them: left alone, they would be released a
# second time in the next minor's notes, main's CHANGELOG.md would never list
# the patch, and main's builds would report a version below what has shipped.
# Run this on a branch from main after the release is tagged; it makes one
# local commit that:
#
#   - inserts the release's `## [<version>]` section, as tagged, into
#     CHANGELOG.md in version order;
#   - deletes the fragments the release commit consumed (a cherry-pick keeps
#     the fragment's filename, so the names match on main);
#   - when main's version isn't above the release, sets it to the next minor's
#     X.(Y+1).0-alpha.0: the untagged version main is developing
#     (docs/release.md#mains-version).
#
#   git switch -c chore/sync-0.2.1-to-main origin/main
#   ./scripts/sync-release-to-main.sh --version 0.2.1
#
# It never pushes: push the branch and open a PR to main.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib-release-version.sh
source "$SCRIPT_DIR/lib-release-version.sh"

usage() {
  cat >&2 <<'EOF'
Usage:
  ./scripts/sync-release-to-main.sh --version X.Y.Z[-pre]

Run on a branch from main, after v<version> was cut on its release branch.
EOF
}

die() { echo "error: $*" >&2; exit 1; }

main() {
  local version=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --version) version="${2:?--version needs a value}"; shift 2 ;;
      -h|--help) usage; return 0 ;;
      *) echo "error: unknown argument '$1'" >&2; usage; return 1 ;;
    esac
  done
  [[ -n "$version" ]] || { echo "error: --version is required" >&2; usage; return 1; }
  rv_validate "$version" || return 1

  local root changelog tag branch
  root="$(git rev-parse --show-toplevel)"
  changelog="$root/CHANGELOG.md"
  tag="v$version"

  branch="$(git -C "$root" symbolic-ref --quiet --short HEAD)" \
    || die "HEAD is detached; switch to a branch from main first"
  [[ "$branch" != release/* ]] \
    || die "this brings a release back to main; run it on a branch from main, not $branch"
  git -C "$root" diff --quiet || die "worktree has unstaged changes; commit or stash first"
  git -C "$root" diff --cached --quiet || die "index has staged changes; commit or reset first"

  if git -C "$root" remote get-url origin >/dev/null 2>&1; then
    git -C "$root" fetch --quiet --tags origin \
      || die "could not fetch tags from origin"
  else
    echo "warning: no origin remote; using local tags only" >&2
  fi
  git -C "$root" rev-parse -q --verify "refs/tags/$tag" >/dev/null \
    || die "tag $tag not found"
  [[ "$(git -C "$root" log -1 --format=%s "$tag")" == "release: $version" ]] \
    || die "$tag is not a prepare-release.sh commit ('release: $version'); sync it by hand"
  [[ "$(rv_release_branch "$version")" != main ]] \
    || die "$version is cut on main; there is nothing to bring back"
  # Globals, not locals: the EXIT trap runs after main has returned.
  SYNC_TAGGED="$(mktemp)"
  SYNC_TMP="$(mktemp)"
  trap 'rm -f "$SYNC_TAGGED" "$SYNC_TMP"' EXIT
  local tagged="$SYNC_TAGGED" tmp="$SYNC_TMP"

  # The section as tagged, header included. A release cut with
  # --allow-empty-changelog has none; its version still comes back to main.
  local section=""
  git -C "$root" show "$tag:CHANGELOG.md" >"$tagged"
  if grep -qF "## [$version]" "$changelog"; then
    echo "note: CHANGELOG.md already has a $version section"
  elif ! grep -qF "## [$version]" "$tagged"; then
    echo "note: $tag's CHANGELOG.md has no $version section"
  else
    # The header is there, so a failure here is real (an empty section, an
    # I/O error): stop before deleting main's fragments for entries that would
    # then exist nowhere on main.
    section="$("$SCRIPT_DIR/extract-release-notes.sh" --version "$version" --changelog "$tagged" --with-header)" \
      || die "could not extract the $version section from $tag's CHANGELOG.md"
  fi

  if [[ -n "$section" ]]; then
    # Insert before the first versioned section that is older than this one; main
    # lists 0.3.0-alpha.1 above 0.2.2 above 0.2.1. Append if every one is newer.
    local insert_at="" n hdr v
    while IFS=: read -r n hdr; do
      v="${hdr#'## ['}"
      v="${v%%]*}"
      rv_validate "$v" >/dev/null 2>&1 || continue
      if rv_gt "$version" "$v"; then insert_at="$n"; break; fi
    done < <(grep -n '^## \[' "$changelog")

    if [[ -n "$insert_at" ]]; then
      {
        head -n "$((insert_at - 1))" "$changelog"
        printf '%s\n\n' "$section"
        tail -n "+$insert_at" "$changelog"
      } >"$tmp"
    else
      { cat "$changelog"; printf '\n%s\n' "$section"; } >"$tmp"
    fi
    # Write in place: mv would give CHANGELOG.md mktemp's 0600 mode.
    cat "$tmp" >"$changelog"
    git -C "$root" add -- "$changelog"
    echo "Added the $version section to CHANGELOG.md."
  fi

  # Fragments the release commit deleted. One written directly on the release
  # branch was never on main; say so rather than skip it silently.
  local frag removed=0
  while IFS= read -r frag; do
    [[ -n "$frag" ]] || continue
    if [[ -e "$root/$frag" ]]; then
      git -C "$root" rm -q -- "$frag"
      removed=$((removed + 1))
    else
      echo "note: $frag is not on this branch (written on the release branch?)"
    fi
  done < <(git -C "$root" diff --name-only --diff-filter=D "$tag^" "$tag" -- changelog.d/)

  [[ "$removed" -eq 0 ]] || echo "Removed $removed released fragment(s)."

  # Main carries the version it is developing, above everything shipped; after
  # 0.2.1 that is 0.3.0-alpha.0 unless main already moved past it.
  local info_xml current major minor next
  info_xml="$root/appinfo/info.xml"
  current="$(rv_read_version "$info_xml")" || die "could not read main's version"
  if ! rv_gt "$current" "$version"; then
    read -r major minor _ _ _ <<<"$(rv_parse "$version")"
    next="${major}.$((minor + 1)).0-alpha.0"
    rv_write_release_version "$root" "$next" || die "failed to update the version files"
    local files=()
    mapfile -t files < <(rv_release_files "$root")
    git -C "$root" add -- "${files[@]}"
    echo "Main's version $current -> $next (above the released $version)."
  fi

  if git -C "$root" diff --cached --quiet; then
    die "main already has everything from $version; nothing to commit"
  fi
  git -C "$root" commit -q -m "chore(release): bring $version back to main"
  cat <<EOF

Nothing has been pushed. Review the commit, then open a PR to main:
  git show
  git push -u origin $branch
EOF
}

main "$@"
