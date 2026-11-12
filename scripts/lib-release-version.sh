# shellcheck shell=bash
#
# Shared helpers for the Cassini Live release version ladder. Ported from
# codemyriad/gocassini's scripts/lib-release-version.sh; the ladder, branch
# rule and comparison are unchanged.
#
# The release version is read from appinfo/info.xml <version>, and the AppAPI
# Docker <image-tag> must stay equal to it. Unlike gocassini, Cassini Live
# keeps the version in more than one file (scripts/check_manifest.py asserts
# they agree), so rv_write_release_version rewrites all of them in lockstep:
#   appinfo/info.xml       <version> + <image-tag>  (native live_transcription manifest)
#   appstore/info.xml      <version> + <image-tag>  (App Store manifest)
#   pyproject.toml         [project] version
#   cassini_live/__init__.py  __version__
#   uv.lock                the cassini-live package's version (PEP 440 form)
#
# Version grammar is stricter than the App Store info.xsd `semver` type on
# purpose: the ladder only recognizes stable X.Y.Z and alpha/beta/rc
# prereleases, so anything else (arbitrary suffixes, build metadata) is a bug
# we want to catch, not ship.
#
# Deliberately sed-based (same idiom as scripts/bump-exapp-version.sh and
# harness/bin/lib-exapp-image.sh): xmllint is not guaranteed to exist in dev
# shells.
#
# Meant to be sourced; defines functions only, no side effects.

# X.Y.Z with an optional -(alpha|beta|rc).N prerelease. Capture groups:
#   1=major 2=minor 3=patch 5=prerelease-type 6=prerelease-number
RV_VERSION_RE='^([0-9]+)\.([0-9]+)\.([0-9]+)(-(alpha|beta|rc)\.([0-9]+))?$'

# rv_validate <version>
# Return 0 when <version> is a well-formed release version, else print why to
# stderr and return 1.
rv_validate() {
  local v="$1"
  if [[ "$v" == *+* ]]; then
    echo "error: build metadata (+...) is not allowed in release versions: '$v'" >&2
    return 1
  fi
  if [[ ! "$v" =~ $RV_VERSION_RE ]]; then
    echo "error: version must be X.Y.Z or X.Y.Z-(alpha|beta|rc).N, got: '$v'" >&2
    return 1
  fi
}

# rv_parse <version>
# Echo "MAJOR MINOR PATCH PRETYPE PRENUM". PRETYPE/PRENUM are empty for a
# stable version. Validates first.
rv_parse() {
  local v="$1"
  rv_validate "$v" || return 1
  [[ "$v" =~ $RV_VERSION_RE ]]  # repopulate BASH_REMATCH
  printf '%s %s %s %s %s\n' \
    "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}" "${BASH_REMATCH[3]}" \
    "${BASH_REMATCH[5]}" "${BASH_REMATCH[6]}"
}

# rv_bump <patch|minor|major> <version>
# Echo the next version. A minor or major bump starts the prerelease train at
# -alpha.1, whether the current version is stable or a prerelease, so the base
# can be re-targeted mid-cycle. A patch skips the ladder (it carries fixes, no
# features to test) and only follows a stable release:
#   patch:  A.B.C       -> A.B.(C+1)
#   minor:  A.B.C[-pre] -> A.(B+1).0-alpha.1
#   major:  A.B.C[-pre] -> (A+1).0.0-alpha.1
rv_bump() {
  local level="$1" v="$2" parsed major minor patch pretype
  parsed="$(rv_parse "$v")" || return 1
  read -r major minor patch pretype _ <<<"$parsed"
  case "$level" in
    patch)
      [[ -z "$pretype" ]] || { echo "error: a patch follows a stable release; finish $v first (--promote stable)" >&2; return 1; }
      printf '%s.%s.%s\n' "$major" "$minor" "$((patch + 1))"
      return ;;
    minor) minor=$((minor + 1)); patch=0 ;;
    major) major=$((major + 1)); minor=0; patch=0 ;;
    *) echo "error: bump level must be patch|minor|major, got: '$level'" >&2; return 1 ;;
  esac
  printf '%s.%s.%s-alpha.1\n' "$major" "$minor" "$patch"
}

