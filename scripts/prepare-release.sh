#!/usr/bin/env bash
# prepare-release.sh — turn a version decision into a local release commit+tag.
#
# One command bumps the version (rv_write_release_version), folds changelog.d/ into CHANGELOG.md,
# extracts the release notes, commits as `release: <version>`, and tags
# `v<version>` — all LOCALLY. It never pushes: the maintainer inspects the diff
# and pushes the branch + tag intentionally (pushing the tag is what triggers
# publish-exapp-image.yml to build the release images).
#
#   ./scripts/prepare-release.sh --bump minor
#   ./scripts/prepare-release.sh --promote rc.1
#   ./scripts/prepare-release.sh --version 0.3.0-beta.1
#
# It cuts only on the version's branch (docs/release.md#branches): main for
# X.Y.0-alpha.N, release/X.Y for the rest of the X.Y line. It fetches origin's
# tags first and refuses a branch that is behind origin.
#
# Options:
#   --bump patch|minor|major       compute the next version from info.xml
#   --promote alpha|beta|rc.1|rc.2|stable advance along the prerelease ladder
#   --version X.Y.Z[-pre]          set an explicit target version
#   --date YYYY-MM-DD              changelog date (default: today)
#   --allow-empty-changelog        release even with no changelog.d fragments
#   --no-tag                       make the commit but skip creating the tag

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib-release-version.sh
source "$SCRIPT_DIR/lib-release-version.sh"

usage() {
  cat >&2 <<'EOF'
Usage:
  ./scripts/prepare-release.sh [--push]                       # guided: preview + pick
  ./scripts/prepare-release.sh (--bump L | --promote T | --version V) [--date D]
                               [--allow-empty-changelog] [--no-tag] [--push]

With no --bump/--promote/--version, runs guided: shows the pending changes and
the candidate versions, then asks you to choose.

--push pushes the release commit + tag to origin after creating them. The tag
push triggers the image build and the release workflow, so this one command is
the whole release trigger.
EOF
}

die() { echo "error: $*" >&2; exit 1; }

