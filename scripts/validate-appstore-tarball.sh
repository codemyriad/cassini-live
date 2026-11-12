#!/usr/bin/env bash
# validate-appstore-tarball.sh — enforce the App Store archive invariants.
#
# Ported from codemyriad/gocassini. Runs against a built tarball with no
# secrets, so CI can gate an upload without App Store credentials. <id> is the
# store app id: --app-id, or appstore/info.xml's <id> by default. Checks:
#   - every entry is under a single `<id>/` root
#   - <id>/appinfo/info.xml exists and parses
#   - its <id> is the store app id (never Talk's `live_transcription`)
#   - <version> equals the requested release version
#   - the Docker <image-tag> equals the requested version
#   - the archive is below 20 MiB and info.xml below 512 KiB (store limits)
#   - info.xml validates the way apps.nextcloud.com does: pre-info.xslt then
#     info.xsd (see the store-schema note below)
#   - no private key / certificate / CSR material is present
#   - <id>/appinfo/signature.json exists when --signed is given
#
#   ./scripts/validate-appstore-tarball.sh --version 0.12.0-alpha.1 --tarball path.tar.gz
#   ./scripts/validate-appstore-tarball.sh --version 0.12.0-alpha.1 --tarball path.tar.gz --signed

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib-release-version.sh
source "$SCRIPT_DIR/lib-release-version.sh"

MAX_BYTES=$((20 * 1024 * 1024))    # App Store archive ceiling: 20 MiB
MAX_INFO_BYTES=$((512 * 1024))     # App Store info.xml ceiling: 512 KiB
# Extensions that must never ship in the package (signing material). Note
# signature.json is JSON, not one of these, so it is allowed.
FORBIDDEN_EXT_RE='\.(key|pem|crt|csr|p12|pfx)$'

# Store metadata schema. apps.nextcloud.com runs info.xml through pre-info.xslt
# (which drops elements outside the base schema, e.g. the ExApp's
# <external-app><routes>) and THEN validates the result against info.xsd —
# validating info.xml against the XSD directly false-fails an ExApp manifest.
# Vendored under spec/appstore/ so this runs offline and reproducibly; override
# with APPSTORE_XSLT/APPSTORE_XSD, or fall back to downloading upstream.
STORE_XSLT_URL="https://raw.githubusercontent.com/nextcloud/appstore/master/nextcloudappstore/api/v1/release/pre-info.xslt"
STORE_XSD_URL="https://apps.nextcloud.com/schema/apps/info.xsd"
VENDORED_SCHEMA_DIR="$(cd "$SCRIPT_DIR/.." && pwd)/spec/appstore"

# resolve_schema <xslt|xsd> <download-dest> — echo a usable path for the store
# stylesheet/schema, trying: env override, vendored copy, then download.
resolve_schema() {
  local kind="$1" dest="$2" env_path vendored url
  case "$kind" in
    xslt) env_path="${APPSTORE_XSLT:-}"; vendored="$VENDORED_SCHEMA_DIR/pre-info.xslt"; url="$STORE_XSLT_URL" ;;
    xsd)  env_path="${APPSTORE_XSD:-}";  vendored="$VENDORED_SCHEMA_DIR/info.xsd";      url="$STORE_XSD_URL" ;;
    *) return 1 ;;
  esac
  if [[ -n "$env_path" && -f "$env_path" ]]; then printf '%s\n' "$env_path"; return 0; fi
  if [[ -f "$vendored" ]]; then printf '%s\n' "$vendored"; return 0; fi
  curl -fsSL "$url" -o "$dest" 2>/dev/null && printf '%s\n' "$dest"
}

usage() {
  cat >&2 <<'EOF'
Usage:
  ./scripts/validate-appstore-tarball.sh --version X.Y.Z[-pre] --tarball FILE [--signed] [--app-id ID]
EOF
}

