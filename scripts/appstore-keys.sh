#!/usr/bin/env bash
# appstore-keys.sh — the two one-time openssl steps for App Store registration.
#
# Nextcloud signs every store app with a certificate it issues for that app id.
# Run these on a trusted machine, never in CI, and keep the key out of the repo
# (.gitignore refuses *.key, *.crt and *.csr; the validator refuses them in a
# package). See docs/appstore-checklist.md for the order.
#
#   # 1. A private key and a certificate signing request for the app id. Send
#   #    the .csr (never the .key) in a PR to nextcloud/app-certificate-requests.
#   ./scripts/appstore-keys.sh csr ~/secure/cassini_live
#
#   # 2. Once the signed .crt is back: the signature of the app id that the
#   #    "Register app" form on apps.nextcloud.com asks for.
#   ./scripts/appstore-keys.sh register-signature ~/secure/cassini_live/cassini_live.key
#
# The app id defaults to appstore/info.xml's <id>; override with APP_ID.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib-release-version.sh
source "$SCRIPT_DIR/lib-release-version.sh"

die() { echo "error: $*" >&2; exit 1; }

usage() {
  cat >&2 <<'USAGE'
Usage:
  ./scripts/appstore-keys.sh csr DIR                  # DIR/<id>.key + DIR/<id>.csr
  ./scripts/appstore-keys.sh register-signature KEY   # base64 SHA-512 signature of <id>
USAGE
}

main() {
  local cmd="${1:-}" app_id root
  root="$(cd "$SCRIPT_DIR/.." && pwd)"
  app_id="${APP_ID:-$(rv_read_app_id "$root/appstore/info.xml")}"
  case "$cmd" in
    csr)
      local dir="${2:?usage: $0 csr DIR}"
      dir="$(realpath -m "$dir")"
      case "$dir/" in
        "$root"/*) die "$dir is inside the repository; keep the key outside it" ;;
      esac
      mkdir -p "$dir"
      [[ -e "$dir/$app_id.key" ]] && die "$dir/$app_id.key already exists; refusing to replace a key"
      ( umask 077
        openssl req -nodes -newkey rsa:4096 -keyout "$dir/$app_id.key" \
          -out "$dir/$app_id.csr" -subj "/CN=$app_id" )
      echo "Wrote $dir/$app_id.key (keep it secret) and $dir/$app_id.csr (send this one)."
      ;;
    register-signature)
      local key="${2:?usage: $0 register-signature KEY}"
      [[ -f "$key" ]] || die "$key not found"
      printf '%s' "$app_id" | openssl dgst -sha512 -sign "$key" | openssl base64 -A
      echo
      ;;
    -h|--help|help|"") usage ;;
    *) usage; exit 1 ;;
  esac
}

main "$@"
