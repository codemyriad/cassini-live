#!/usr/bin/env bash
# fold-changelog.sh — fold changelog.d/*.md fragments into CHANGELOG.md.
#
# Fragments keep agent-heavy PRs from colliding on CHANGELOG.md. At release
# time this script collects them, groups their entries under the Keep a
# Changelog headings in canonical order, and inserts a new
# `## [<version>] - <date>` section immediately after `## [Unreleased]`.
#
#   ./scripts/fold-changelog.sh --version 0.3.0-alpha.1 --check
#   ./scripts/fold-changelog.sh --version 0.3.0-alpha.1 --date 2026-07-06 --write
#
# --check validates every fragment and prints the section that would be
# inserted, touching nothing. --write splices the section into CHANGELOG.md and
# deletes the consumed fragments. Either way, a malformed fragment (unknown
# heading, text outside a heading, an empty heading) fails before any change.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib-release-version.sh
source "$SCRIPT_DIR/lib-release-version.sh"

# Keep a Changelog headings, in the canonical output order.
ACCEPTED="Added Changed Deprecated Removed Fixed Security"

usage() {
  cat >&2 <<'EOF'
Usage:
  ./scripts/fold-changelog.sh --version X.Y.Z[-pre] [--date YYYY-MM-DD] (--check|--write)
  ./scripts/fold-changelog.sh --preview        # group pending fragments, no version needed
EOF
}

# is_accepted <category> — return 0 when <category> is a known heading.
is_accepted() {
  local c="$1" a
  for a in $ACCEPTED; do [[ "$c" == "$a" ]] && return 0; done
  return 1
}

