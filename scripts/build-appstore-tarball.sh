#!/usr/bin/env bash
# build-appstore-tarball.sh — assemble the Nextcloud App Store package.
#
# Ported from codemyriad/gocassini. Cassini Live is an ExApp: the runtime is
# the pinned Docker image in the manifest, so the App Store archive is just the
# manifest and a little metadata. Files are copied by an explicit allowlist —
# source, Dockerfiles, tests and any signing material can never leak in because
# they are simply never listed.
#
# The packaged manifest is appstore/info.xml (the store app id), not
# appinfo/info.xml (the native `live_transcription` drop-in, registered by hand
# with occ). The archive root is named after appstore/info.xml's <id>.
#
#   # Local, unsigned: build the staging tree and the tarball in one go.
#   ./scripts/build-appstore-tarball.sh --version 0.12.0-alpha.1
#
#   # CI signed flow, in three steps (occ signs the tree in between):
#   ./scripts/build-appstore-tarball.sh --version V --stage-only
#   occ integrity:sign-app --path build/appstore/<id> ...   # adds signature.json
#   ./scripts/build-appstore-tarball.sh --version V --archive-only
#
# Options:
#   --version X.Y.Z[-pre]   required; must equal the packaged info.xml <version>
#   --staging DIR           staging root (default: build/appstore)
#   --output PATH           tarball path (default: build/artifacts/appstore/<id>.tar.gz)
#   --stage-only            build the <id>/ tree, do not create the tarball
#   --archive-only          tar an existing (e.g. already-signed) tree; do not rebuild it

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib-release-version.sh
source "$SCRIPT_DIR/lib-release-version.sh"

# Allowlist of "source:destination" pairs copied into <id>/ (sources relative
# to the repo root, destinations to the archive root). README.md is
# intentionally excluded: the App Store reads metadata from info.xml, and
# <website> already links the repo README. No appinfo/app.php: an ExApp has no
# PHP, and Nextcloud's own ExApps (e.g. live_transcription) ship none.
APPSTORE_FILES=(
  appstore/info.xml:appinfo/info.xml
  img/app.svg:img/app.svg
  CHANGELOG.md:CHANGELOG.md
  LICENSE:LICENSE
)

usage() {
  cat >&2 <<'EOF'
Usage:
  ./scripts/build-appstore-tarball.sh --version X.Y.Z[-pre]
      [--staging DIR] [--output PATH] [--stage-only | --archive-only] [--sign-app]

--sign-app runs `occ integrity:sign-app` on the staged tree. It needs, in the
environment:
  OCC              command that runs occ against a working Nextcloud, e.g.
                   "docker exec -u www-data nc php occ" — occ needs a bootable
                   Nextcloud, so how you provide one is left to the caller.
  APP_PRIVATE_KEY  PEM content of the app signing key
  APP_PUBLIC_CRT   PEM content of the signed certificate
EOF
}

die() { echo "error: $*" >&2; exit 1; }

main() {
  local version="" staging="" output="" mode="both" sign_app=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --version)      version="${2:?--version needs a value}"; shift 2 ;;
      --staging)      staging="${2:?--staging needs a value}"; shift 2 ;;
      --output)       output="${2:?--output needs a value}"; shift 2 ;;
      --stage-only)   mode="stage"; shift ;;
      --archive-only) mode="archive"; shift ;;
      --sign-app)     sign_app=1; shift ;;
      -h|--help)      usage; return 0 ;;
      *) echo "error: unknown argument '$1'" >&2; usage; return 1 ;;
    esac
  done
  [[ -n "$version" ]] || { echo "error: --version is required" >&2; usage; return 1; }
  rv_validate "$version" || return 1
  # --sign-app signs the tree as it is staged; there is nothing to sign when
  # only re-archiving an existing tree.
  [[ "$sign_app" -eq 1 && "$mode" == "archive" ]] \
    && die "--sign-app cannot be combined with --archive-only"

  local root app_id
  root="$(git rev-parse --show-toplevel)"
  app_id="$(rv_read_app_id "$root/appstore/info.xml")" || return 1
  staging="${staging:-$root/build/appstore}"
  output="${output:-$root/build/artifacts/appstore/$app_id.tar.gz}"
  local tree="$staging/$app_id"

  if [[ "$mode" != "archive" ]]; then
    # The packaged manifest must be the one for this exact version.
    local packaged
    packaged="$(rv_read_version "$root/appstore/info.xml")" || return 1
    [[ "$packaged" == "$version" ]] \
      || die "appstore/info.xml is at $packaged, not $version; run scripts/release-version.sh first"

    rm -rf "$tree"
    local pair src dst
    for pair in "${APPSTORE_FILES[@]}"; do
      src="${pair%%:*}"; dst="${pair#*:}"
      [[ -f "$root/$src" ]] || die "missing packaged file: $src"
      mkdir -p "$tree/$(dirname "$dst")"
      cp "$root/$src" "$tree/$dst"
    done
    echo "Staged ${#APPSTORE_FILES[@]} files into ${tree#"$root"/}/"

    if [[ "$sign_app" -eq 1 ]]; then
      : "${OCC:?--sign-app requires OCC (a command that runs occ against a working Nextcloud) in the environment}"
      : "${APP_PRIVATE_KEY:?--sign-app requires APP_PRIVATE_KEY (PEM content) in the environment}"
      : "${APP_PUBLIC_CRT:?--sign-app requires APP_PUBLIC_CRT (PEM content) in the environment}"
      local keydir key crt
      keydir="$(mktemp -d)"
      # shellcheck disable=SC2064  # expand keydir now; it is fixed for this run.
      trap "rm -rf '$keydir'" EXIT
      key="$keydir/$app_id.key"
      crt="$keydir/$app_id.crt"
      ( umask 077; printf '%s\n' "$APP_PRIVATE_KEY" >"$key"; printf '%s\n' "$APP_PUBLIC_CRT" >"$crt" )
      echo "Signing $tree via: \$OCC integrity:sign-app"
      # $OCC is a command with arguments (e.g. docker exec … php occ); it must
      # word-split, so it is intentionally unquoted.
      # shellcheck disable=SC2086
      $OCC integrity:sign-app --privateKey="$key" --certificate="$crt" --path="$tree"
      [[ -f "$tree/appinfo/signature.json" ]] \
        || die "signing reported success but $tree/appinfo/signature.json is missing"
      echo "Signed: appinfo/signature.json present"
    fi
  fi

  if [[ "$mode" != "stage" ]]; then
    [[ -d "$tree" ]] || die "no staging tree at $tree (run without --archive-only first)"
    mkdir -p "$(dirname "$output")"
    # Archive root is <id>/ because we tar the directory by name from its
    # parent. --owner/--group keep it independent of the building user.
    tar -czf "$output" --owner=0 --group=0 -C "$staging" "$app_id"
    echo "Wrote ${output#"$root"/} ($(wc -c <"$output") bytes)"
  fi
}

main "$@"