main() {
  local mode="" arg="" date="" allow_empty=0 make_tag=1 do_push=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --bump)                  mode="bump";    arg="${2:?--bump needs a level}"; shift 2 ;;
      --promote)               mode="promote"; arg="${2:?--promote needs a target}"; shift 2 ;;
      --version)               mode="version"; arg="${2:?--version needs a value}"; shift 2 ;;
      --date)                  date="${2:?--date needs a value}"; shift 2 ;;
      --allow-empty-changelog) allow_empty=1; shift ;;
      --no-tag)                make_tag=0; shift ;;
      --push)                  do_push=1; shift ;;
      -h|--help)               usage; return 0 ;;
      *) echo "error: unknown argument '$1'" >&2; usage; return 1 ;;
    esac
  done
  [[ "$do_push" -eq 1 && "$make_tag" -eq 0 ]] \
    && die "--push with --no-tag would push a release commit with no tag to trigger the release; drop one"
  date="${date:-$(date +%F)}"

  local root info_xml current target interactive=0
  root="$(git rev-parse --show-toplevel)"
  info_xml="$root/appinfo/info.xml"
  current="$(rv_read_version "$info_xml")" || return 1

  # No --bump/--promote/--version given → guided mode: show the preview
  # (pending changes + candidates) and ask. Reads from stdin, so it also works
  # with piped input; on EOF it aborts.
  if [[ -z "$mode" ]]; then
    interactive=1
    RELEASE_PREVIEW_NO_HINT=1 "$SCRIPT_DIR/release-preview.sh" || true
    echo
    local choice pretype
    pretype="$(rv_parse "$current" | awk '{print $4}')"
    if [[ -z "$pretype" ]]; then
      read -r -p "Choose a bump [patch/minor/major] (or a version, q to quit): " choice \
        || { echo; die "no choice given"; }
    else
      read -r -p "Advance [alpha/beta/rc.1/rc.2/stable] (or a version, q to quit): " choice \
        || { echo; die "no choice given"; }
    fi
    case "$choice" in
      q|Q|quit|"")            echo "Aborted — nothing changed."; return 0 ;;
      patch|minor|major)      mode="bump"; arg="$choice" ;;
      alpha|beta|rc.1|rc.2|stable)  mode="promote"; arg="$choice" ;;
      *)
        if rv_validate "$choice" >/dev/null 2>&1; then mode="version"; arg="$choice"
        else die "invalid choice: '$choice'"; fi
        ;;
    esac
  fi

  # --- Resolve the target version -----------------------------------------
  case "$mode" in
    bump)    target="$(rv_bump "$arg" "$current")" || return 1 ;;
    promote) target="$(rv_promote "$arg" "$current")" || return 1 ;;
    version) rv_validate "$arg" || return 1; target="$arg" ;;
  esac
  echo "Preparing release: $current -> $target"

  # In guided mode, confirm before doing anything (the commit/tag, and the push).
  if [[ "$interactive" -eq 1 ]]; then
    local prompt="Cut v$target"
    [[ "$do_push" -eq 1 ]] && prompt="$prompt and push it (triggers the image build + release)"
    local go
    read -r -p "$prompt? [y/N] " go || true
    [[ "$go" == y || "$go" == Y ]] || { echo "Aborted — nothing changed."; return 0; }
  fi

  # --- Pre-flight checks (before mutating anything) -----------------------
  # Clean tracked worktree; untracked files are fine (they are not committed).
  git -C "$root" diff --quiet || die "worktree has unstaged changes; commit or stash first"
  git -C "$root" diff --cached --quiet || die "index has staged changes; commit or reset first"

  # info.xml well-formed (best effort — skip silently if no parser available).
  if command -v xmllint >/dev/null 2>&1; then
    xmllint --noout "$info_xml" || die "appinfo/info.xml is not well-formed"
  elif command -v python3 >/dev/null 2>&1; then
    python3 -c "import sys,xml.dom.minidom as m; m.parse(sys.argv[1])" "$info_xml" \
      || die "appinfo/info.xml is not well-formed"
  fi

  local branch want
  branch="$(git -C "$root" symbolic-ref --quiet --short HEAD)" \
    || die "HEAD is detached; check out the branch v$target is cut on first"

  # Fetch origin first: the checks below must see every tag, including one cut
  # in parallel elsewhere, and the branch must not be behind origin's.
  if git -C "$root" remote get-url origin >/dev/null 2>&1; then
    git -C "$root" fetch --quiet --tags origin \
      || die "could not fetch tags from origin; the version check needs them"
    if git -C "$root" rev-parse -q --verify "refs/remotes/origin/$branch" >/dev/null \
       && ! git -C "$root" merge-base --is-ancestor "origin/$branch" HEAD; then
      die "$branch is behind origin/$branch; pull it first"
    fi
  else
    echo "warning: no origin remote; checking the version against local tags only" >&2
  fi

  # The latest release tag in the target's own X.Y line. Lines move
  # independently: 0.2.2 ships after 0.3.0-alpha.1.
  local latest="" t v
  while IFS= read -r t; do
    [[ -n "$t" ]] || continue
    v="${t#v}"
    rv_validate "$v" >/dev/null 2>&1 || continue
    rv_same_line "$v" "$target" || continue
    if [[ -z "$latest" ]] || rv_gt "$v" "$latest"; then latest="$v"; fi
  done < <(git -C "$root" tag --list 'v*')

  # Cut on the version's branch (docs/release.md#branches).
  want="$(rv_release_branch "$target")"
  if [[ "$branch" != "$want" ]]; then
    if [[ "$want" == release/* ]] \
       && ! git -C "$root" rev-parse -q --verify "refs/heads/$want" >/dev/null \
       && ! git -C "$root" rev-parse -q --verify "refs/remotes/origin/$want" >/dev/null; then
      # A line that has shipped is branched from its last release, so a patch
      # carries only cherry-picked fixes; one that hasn't, from main at its
      # first beta.
      if [[ -n "$latest" && "$(rv_parse "$latest" | awk '{print $4}')" == "" ]]; then
        die "v$target is cut on $want, which doesn't exist yet; branch it from the line's last release: git switch -c $want v$latest"
      fi
      die "v$target is cut on $want, which doesn't exist yet; branch it from main at the first beta: git switch -c $want main"
    fi
    die "v$target is cut on $want, not $branch; git switch $want (see docs/release.md#branches)"
  fi

  # Tag must not already exist.
  git -C "$root" rev-parse -q --verify "refs/tags/v$target" >/dev/null 2>&1 \
    && die "tag v$target already exists"

  # Target must be greater than the latest release tag in its line, and than
  # the branch's own version: main carries the version it is developing, above
  # everything shipped, so this also stops an alpha on main going backwards.
  if [[ -n "$latest" ]] && ! rv_gt "$target" "$latest"; then
    die "target $target is not greater than v$latest, the latest release tag in its line"
  fi
  if ! rv_gt "$target" "$current"; then
    die "target $target is not greater than $branch's version $current (appinfo/info.xml)"
  fi

  # Fragment presence (unless explicitly allowed to be empty).
  local frag frag_count=0
  for frag in "$root"/changelog.d/*.md; do
    [[ -e "$frag" ]] || continue
    [[ "$(basename "$frag")" == "README.md" ]] && continue
    frag_count=$((frag_count + 1))
  done
  if [[ "$frag_count" -eq 0 && "$allow_empty" -eq 0 ]]; then
    die "no changelog.d fragments; add one or pass --allow-empty-changelog"
  fi

  # --- Mutate: version, changelog, release notes --------------------------
  rv_write_release_version "$root" "$target" || die "failed to update the version files"

  local notes_dir="$root/build/release" notes_file
  notes_file="$notes_dir/cassini-live-$target-notes.md"
  mkdir -p "$notes_dir"

  local staged=()
  mapfile -t staged < <(rv_release_files "$root")
  if [[ "$frag_count" -gt 0 ]]; then
    "$SCRIPT_DIR/fold-changelog.sh" --version "$target" --date "$date" --write \
      || die "changelog fold failed"
    "$SCRIPT_DIR/extract-release-notes.sh" --version "$target" >"$notes_file" \
      || die "could not extract release notes for $target"
    staged+=("$root/CHANGELOG.md" "$root/changelog.d")
  else
    printf 'Release %s.\n' "$target" >"$notes_file"
    echo "note: --allow-empty-changelog set; CHANGELOG.md not modified"
  fi

  # --- Commit and tag (local only) ----------------------------------------
  git -C "$root" add -- "${staged[@]}"
  git -C "$root" commit -q -m "release: $target"
  echo "Created commit: release: $target"
  if [[ "$make_tag" -eq 1 ]]; then
    git -C "$root" tag "v$target"
    echo "Created tag:    v$target"
  fi

  local push_ref
  push_ref="$branch"
  [[ "$make_tag" -eq 1 ]] && push_ref="$branch v$target"

  echo
  echo "Release notes: ${notes_file#"$root"/}"
  if [[ "$do_push" -eq 1 ]]; then
    echo "Pushing to origin: $push_ref"
    # --atomic: if the branch is rejected (someone pushed meanwhile), the tag
    # must not land either — the tag alone would publish the release images.
    # shellcheck disable=SC2086  # push_ref is intentionally branch + tag (two args).
    git -C "$root" push --atomic origin $push_ref
    echo "Pushed. The tag push triggers the image build and the release workflow."
  else
    cat <<EOF

Nothing has been pushed. Review the commit, then push intentionally:
  git show
  git push --atomic origin ${push_ref}
EOF
  fi
  # The sync works from the tag, so an untagged cut has nothing to bring back.
  if [[ "$branch" == release/* && "$make_tag" -eq 1 ]]; then
    cat <<EOF

Then bring this release back to main (its changelog, and main's version):
  git switch -c chore/sync-${target}-to-main origin/main
  ./scripts/sync-release-to-main.sh --version ${target}
EOF
  fi
}

main "$@"