# parse_fragment <file> <workdir> — validate one fragment and append its
# entries to <workdir>/<Category>. Fails with a located message when the
# fragment has an unknown/malformed heading, text outside any heading, or a
# heading with no entries.
parse_fragment() {
  local frag="$1" work="$2" current="" line cat lineno=0 count_current=0 had_any=0
  while IFS= read -r line || [[ -n "$line" ]]; do
    lineno=$((lineno + 1))
    if [[ "$line" == \#* ]]; then
      if [[ "$line" =~ ^###[[:space:]]+([A-Za-z]+)[[:space:]]*$ ]]; then
        cat="${BASH_REMATCH[1]}"
        if ! is_accepted "$cat"; then
          echo "error: $frag:$lineno unknown heading '### $cat' (allowed: $ACCEPTED)" >&2
          return 1
        fi
        if [[ -n "$current" && "$count_current" -eq 0 ]]; then
          echo "error: $frag heading '### $current' has no entries" >&2
          return 1
        fi
        current="$cat"; count_current=0
      else
        echo "error: $frag:$lineno invalid heading '$line' (only '### Added|Changed|Deprecated|Removed|Fixed|Security')" >&2
        return 1
      fi
    elif [[ -z "${line//[[:space:]]/}" ]]; then
      : # blank line — drop; output formatting re-adds spacing
    else
      if [[ -z "$current" ]]; then
        echo "error: $frag:$lineno text outside any '###' heading: '$line'" >&2
        return 1
      fi
      printf '%s\n' "$line" >>"$work/$current"
      count_current=$((count_current + 1)); had_any=1
    fi
  done <"$frag"
  if [[ -n "$current" && "$count_current" -eq 0 ]]; then
    echo "error: $frag heading '### $current' has no entries" >&2
    return 1
  fi
  if [[ "$had_any" -eq 0 ]]; then
    echo "error: $frag has no changelog entries" >&2
    return 1
  fi
}

main() {
  local version="" date="" mode=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --version) version="${2:?--version needs a value}"; shift 2 ;;
      --date)    date="${2:?--date needs a value}"; shift 2 ;;
      --check)   mode="check"; shift ;;
      --write)   mode="write"; shift ;;
      --preview) mode="preview"; shift ;;
      -h|--help) usage; return 0 ;;
      *) echo "error: unknown argument '$1'" >&2; usage; return 1 ;;
    esac
  done

  [[ -n "$mode" ]] || { echo "error: one of --check, --write, or --preview is required" >&2; usage; return 1; }
  # --preview is version-agnostic (it just shows the pending entries); check and
  # write build a `## [<version>] - <date>` section and need the version.
  if [[ "$mode" != "preview" ]]; then
    [[ -n "$version" ]] || { echo "error: --version is required" >&2; usage; return 1; }
    rv_validate "$version" || return 1
  fi
  date="${date:-$(date +%F)}"
  if [[ ! "$date" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
    echo "error: --date must be YYYY-MM-DD, got: '$date'" >&2
    return 1
  fi

  local root changelog fragdir
  root="$(git rev-parse --show-toplevel)"
  changelog="$root/CHANGELOG.md"
  fragdir="$root/changelog.d"
  [[ -f "$changelog" ]] || { echo "error: $changelog not found" >&2; return 1; }

  # Collect fragment files (everything under changelog.d except README.md),
  # sorted for a stable within-category order.
  local frag frags=()
  for frag in "$fragdir"/*.md; do
    [[ -e "$frag" ]] || continue
    [[ "$(basename "$frag")" == "README.md" ]] && continue
    frags+=("$frag")
  done
  if [[ "${#frags[@]}" -eq 0 ]]; then
    if [[ "$mode" == "preview" ]]; then
      echo "(no pending changelog fragments in changelog.d/)"
      return 0
    fi
    echo "error: no changelog fragments in $fragdir (nothing to fold)" >&2
    return 1
  fi

  local work
  work="$(mktemp -d)"
  # Trap fires at process exit, after main() returns and its locals are gone,
  # so guard the expansion against set -u.
  trap 'rm -rf "${work:-}"' EXIT

  local f
  for f in "${frags[@]}"; do
    parse_fragment "$f" "$work" || return 1
  done

  # --preview: print the grouped entries with no version header, so a caller
  # (e.g. release-preview.sh) can show "what would ship" before a version is
  # even chosen.
  if [[ "$mode" == "preview" ]]; then
    local pcat
    for pcat in $ACCEPTED; do
      [[ -s "$work/$pcat" ]] || continue
      printf '### %s\n' "$pcat"
      cat "$work/$pcat"
      printf '\n'
    done
    printf '(%s pending fragment(s))\n' "${#frags[@]}"
    return 0
  fi

  # Build the release section from the accumulated categories, in canonical
  # order, including only categories that received entries.
  local section="$work/section.md" cat
  {
    printf '## [%s] - %s\n' "$version" "$date"
    for cat in $ACCEPTED; do
      if [[ -s "$work/$cat" ]]; then
        printf '\n### %s\n' "$cat"
        cat "$work/$cat"
      fi
    done
    printf '\n'
  } >"$section"

  if [[ "$mode" == "check" ]]; then
    echo "fold-changelog: would insert into CHANGELOG.md:"
    echo
    cat "$section"
    echo "fold-changelog: would consume ${#frags[@]} fragment(s):"
    for f in "${frags[@]}"; do echo "  ${f#"$root"/}"; done
    return 0
  fi

  # --write: splice the section in after the Unreleased block (before the first
  # existing version heading), then drop the consumed fragments.
  local new="$work/CHANGELOG.new"
  awk -v sf="$section" '
    function emit(   l) { while ((getline l < sf) > 0) print l; close(sf) }
    /^## \[Unreleased\]/ { seen = 1; print; next }
    seen && !done && /^## \[/ { emit(); done = 1; print; next }
    { print }
    END { if (seen && !done) emit() }
  ' "$changelog" >"$new"

  if ! grep -qF "## [$version] - $date" "$new"; then
    echo "error: failed to insert section (is there a '## [Unreleased]' heading in CHANGELOG.md?)" >&2
    return 1
  fi

  mv "$new" "$changelog"
  rm -f "${frags[@]}"
  echo "fold-changelog: wrote [$version] - $date and removed ${#frags[@]} fragment(s)."
}

main "$@"