# rv_release_branch <version>
# Echo the branch <version> is cut on (docs/release.md#branches): main for the
# next release's X.Y.0-alpha.N, release/X.Y for the rest of the X.Y line (its
# betas, rcs and X.Y.0, and every X.Y.Z patch).
rv_release_branch() {
  local parsed major minor patch pretype
  parsed="$(rv_parse "$1")" || return 1
  read -r major minor patch pretype _ <<<"$parsed"
  if [[ "$patch" == 0 && "$pretype" == alpha ]]; then
    echo main
  else
    printf 'release/%s.%s\n' "$major" "$minor"
  fi
}

# rv_previous_in_line <version>
# Read versions on stdin, one per line (anything not a release version is
# skipped), and echo the newest one in <version>'s X.Y line that is lower than
# <version>. Echo nothing when there is none.
rv_previous_in_line() {
  local target="$1" v best=""
  rv_validate "$target" || return 1
  while IFS= read -r v; do
    rv_validate "$v" >/dev/null 2>&1 || continue
    rv_same_line "$v" "$target" || continue
    rv_gt "$target" "$v" || continue
    if [[ -z "$best" ]] || rv_gt "$v" "$best"; then best="$v"; fi
  done
  [[ -z "$best" ]] || printf '%s\n' "$best"
}

# rv_same_line <a> <b>: return 0 (true) when a and b share MAJOR.MINOR.
rv_same_line() {
  local pa pb amaj amin bmaj bmin
  pa="$(rv_parse "$1")" || return 1
  pb="$(rv_parse "$2")" || return 1
  read -r amaj amin _ _ _ <<<"$pa"
  read -r bmaj bmin _ _ _ <<<"$pb"
  [[ "$amaj" == "$bmaj" && "$amin" == "$bmin" ]]
}

# rv_promote <alpha|beta|rc.1|rc.2|stable> <version>
# Echo the next version along the Nextcloud prerelease ladder. The base X.Y.Z
# never changes:
#   promote alpha  : alpha.N -> alpha.(N+1)  (alpha.0 -> alpha.1 cuts the first
#                    alpha of the version main is developing; see
#                    docs/release.md#mains-version)
#   promote beta   : alpha.N -> beta.1
#   promote rc.1   : beta.N  -> rc.1
#   promote rc.2   : rc.1    -> rc.2
#   promote stable : rc.N    -> X.Y.Z   (drops the suffix)
# Any other source state is rejected as an ambiguous promotion. Stable does not
# require rc.2 first — rc.1 -> X.Y.Z is allowed when a cycle needs only one
# candidate.
rv_promote() {
  local target="$1" v="$2" parsed major minor patch pretype prenum base
  parsed="$(rv_parse "$v")" || return 1
  read -r major minor patch pretype prenum <<<"$parsed"
  base="${major}.${minor}.${patch}"
  case "$target" in
    alpha)
      [[ "$pretype" == alpha ]] || { echo "error: 'promote alpha' requires an alpha.N version, got: '$v'" >&2; return 1; }
      printf '%s-alpha.%s\n' "$base" "$((prenum + 1))" ;;
    beta)
      [[ "$pretype" == alpha ]] || { echo "error: 'promote beta' requires an alpha.N version, got: '$v'" >&2; return 1; }
      printf '%s-beta.1\n' "$base" ;;
    rc.1)
      [[ "$pretype" == beta ]] || { echo "error: 'promote rc.1' requires a beta.N version, got: '$v'" >&2; return 1; }
      printf '%s-rc.1\n' "$base" ;;
    rc.2)
      [[ "$pretype" == rc && "$prenum" == 1 ]] || { echo "error: 'promote rc.2' requires an rc.1 version, got: '$v'" >&2; return 1; }
      printf '%s-rc.2\n' "$base" ;;
    stable)
      [[ "$pretype" == rc ]] || { echo "error: 'promote stable' requires an rc.N version, got: '$v'" >&2; return 1; }
      printf '%s\n' "$base" ;;
    *)
      echo "error: promote target must be alpha|beta|rc.1|rc.2|stable, got: '$target'" >&2; return 1 ;;
  esac
}

# rv_pretype_rank <alpha|beta|rc>
# Echo the precedence rank of a prerelease type (alpha < beta < rc).
rv_pretype_rank() {
  case "$1" in
    alpha) echo 0 ;;
    beta)  echo 1 ;;
    rc)    echo 2 ;;
    *)     echo 9 ;;
  esac
}

