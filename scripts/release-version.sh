#!/usr/bin/env bash
# release-version.sh — inspect and advance the Cassini Live release version.
#
# The version is read from appinfo/info.xml <version>, pinned in lockstep to
# the AppAPI Docker <image-tag> and to every other file that carries it (see
# rv_write_release_version in lib-release-version.sh). This CLI is the single
# entry point for moving along the Nextcloud release ladder:
#
#   X.Y.Z-alpha.1 -> X.Y.Z-beta.1 -> X.Y.Z-rc.1 -> X.Y.Z-rc.2 -> X.Y.Z
#
# It only edits those files. Committing, tagging and pushing are left to
# the operator (or scripts/prepare-release.sh), so you can inspect the diff
# before a release becomes real.
#
# Usage:
#   ./scripts/release-version.sh current
#   ./scripts/release-version.sh bump patch|minor|major
#   ./scripts/release-version.sh promote alpha|beta|rc.1|rc.2|stable
#   ./scripts/release-version.sh set X.Y.Z[-(alpha|beta|rc).N]
#   ./scripts/release-version.sh branch X.Y.Z[-pre]   # the branch it is cut on
#   ./scripts/release-version.sh previous X.Y.Z[-pre] <versions  # newest lower one in its line

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib-release-version.sh
source "$SCRIPT_DIR/lib-release-version.sh"

usage() {
  cat >&2 <<'EOF'
Usage:
  ./scripts/release-version.sh current
  ./scripts/release-version.sh bump patch|minor|major
  ./scripts/release-version.sh promote alpha|beta|rc.1|rc.2|stable
  ./scripts/release-version.sh set X.Y.Z[-(alpha|beta|rc).N]
  ./scripts/release-version.sh branch X.Y.Z[-(alpha|beta|rc).N]
  ./scripts/release-version.sh previous X.Y.Z[-(alpha|beta|rc).N] < versions
EOF
}

# report <from> <to>: summarize the edit and print the release follow-up.
report() {
  local from="$1" to="$2" branch
  branch="$(rv_release_branch "$to")"
  echo "version ${from} -> ${to} in both manifests (image-tag kept in lockstep), pyproject.toml, cassini_live/__init__.py and uv.lock"
  echo
  echo "Next steps, on ${branch} (or run scripts/prepare-release.sh, which checks them):"
  echo "  git commit -am \"release: ${to}\""
  echo "  git tag v${to}"
  echo "  git push --atomic origin ${branch} v${to}"
}

main() {
  local root info_xml current next cmd
  root="$(git rev-parse --show-toplevel)"
  info_xml="$root/appinfo/info.xml"
  cmd="${1:-}"
  case "$cmd" in
    current)
      rv_read_version "$info_xml"
      ;;
    bump)
      local level="${2:?usage: $0 bump patch|minor|major}"
      current="$(rv_read_version "$info_xml")"
      next="$(rv_bump "$level" "$current")"
      rv_write_release_version "$root" "$next"
      report "$current" "$next"
      ;;
    promote)
      local target="${2:?usage: $0 promote alpha|beta|rc.1|rc.2|stable}"
      current="$(rv_read_version "$info_xml")"
      next="$(rv_promote "$target" "$current")"
      rv_write_release_version "$root" "$next"
      report "$current" "$next"
      ;;
    set)
      local explicit="${2:?usage: $0 set X.Y.Z[-(alpha|beta|rc).N]}"
      rv_validate "$explicit"
      current="$(rv_read_version "$info_xml")"
      if [[ "$current" == "$explicit" ]]; then
        echo "appinfo/info.xml is already at $explicit — nothing to do."
        return 0
      fi
      rv_write_release_version "$root" "$explicit"
      report "$current" "$explicit"
      ;;
    branch)
      rv_release_branch "${2:?usage: $0 branch X.Y.Z[-(alpha|beta|rc).N]}"
      ;;
    previous)
      rv_previous_in_line "${2:?usage: $0 previous X.Y.Z[-(alpha|beta|rc).N] < versions}"
      ;;
    -h|--help|help|"")
      usage
      ;;
    *)
      echo "error: unknown command '$cmd'" >&2
      usage >&2
      exit 1
      ;;
  esac
}

main "$@"