main() {
  local version="" tarball="" signed=0 fail=0 app_id=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --version) version="${2:?--version needs a value}"; shift 2 ;;
      --tarball) tarball="${2:?--tarball needs a value}"; shift 2 ;;
      --signed)  signed=1; shift ;;
      --app-id)  app_id="${2:?--app-id needs a value}"; shift 2 ;;
      -h|--help) usage; return 0 ;;
      *) echo "error: unknown argument '$1'" >&2; usage; return 1 ;;
    esac
  done
  [[ -n "$version" ]] || { echo "error: --version is required" >&2; usage; return 1; }
  [[ -n "$tarball" ]] || { echo "error: --tarball is required" >&2; usage; return 1; }
  rv_validate "$version" || return 1
  [[ -f "$tarball" ]] || { echo "error: $tarball not found" >&2; return 1; }
  if [[ -z "$app_id" ]]; then
    app_id="$(rv_read_app_id "$(git rev-parse --show-toplevel)/appstore/info.xml")" || return 1
  fi
  # Talk hardcodes this id and Nextcloud's own ExApp owns it on the store; the
  # store package must never carry it.
  [[ "$app_id" != "live_transcription" ]] \
    || { echo "error: the store app id must not be live_transcription" >&2; return 1; }

  # bad <message> — record a failed check but keep going so one run reports
  # every problem at once.
  bad() { echo "FAIL: $1" >&2; fail=1; }

  local entries
  entries="$(tar -tzf "$tarball")" || { echo "error: cannot read $tarball" >&2; return 1; }

  # Single <id>/ root.
  local line
  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    if [[ "$line" != "$app_id"/* && "$line" != "$app_id" ]]; then
      bad "entry outside $app_id/ root: '$line'"
    fi
  done <<<"$entries"

  # No signing material.
  while IFS= read -r line; do
    if [[ "$line" =~ $FORBIDDEN_EXT_RE ]]; then
      bad "forbidden signing/secret file in archive: '$line'"
    fi
  done <<<"$entries"

  # Signature presence in signed mode.
  if [[ "$signed" -eq 1 ]] && ! grep -qxF "$app_id/appinfo/signature.json" <<<"$entries"; then
    bad "signed mode requested but $app_id/appinfo/signature.json is absent"
  fi

  # Size ceiling.
  local size
  size="$(wc -c <"$tarball")"
  if (( size > MAX_BYTES )); then
    bad "archive is ${size} bytes, over the ${MAX_BYTES}-byte (20 MiB) limit"
  fi

  # Manifest checks — extract just info.xml to a temp dir.
  local tmp info
  tmp="$(mktemp -d)"
  trap 'rm -rf "${tmp:-}"' EXIT
  info="$tmp/$app_id/appinfo/info.xml"
  if tar -xzf "$tarball" -C "$tmp" "$app_id/appinfo/info.xml" 2>/dev/null && [[ -f "$info" ]]; then
    if command -v xmllint >/dev/null 2>&1; then
      xmllint --noout "$info" 2>/dev/null || bad "$app_id/appinfo/info.xml is not well-formed"
    elif command -v python3 >/dev/null 2>&1; then
      python3 -c "import sys,xml.dom.minidom as m; m.parse(sys.argv[1])" "$info" 2>/dev/null \
        || bad "$app_id/appinfo/info.xml is not well-formed"
    fi
    local id ver tag
    id="$(sed -n 's|.*<id>\(.*\)</id>.*|\1|p' "$info" | head -n1)"
    ver="$(rv_read_version "$info" 2>/dev/null || true)"
    tag="$(rv_read_image_tag "$info" 2>/dev/null || true)"
    [[ "$id" == "$app_id" ]] || bad "<id> is '$id', expected '$app_id'"
    [[ "$ver" == "$version" ]] || bad "<version> is '$ver', expected '$version'"
    [[ "$tag" == "$version" ]] || bad "<image-tag> is '$tag', expected '$version'"

    # info.xml size ceiling (store limit).
    local info_bytes
    info_bytes="$(wc -c <"$info")"
    if (( info_bytes > MAX_INFO_BYTES )); then
      bad "appinfo/info.xml is ${info_bytes} bytes, over the ${MAX_INFO_BYTES}-byte (512 KiB) store limit"
    fi

    # Store-faithful metadata validation: pre-info.xslt then info.xsd (see the
    # header note). Needs xsltproc + xmllint; if either is missing we warn and
    # skip rather than fail, so a dev box without them can still run the other
    # checks — CI installs both so the store check always runs there.
    if command -v xsltproc >/dev/null 2>&1 && command -v xmllint >/dev/null 2>&1; then
      local xslt xsd
      if xslt="$(resolve_schema xslt "$tmp/pre-info.xslt")" && [[ -n "$xslt" ]] \
         && xsd="$(resolve_schema xsd "$tmp/info.xsd")" && [[ -n "$xsd" ]]; then
        if xsltproc "$xslt" "$info" >"$tmp/info.pre.xml" 2>/dev/null \
           && xmllint --noout --schema "$xsd" "$tmp/info.pre.xml" 2>"$tmp/xsd.err"; then
          : # store metadata is valid
        else
          bad "store metadata validation failed (pre-info.xslt -> info.xsd):"
          sed 's/^/    /' "$tmp/xsd.err" >&2 2>/dev/null || true
        fi
      else
        echo "warn: could not obtain the store schema (set APPSTORE_XSLT/APPSTORE_XSD or keep spec/appstore/) — skipping store-schema validation" >&2
      fi
    else
      echo "warn: xsltproc/xmllint not found — skipping store-schema validation" >&2
    fi
  else
    bad "$app_id/appinfo/info.xml is missing from the archive"
  fi

  if [[ "$fail" -eq 0 ]]; then
    echo "OK: $tarball is a valid $app_id $version App Store archive$([[ "$signed" -eq 1 ]] && echo ' (signed)')."
    return 0
  fi
  echo "error: $tarball failed App Store validation" >&2
  return 1
}

main "$@"
