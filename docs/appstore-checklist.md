# App Store checklist

What publishing Cassini Live to the Nextcloud App Store needs, in order, and
where each item stands. The release mechanics are in [release.md](release.md).
Tick items here as they land.

Cassini (`gocassini`) went through the same steps: its first App Store release
was `0.2.0-alpha.3` (2026-07-11), after running `0.1.0` internally.

## 1. In this repository (scaffolded on `feat/appstore-release`)

- [x] Store manifest `appstore/info.xml` under the app id `cassini_live`, with a
      store description, `<documentation>`, `<author>`, categories, bugs and
      repository links.
- [x] `scripts/check_manifest.py` validates both manifests against the vendored
      store schema, keeps their runtime sections identical, and keeps their
      versions equal to `pyproject.toml` and `__version__`.
- [x] Release scripts ported from gocassini: `lib-release-version.sh`,
      `release-version.sh`, `prepare-release.sh`, `release-preview.sh`,
      `fold-changelog.sh`, `extract-release-notes.sh`, `check-tag-branch.sh`,
      `sync-release-to-main.sh`. The version bump rewrites all five files that
      carry the version, `uv.lock` included.
- [x] Store package: `build-appstore-tarball.sh` (allowlist: manifest,
      `img/app.svg`, `CHANGELOG.md`, `LICENSE`; signing via `occ-nextcloud.sh`)
      and `validate-appstore-tarball.sh` (single `cassini_live/` root, id,
      version, image tag, size limits, store schema, no key material, refuses
      the `live_transcription` id).
- [x] `scripts/appstore-keys.sh`: the CSR and the registration signature, run
      by hand outside CI. It refuses to write a key inside the repo.
- [x] `.gitignore` refuses `*.key`, `*.crt`, `*.csr` and `build/`.
- [x] `CHANGELOG.md`, `changelog.d/` with its README, and the PR gate
      `.github/workflows/changelog-check.yml`.
- [x] `.github/workflows/image.yml`: a `v*` tag pushes `:<version>` (amd64 +
      arm64, each built and smoke-tested natively) after checking the tag equals the version and sits on its branch; a
      manual run pushes only `:sha-<commit>`.
- [x] `.github/workflows/release.yml`: validate, unsigned package, then one
      approval in the `release` environment; waits for the image, signs,
      creates the GitHub release, posts to apps.nextcloud.com.
- [x] `check.yml` gains a `release-tooling` job: shellcheck, the scripts' tests,
      an unsigned package built and validated. `scripts/package.py` (a source
      tarball rooted at `live_transcription/`) is retired.
- [x] `docs/release.md`.

Verified locally: `./scripts/test-release-tooling.sh` 168/168 (the store-schema
steps through lxml stand-ins for `xmllint`/`xsltproc`), shellcheck clean,
`check_manifest.py` passes. **Not verified:** none of the workflows has run;
`occ-nextcloud.sh` signing was not run here (it needs Docker and the real key).

## 2. Decisions (settled 2026-11-11)

- [x] **The store app id: `cassini_live`.** It can't be renamed after the
      certificate is issued. `live_transcription` stays supported, registered by
      hand from `appinfo/info.xml`, for local testing until the transition.
- [x] **The first store version: `0.12.0-alpha.1`.** Not a restart at 0.1.0:
      the hosts still hold old `cassini-live:0.1.0` images that a
      `ghcr.io → local` mapping would pick up.
- [x] **Main's version.** Main is at 0.11.0, the deployed version, so the first
      cut is `prepare-release.sh --bump minor` → `0.12.0-alpha.1`. After the first
      beta, main carries the next untagged `X.Y.0-alpha.0`, as in Cassini.
- [x] **Release branches: Cassini's model.** Alphas on main; `release/X.Y`
      from the first beta, carrying the rest of the X.Y line; fixes land on main
      and are cherry-picked.
- [x] **arm64: native runners, as in Cassini.** `image.yml` builds and
      smoke-tests each platform on its own runner (`ubuntu-24.04`,
      `ubuntu-24.04-arm`), pushes by digest, and joins the digests into one
      tagged multi-arch image. Not yet run.

## 3. Code before the first store release