# rv_compare <a> <b>
# Echo -1 if a<b, 0 if equal, 1 if a>b, by semver precedence. A stable version
# outranks the same base prerelease (1.0.0 > 1.0.0-rc.1); among prereleases
# alpha<beta<rc, then the numeric counter.
rv_compare() {
  local a="$1" b="$2" pa pb
  local amaj amin apat atype anum bmaj bmin bpat btype bnum ra rb
  pa="$(rv_parse "$a")" || return 1
  pb="$(rv_parse "$b")" || return 1
  read -r amaj amin apat atype anum <<<"$pa"
  read -r bmaj bmin bpat btype bnum <<<"$pb"
  if (( amaj != bmaj )); then (( amaj > bmaj )) && echo 1 || echo -1; return; fi
  if (( amin != bmin )); then (( amin > bmin )) && echo 1 || echo -1; return; fi
  if (( apat != bpat )); then (( apat > bpat )) && echo 1 || echo -1; return; fi
  # Base X.Y.Z equal — a missing prerelease type ranks highest (it is the
  # final release of that base).
  if [[ -z "$atype" && -z "$btype" ]]; then echo 0; return; fi
  if [[ -z "$atype" ]]; then echo 1; return; fi
  if [[ -z "$btype" ]]; then echo -1; return; fi
  ra="$(rv_pretype_rank "$atype")"; rb="$(rv_pretype_rank "$btype")"
  if (( ra != rb )); then (( ra > rb )) && echo 1 || echo -1; return; fi
  if (( anum != bnum )); then (( anum > bnum )) && echo 1 || echo -1; return; fi
  echo 0
}

# rv_gt <a> <b>: return 0 (true) when a is strictly greater than b.
rv_gt() {
  [[ "$(rv_compare "$1" "$2")" == 1 ]]
}

# rv_read_version <info.xml>
# Echo the <version> pinned in the manifest, or fail with a message.
rv_read_version() {
  local info_xml="$1" v
  v="$(sed -n 's|.*<version>\(.*\)</version>.*|\1|p' "$info_xml" | head -n1)"
  if [[ -z "$v" ]]; then
    echo "error: could not extract <version> from $info_xml" >&2
    return 1
  fi
  printf '%s\n' "$v"
}

# rv_read_image_tag <info.xml>
# Echo the <image-tag> pinned in the manifest, or fail with a message.
rv_read_image_tag() {
  local info_xml="$1" tag
  tag="$(sed -n 's|.*<image-tag>\(.*\)</image-tag>.*|\1|p' "$info_xml" | head -n1)"
  if [[ -z "$tag" ]]; then
    echo "error: could not extract <image-tag> from $info_xml" >&2
    return 1
  fi
  printf '%s\n' "$tag"
}

# rv_read_app_id <info.xml>
# Echo the manifest's <id>, or fail with a message.
rv_read_app_id() {
  local info_xml="$1" id
  id="$(sed -n 's|.*<id>\(.*\)</id>.*|\1|p' "$info_xml" | head -n1)"
  if [[ -z "$id" ]]; then
    echo "error: could not extract <id> from $info_xml" >&2
    return 1
  fi
  printf '%s\n' "$id"
}

# rv_write_version <info.xml> <new-version>
# Rewrite <version> and <image-tag> in lockstep and verify each landed exactly
# once. Fails if the manifest's two fields already disagree — that is a broken
# state to fix by hand, not to bump over.
rv_write_version() {
  local info_xml="$1" new="$2" cur_version cur_tag marker count
  rv_validate "$new" || return 1
  cur_version="$(rv_read_version "$info_xml")" || return 1
  cur_tag="$(rv_read_image_tag "$info_xml")" || return 1
  if [[ "$cur_version" != "$cur_tag" ]]; then
    echo "error: <version> ($cur_version) and <image-tag> ($cur_tag) disagree in $info_xml; fix the manifest before bumping" >&2
    return 1
  fi
  sed -i \
    -e "s|<version>${cur_version}</version>|<version>${new}</version>|" \
    -e "s|<image-tag>${cur_tag}</image-tag>|<image-tag>${new}</image-tag>|" \
    "$info_xml"
  for marker in "<version>${new}</version>" "<image-tag>${new}</image-tag>"; do
    count="$(grep -cF "$marker" "$info_xml")"
    if [[ "$count" -ne 1 ]]; then
      echo "error: expected exactly one '$marker' in $info_xml after rewrite, found $count" >&2
      return 1
    fi
  done
}

