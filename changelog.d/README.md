# Changelog Fragments

Cassini Live uses changelog fragments (the same convention as Cassini,
codemyriad/gocassini) to keep agent-heavy pull requests from
conflicting on `CHANGELOG.md`.

Add one fragment for every pull request that changes user-visible behavior,
installation or release behavior, app-store metadata, security posture, or
administrator documentation. Skip fragments for pure refactors, tests-only changes,
typo fixes, and internal planning notes.

## File Names

Use a short, stable name:

```text
changelog.d/<issue-or-pr>.<slug>.md
```

Examples:

```text
changelog.d/109.public-release.md
changelog.d/d-398.signing-certificate.md
```

## Format

Use Keep a Changelog-style headings. Include only headings that have entries.
The folder accepts exactly these six, and emits them in this order:

```markdown
### Added
- Added a public security policy.

### Changed
- Clarified the ExApp installation flow.

### Deprecated
- Deprecated a legacy deploy option.

### Removed
- Removed the unused nightly image alias.

### Fixed
- Fixed AppAPI route metadata for the transcript panel.

### Security
- Marked local harness credentials as development-only values.
```

Every non-blank line must sit under one of those headings. Text before the
first heading, an unrecognized heading (e.g. `### Improved`), or a heading with
no bullets makes `fold-changelog.sh` fail — so a malformed fragment is caught
at release time rather than silently dropped.

Good fragments are short and user/operator-facing. Do not list every file
changed, implementation step, test helper, or agent action.

## Release Process

Fragments are folded into `CHANGELOG.md` by `scripts/fold-changelog.sh`, not by
hand:

```bash
# Validate every fragment and preview the section (changes nothing):
./scripts/fold-changelog.sh --version 0.12.0-alpha.1 --check

# Fold fragments into CHANGELOG.md under the new version and delete them:
./scripts/fold-changelog.sh --version 0.12.0-alpha.1 --write
```

The script groups entries from every fragment under the canonical headings,
inserts a `## [<version>] - <date>` section right after `## [Unreleased]`
(defaulting the date to today), and removes the consumed fragments on `--write`.
`README.md` is never treated as a fragment. `scripts/prepare-release.sh` calls
this as part of the full release flow.

A release cut on a `release/X.Y` branch consumes the fragments cherry-picked
there, while main keeps its copies. `scripts/sync-release-to-main.sh
--version X.Y.Z`, run on a branch from main, copies that release's section into
main's `CHANGELOG.md` and deletes those fragments, so they aren't released twice.
