#!/usr/bin/env bash
# extract-release-notes.sh — print one version's CHANGELOG.md section body.
#
# Prints everything under `## [<version>] - <date>` up to the next `## [`
# heading, with the header line itself and surrounding blank lines stripped —
# i.e. the release-note body suitable for a GitHub release. The release title
# already carries the version, so the `## [<version>]` line is omitted.
#
#   ./scripts/extract-release-notes.sh --version 0.3.0-alpha.1
#   ./scripts/extract-release-notes.sh --version 0.3.0-alpha.1 --changelog path/to/CHANGELOG.md
#
# --with-header keeps the `## [<version>] - <date>` line (and a blank line)
# above the body, i.e. the whole section as it sits in CHANGELOG.md.
#
# Exits non-zero if the section is absent or empty.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib-release-version.sh
source "$SCRIPT_DIR/lib-release-version.sh"

usage() {
  cat >&2 <<'EOF'
Usage:
  ./scripts/extract-release-notes.sh --version X.Y.Z[-pre] [--changelog FILE] [--with-header]
EOF
}

main() {
  local version="" changelog="" with_header=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --version)   version="${2:?--version needs a value}"; shift 2 ;;
      --changelog) changelog="${2:?--changelog needs a value}"; shift 2 ;;
      --with-header) with_header=1; shift ;;
      -h|--help)   usage; return 0 ;;
      *) echo "error: unknown argument '$1'" >&2; usage; return 1 ;;
    esac
  done

  [[ -n "$version" ]] || { echo "error: --version is required" >&2; usage; return 1; }
  rv_validate "$version" || return 1
  changelog="${changelog:-$(git rev-parse --show-toplevel)/CHANGELOG.md}"
  [[ -f "$changelog" ]] || { echo "error: $changelog not found" >&2; return 1; }

  local body header
  # Buffer the section, then trim leading/trailing blank lines. Header match is
  # literal (index==1) so version dots aren't treated as regex wildcards.
  body="$(awk -v hdr="## [$version]" '
    index($0, hdr) == 1 { insec = 1; next }
    insec && index($0, "## [") == 1 { insec = 0 }
    insec { buf[n++] = $0 }
    END {
      s = 0; while (s < n && buf[s] ~ /^[[:space:]]*$/) s++
      e = n - 1; while (e >= s && buf[e] ~ /^[[:space:]]*$/) e--
      for (i = s; i <= e; i++) print buf[i]
    }
  ' "$changelog")"

  if [[ -z "$body" ]]; then
    echo "error: no release notes for $version in $changelog" >&2
    return 1
  fi
  if [[ "$with_header" -eq 1 ]]; then
    header="$(awk -v hdr="## [$version]" 'index($0, hdr) == 1 { print; exit }' "$changelog")"
    printf '%s\n\n' "$header"
  fi
  printf '%s\n' "$body"
}

main "$@"