# rv_pep440 <version>
# Echo the PEP 440 normal form uv writes into uv.lock: 0.12.0-alpha.1 ->
# 0.12.0a1, -beta.N -> bN, -rc.N -> rcN. A stable version is unchanged.
rv_pep440() {
  local parsed major minor patch pretype prenum
  parsed="$(rv_parse "$1")" || return 1
  read -r major minor patch pretype prenum <<<"$parsed"
  case "$pretype" in
    "")    printf '%s.%s.%s\n' "$major" "$minor" "$patch" ;;
    alpha) printf '%s.%s.%sa%s\n' "$major" "$minor" "$patch" "$prenum" ;;
    beta)  printf '%s.%s.%sb%s\n' "$major" "$minor" "$patch" "$prenum" ;;
    rc)    printf '%s.%s.%src%s\n' "$major" "$minor" "$patch" "$prenum" ;;
  esac
}

# rv_release_files <root>
# Echo, one per line, the files rv_write_release_version rewrites that exist
# under <root> (test fixtures may carry only some of them).
rv_release_files() {
  local root="$1" f
  for f in appinfo/info.xml appstore/info.xml pyproject.toml cassini_live/__init__.py uv.lock; do
    [[ -f "$root/$f" ]] && printf '%s\n' "$root/$f"
  done
  return 0
}

# _rv_sub_once <file> <sed-expr> <marker>
# Apply <sed-expr> to <file>, then require <marker> to occur exactly once.
_rv_sub_once() {
  local file="$1" expr="$2" marker="$3" count
  sed -i -e "$expr" "$file"
  count="$(grep -cF -- "$marker" "$file")"
  if [[ "$count" -ne 1 ]]; then
    echo "error: expected exactly one '$marker' in $file after rewrite, found $count" >&2
    return 1
  fi
}

# rv_write_release_version <root> <new-version>
# Rewrite every version-carrying file under <root> to <new-version>. Each
# manifest keeps <version> and <image-tag> in lockstep (rv_write_version), and
# the Python files must already agree with appinfo/info.xml: a disagreement is
# a broken state to fix by hand, not to bump over.
rv_write_release_version() {
  local root="$1" new="$2" current file pep
  rv_validate "$new" || return 1
  current="$(rv_read_version "$root/appinfo/info.xml")" || return 1
  pep="$(rv_pep440 "$new")" || return 1
  if [[ -f "$root/pyproject.toml" ]] \
     && ! grep -qxF "version = \"$current\"" "$root/pyproject.toml"; then
    echo "error: pyproject.toml does not carry version \"$current\" (appinfo/info.xml); fix it before bumping" >&2
    return 1
  fi
  if [[ -f "$root/cassini_live/__init__.py" ]] \
     && ! grep -qxF "__version__ = \"$current\"" "$root/cassini_live/__init__.py"; then
    echo "error: cassini_live/__init__.py does not carry __version__ \"$current\"; fix it before bumping" >&2
    return 1
  fi
  for file in "$root/appinfo/info.xml" "$root/appstore/info.xml"; do
    [[ -f "$file" ]] || continue
    rv_write_version "$file" "$new" || return 1
  done
  if [[ -f "$root/pyproject.toml" ]]; then
    # The first `version = ` line is [project]'s.
    _rv_sub_once "$root/pyproject.toml" \
      "0,/^version = \".*\"\$/s//version = \"$new\"/" "version = \"$new\"" || return 1
  fi
  if [[ -f "$root/cassini_live/__init__.py" ]]; then
    _rv_sub_once "$root/cassini_live/__init__.py" \
      "s/^__version__ = \".*\"\$/__version__ = \"$new\"/" "__version__ = \"$new\"" || return 1
  fi
  if [[ -f "$root/uv.lock" ]]; then
    # Only the line right after cassini-live's own `name =`; dependencies keep theirs.
    sed -i -e "/^name = \"cassini-live\"\$/{n;s/^version = \".*\"\$/version = \"$pep\"/}" "$root/uv.lock"
    if ! grep -A1 -xF 'name = "cassini-live"' "$root/uv.lock" | grep -qxF "version = \"$pep\""; then
      echo "error: could not set cassini-live's version to $pep in uv.lock" >&2
      return 1
    fi
  fi
}

# rv_info_xml
# Echo the path to appinfo/info.xml at the repo root.
rv_info_xml() {
  printf '%s/appinfo/info.xml\n' "$(git rev-parse --show-toplevel)"
}
