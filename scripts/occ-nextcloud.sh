#!/usr/bin/env bash
# occ-nextcloud.sh — an OCC provider for `build-appstore-tarball.sh --sign-app`.
#
# `occ integrity:sign-app` needs a bootable Nextcloud. This runs it inside a
# throwaway `nextcloud:stable` container (sqlite auto-install, ~10s) and copies
# the app + key/cert in and signature.json back out with `docker cp`, so there
# are no bind-mount uid/permission issues. Use it as:
#
#   OCC="./scripts/occ-nextcloud.sh" \
#     ./scripts/build-appstore-tarball.sh --version X.Y.Z --sign-app
#
# The build script invokes it as:
#   occ-nextcloud.sh integrity:sign-app --privateKey=K --certificate=C --path=DIR
# and expects DIR/appinfo/signature.json to exist afterwards.
#
# NEXTCLOUD_IMAGE overrides the image (default nextcloud:stable).

set -euo pipefail

SUB="${1:?usage: $0 integrity:sign-app --privateKey=K --certificate=C --path=DIR}"
[[ "$SUB" == "integrity:sign-app" ]] \
  || { echo "occ-nextcloud.sh: only 'integrity:sign-app' is supported, got '$SUB'" >&2; exit 2; }
shift

KEY="" CRT="" APPDIR=""
for arg in "$@"; do
  case "$arg" in
    --privateKey=*)  KEY="${arg#--privateKey=}" ;;
    --certificate=*) CRT="${arg#--certificate=}" ;;
    --path=*)        APPDIR="${arg#--path=}" ;;
  esac
done
[[ -f "$KEY" ]]    || { echo "occ-nextcloud.sh: --privateKey file not found: $KEY" >&2; exit 2; }
[[ -f "$CRT" ]]    || { echo "occ-nextcloud.sh: --certificate file not found: $CRT" >&2; exit 2; }
[[ -d "$APPDIR" ]] || { echo "occ-nextcloud.sh: --path dir not found: $APPDIR" >&2; exit 2; }

IMAGE="${NEXTCLOUD_IMAGE:-nextcloud:stable}"

cid="$(docker run -d \
  -e SQLITE_DATABASE=nc -e NEXTCLOUD_ADMIN_USER=admin -e NEXTCLOUD_ADMIN_PASSWORD=admin \
  "$IMAGE")"
trap 'docker rm -f "$cid" >/dev/null 2>&1 || true' EXIT

echo "occ-nextcloud.sh: waiting for $IMAGE to install..."
installed=0
for _ in $(seq 1 80); do
  if docker exec -u www-data "$cid" php occ status 2>/dev/null | grep -q 'installed: true'; then
    installed=1; break
  fi
  sleep 5
done
[[ "$installed" -eq 1 ]] || { echo "occ-nextcloud.sh: Nextcloud did not install in time" >&2; exit 1; }

# Copy app + signing material in, hand ownership to www-data (occ refuses to run
# as root and must be able to write signature.json), sign, copy the result back.
docker cp "$APPDIR" "$cid:/tmp/app" >/dev/null
docker cp "$KEY"    "$cid:/tmp/app.key" >/dev/null
docker cp "$CRT"    "$cid:/tmp/app.crt" >/dev/null
docker exec -u root "$cid" chown -R www-data:www-data /tmp/app /tmp/app.key /tmp/app.crt
docker exec -u www-data "$cid" php occ integrity:sign-app \
  --privateKey=/tmp/app.key --certificate=/tmp/app.crt --path=/tmp/app
docker cp "$cid:/tmp/app/appinfo/signature.json" "$APPDIR/appinfo/signature.json" >/dev/null