The store package installs, but under `cassini_live` nothing starts captions
yet:

- [x] Either app id runs: `cassini_live/appapi.py` reads `APP_ID` once
      (`create_app`, and the `capacity` CLI) and everything below gets it
      passed in. Native mode is `appapi.is_talk_provider(app_id)`; under
      another id the app logs that Talk's button does not reach it.
- [x] Store mode: the USER route `POST /captions/transcribe` (`cassini_live/app.py`,
      checks in `cassini_live/call_access.py`). Same body and core as Talk's
      `/api/v1/call/transcribe`; re-checks, as the caller, room membership and
      the lobby, that the session is the caller's own (and in the call, to
      start), CSRF (`X-Cassini-Request`, same origin, JSON) and 10 toggles a
      minute per user. In both manifests and `check_manifest.py`. Guests can't
      use it: USER proxy routes need a Nextcloud login.
- [x] `ui/src/captions.js` posts to that route instead of Talk's
      `/live-transcription/{token}`, under either app id, with the tab's Talk
      session id (the signalling connection's `nextcloudSessionId`) as
      `ncSessionId`, `langId: auto` and the `X-Cassini-Request: 1` header. The
      UI fixture server accepts only that request.
- [x] Proxy paths from the app id, not the literal: the settings form's
      link (`settings_page.page_url(app_id)`), the auth middleware's
      `EX-APP-ID` check, and the panel bundles and bookmarklets, which the
      build leaves with a placeholder (`ui/src/app-id.js`) that
      `panel_page.create_router(app_id)` fills in. `replay.py` finds models
      under `APP_PERSISTENT_STORAGE` instead of `/nc_app_live_transcription_data`.
- [x] PR #8 (0.11.0, diagnostics page) is on main through #12, and its routes
      are in both manifests.
- [ ] Screenshots: up to 10 HTTPS URLs (raw.githubusercontent.com from main, as
      Cassini does) under `img/screenshots/`, listed in `appstore/info.xml`. The
      PNGs in `docs/` don't fit: one is the removed status page, the other a
      test harness.
- [x] The store description in `appstore/info.xml` describes store mode: the
      panel's bookmark starts captions, logged-in users only, and the container
      limits are set after installation.
- [x] `docs/install.md` (the store's admin documentation link) starts with the
      App Store install; the panel's install page and the README no longer
      describe only the hand-registered install.

## 4. Outside the repository (not done; each needs a person)

- [ ] Generate the key and CSR on a trusted machine:
      `./scripts/appstore-keys.sh csr ~/secure/cassini_live`.
- [ ] Open a PR with `cassini_live.csr` to
      [nextcloud/app-certificate-requests](https://github.com/nextcloud/app-certificate-requests)
      and wait for the signed `cassini_live.crt` (a manual review).
- [ ] Register the app on apps.nextcloud.com (**Register app**) with the
      certificate and `./scripts/appstore-keys.sh register-signature <key>`.
- [ ] Create an App Store API token for the account that owns the app.
- [ ] GitHub: create the `release` environment with required reviewers and the
      secrets `APP_PRIVATE_KEY`, `APP_PUBLIC_CRT`, `APPSTORE_TOKEN`
      (all environment secrets; Cassini keeps two at repo level).
- [ ] GitHub: add the `skip-changelog` label.
- [ ] GHCR: make `ghcr.io/codemyriad/cassini-live` public after its first
      push; AppAPI pulls without credentials.
- [ ] Confirm `codemyriad/cassini-live` is public: the store links to it, and
      screenshot URLs must resolve.

## 5. First release

- [ ] Cut the first pre-release with `prepare-release.sh`, push, approve.
- [ ] Rehearse first with `release.yml` `dry_run=true` on that tag.
- [ ] Install from the store on a clean Nextcloud 33 and 34 with HaRP; run a
      captioned call through the transcript panel.
- [ ] Then switch staging, and later production, from the hand-registered
      `live_transcription` to the store's `cassini_live`, following the
      Cassini Live runbook in `codemyriad/systems`
      (`systems/codemyriad/cassini-live/`). That runbook's scripts default to
      `nc_app_live_transcription` and need the new names first.
