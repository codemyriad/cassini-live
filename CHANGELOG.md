# Changelog

All notable user-facing changes to Cassini Live will be documented in this file.

This project follows semantic versioning for Nextcloud App Store releases. The
version in both manifests (`appinfo/info.xml`, `appstore/info.xml`), their
Docker image tag, `pyproject.toml`, `cassini_live/__init__.py` and the release
archive must match; `scripts/check_manifest.py` checks it.

## [Unreleased]

Unreleased changes are collected as fragments in `changelog.d/`. Versioned
sections below are script-managed: `scripts/fold-changelog.sh` (run by
`scripts/prepare-release.sh`) folds those fragments into this file under the new
version and removes the consumed fragments. Edit released sections only to fix
mistakes; add new entries as fragments. See [`docs/release.md`](docs/release.md).

Versions up to 0.11.0 were internal builds, deployed by hand as
`live_transcription` and never published; their notes are in the README.
