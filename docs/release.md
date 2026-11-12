# Releasing Cassini Live

Cassini Live releases the way [Cassini](https://github.com/codemyriad/gocassini/blob/main/docs/release.md)
does, with the same scripts, version ladder and branch rules. This page covers
what differs and the commands; Cassini's page has the full reasoning.

A release ships two things, kept in lockstep by one version: the Docker image
`ghcr.io/codemyriad/cassini-live:<version>` and the App Store package
`cassini_live.tar.gz`. The git tag `v<version>` and both manifests'
`<image-tag>` must equal it. CI enforces it.

Before the first release, work through the [App Store checklist](appstore-checklist.md).

## The manifest

Cassini Live is released under the app id `cassini_live`, from
`appstore/info.xml`. Talk sends its caption button only to Nextcloud's own Live
Transcription app, so captions start from the transcript panel.

The store package contains `appstore/info.xml` as `appinfo/info.xml`. The
repository's own `appinfo/info.xml` is an older manifest, kept for a short
time for installs made before the store release; it will be removed. Until
then `scripts/check_manifest.py` keeps the two files' runtime
sections (`<dependencies>`, `<external-app>`: image, deploy options, routes)
identical, and both manifests' versions equal to `pyproject.toml` and
`cassini_live/__init__.py`.

## Versions

The version lives in five places, and the release scripts rewrite them together
(`rv_write_release_version` in `scripts/lib-release-version.sh`):

- `appinfo/info.xml` and `appstore/info.xml`: `<version>` and `<image-tag>`
- `pyproject.toml`: `[project] version`
- `cassini_live/__init__.py`: `__version__`
- `uv.lock`: the `cassini-live` package, in PEP 440 form (`0.12.0-alpha.1` is
  recorded as `0.12.0a1`)

Never edit them by hand; use `./scripts/release-version.sh set <version>`.

The ladder is Cassini's: `X.Y.Z-alpha.N → beta.N → rc.N → X.Y.Z`. A minor or
major bump starts at `-alpha.1`, a patch goes straight to stable. Suffixed
versions upload as App Store pre-releases. AppAPI only installs an update to a
higher version, so a version is never reused: an image rebuilt under the same
version is not picked up by `app_api:app:update`.

## Branches

As in Cassini: `main` is the next release and carries its alphas;
`release/X.Y` is branched from main at X.Y's first beta and carries the rest of
the X.Y line, including patches. Fixes land on main first and are
cherry-picked. `prepare-release.sh` refuses to cut on the wrong branch, and
`check-tag-branch.sh` refuses a hand-made tag in CI. `sync-release-to-main.sh`
brings a release-branch cut's changelog and version back to main.

## Cut a release

On the version's branch:

```bash
./scripts/release-preview.sh                          # what is pending, and the candidates
./scripts/prepare-release.sh                          # guided: pick, confirm, commit + tag locally
./scripts/prepare-release.sh --bump minor             # 0.11.0 -> 0.12.0-alpha.1 (main)
./scripts/prepare-release.sh --promote beta           # 0.12.0-alpha.N -> 0.12.0-beta.1 (release/0.12)
./scripts/prepare-release.sh --version 0.12.0         # straight to stable
```

Add `--push` to push the commit and tag in one go; without it, nothing leaves
your machine. The tag push starts two workflows:

1. `image.yml` builds `ghcr.io/codemyriad/cassini-live:<version>` for amd64
   and arm64, each on a native runner, smoke-tests both images (Whistle loads
   offline; no engine loads without cgroup limits) and tags them as one
   multi-arch image. A manual run of it pushes only `:sha-<commit>`, never a
   version.
2. `release.yml` validates the tag and both manifests, builds and validates an
   unsigned package, then waits for **one approval** in the `Release`
   environment. After approval it waits for the image, signs the package with
   `occ integrity:sign-app` in a throwaway Nextcloud container, creates the
   GitHub release with the package attached, and posts it to
   apps.nextcloud.com.

So a release is one command and one approval. Re-run `release.yml` from
**Actions** on an existing tag with `dry_run=true` to rehearse, or
`publish_appstore=false` for a signed GitHub release without the store.

## Changelog

Changes are captured per pull request as fragments in
[`changelog.d/`](../changelog.d/README.md); the `Changelog` workflow asks for
one (or the `skip-changelog` label) on any PR that changes shipping files.
`prepare-release.sh` folds them into `CHANGELOG.md`.

## The `Release` environment (one-time setup)

**Settings → Environments → `Release`**, set up as Cassini's environment of the
same name: **required reviewers** (any one approves; the person who cut the
release may), and deployments limited to the branch `main` and tags `v*`. These
are its environment secrets:

| Secret | What |
|---|---|
| `APP_PRIVATE_KEY` | App Store code-signing private key for `cassini_live` (PEM) |
| `APP_PUBLIC_CRT` | The certificate Nextcloud issued for `cassini_live` (PEM) |
| `APPSTORE_TOKEN` | App Store API token of the account that owns `cassini_live` |

Never commit them. `.gitignore` refuses `*.key`, `*.crt` and `*.csr`, and the
package validator refuses them inside an archive.

## Rollback

- **Not pushed yet:** `git tag -d v<version> && git reset --hard HEAD~1`.
- **Pushed a bad tag:** `git push origin :refs/tags/v<version>`, fix, re-tag.
- **App Store:** a published release can't be overwritten; cut the next version.

Never delete a GHCR `:<version>` tag that a live store release pins: AppAPI
pulls exactly that tag on install and update.

## Local checks

```bash
./scripts/test-release-tooling.sh                     # the release scripts' tests
uv run python scripts/check_manifest.py               # manifests, versions, routes
./scripts/build-appstore-tarball.sh --version "$(./scripts/release-version.sh current)"
./scripts/validate-appstore-tarball.sh --version "$(./scripts/release-version.sh current)" \
  --tarball build/artifacts/appstore/cassini_live.tar.gz
```

The store-schema step needs `xsltproc` and `xmllint`; without them the
validator warns and skips it, and `check_manifest.py` still validates both
manifests against the same schema.

## References

- [Nextcloud release process](https://docs.nextcloud.com/server/latest/developer_manual/app_publishing_maintenance/release_process.html)
  · [code signing](https://docs.nextcloud.com/server/latest/developer_manual/app_publishing_maintenance/code_signing.html)
  · [App Store API](https://nextcloudappstore.readthedocs.io/en/latest/api/restapi.html)
