# App Store checklist

What publishing Cassini Live to the Nextcloud App Store needs, in order, and
where each item stands as of 2026-11-12. The release mechanics are in
[release.md](release.md). Tick items here as they land.

In short: the code is ready; the certificate is
requested and not yet issued; of the setup in section 4, what does not need
the certificate is mostly done; nothing has been released.

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
      any app id but the store's).
- [x] `scripts/appstore-keys.sh`: the CSR and the registration signature, run
      by hand outside CI. It refuses to write a key inside the repo.
- [x] `.gitignore` refuses `*.key`, `*.crt`, `*.csr` and `build/`.
- [x] `CHANGELOG.md`, `changelog.d/` with its README, and the PR gate
      `.github/workflows/changelog-check.yml`.
- [x] `.github/workflows/image.yml`: a `v*` tag pushes `:<version>` (amd64 +
      arm64, each built and smoke-tested natively) after checking the tag equals the version and sits on its branch; a
      manual run pushes only `:sha-<commit>`.
- [x] `.github/workflows/release.yml`: validate, unsigned package, then one
      approval in the `Release` environment; waits for the image, signs,
      creates the GitHub release, posts to apps.nextcloud.com.
- [x] `check.yml` gains a `release-tooling` job: shellcheck, the scripts' tests,
      an unsigned package built and validated. `scripts/package.py` (the old
      source tarball) is retired.
- [x] `docs/release.md`.

Verified locally: `./scripts/test-release-tooling.sh` 168/168 (the store-schema
steps through lxml stand-ins for `xmllint`/`xsltproc`), shellcheck clean,
`check_manifest.py` passes; `check.yml`, with its `release-tooling` job, passes
in CI on this branch. **Not verified:** `image.yml` and `release.yml` have never
run (they need the branch on `main`, and `release.yml` the `Release`
environment); `occ-nextcloud.sh` signing ran only with a stand-in key, in the
local store rehearsal.

## 2. Decisions (settled 2026-11-11)

- [x] **The store app id: `cassini_live`.** It can't be renamed after the
      certificate is issued. The older manifest `appinfo/info.xml` stays for a
      short time, for installs made before the store release, and will be
      removed.
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

Everything here is done.

- [x] The app id comes from AppAPI: `cassini_live/appapi.py` reads `APP_ID`
      once (`create_app`, and the `capacity` CLI) and everything below gets it
      passed in. As `cassini_live` the app logs that Talk's caption button
      does not reach it.
- [x] Captions start from the panel: the USER route `POST /captions/transcribe` (`cassini_live/app.py`,
      checks in `cassini_live/call_access.py`). Same body and core as Talk's
      `/api/v1/call/transcribe`; re-checks, as the caller, room membership and
      the lobby, that the session is the caller's own (and in the call, to
      start), CSRF (`X-Cassini-Request`, same origin, JSON) and 10 toggles a
      minute per user. In both manifests and `check_manifest.py`. Guests can't
      use it: USER proxy routes need a Nextcloud login.
- [x] `ui/src/captions.js` posts to that route instead of Talk's
      `/live-transcription/{token}`, with the tab's Talk
      session id (the signalling connection's `nextcloudSessionId`) as
      `ncSessionId`, `langId: auto` and the `X-Cassini-Request: 1` header. The
      UI fixture server accepts only that request.
- [x] Proxy paths from the app id, not the literal: the settings form's
      link (`settings_page.page_url(app_id)`), the auth middleware's
      `EX-APP-ID` check, and the panel bundles and bookmarklets, which the
      build leaves with a placeholder (`ui/src/app-id.js`) that
      `panel_page.create_router(app_id)` fills in. `replay.py` finds models
      under `APP_PERSISTENT_STORAGE` instead of a fixed volume path.
- [x] PR #8 (0.11.0, diagnostics page) is on main through #12, and its routes
      are in both manifests.
- [x] The HPB secret can be entered after installation, on the settings
      page's **Talk connection** section: Nextcloud 34 and later show no deploy
      options on the Apps page.
- [x] The entrypoint gives the persistent volume, which AppAPI creates owned
      by root, to the unprivileged account the app runs as.
- [x] The panel retries starting captions while the call is on and shows the
      server's reason until they start.
- [x] Screenshot: `img/screenshots/01-talk-transcript-panel.png` (a Talk call
      with the transcript panel; made on a local Nextcloud with made-up users
      and synthetic speech), listed in `appstore/info.xml` by its
      raw.githubusercontent.com URL on `main`, as Cassini does. The file is on
      `main` (PR #15) and the URL resolves. Up to 10 are allowed; the Apps
      page shows only the first.
- [x] The store description in `appstore/info.xml` describes store mode: the
      panel's bookmark starts captions, logged-in users only, and the container
      limits are set after installation.
- [x] `docs/install.md` (the store's admin documentation link) starts with the
      App Store install, and the README and the other docs name only
      `cassini_live`.

Stacked on this branch, not needed for the release:

- `feat/home-page`: a **Cassini Live** entry in Nextcloud's app menu, with the
  transcript bookmark and, for administrators, what is left to set up.
- `docs/appstore-install-verified`: the record of a full install from a local
  stand-in store on Nextcloud 35, in `docs/verification.md`.

## 4. Outside the repository (each needs a person)

- [x] Generate the key and CSR on a trusted machine:
      `./scripts/appstore-keys.sh csr ~/secure/cassini_live`.
- [x] Open a PR with `cassini_live.csr` to
      [nextcloud/app-certificate-requests](https://github.com/nextcloud/app-certificate-requests):
      [#1437](https://github.com/nextcloud/app-certificate-requests/pull/1437).
- [ ] Receive the signed `cassini_live.crt` (a manual review). The reviewer
      requested changes on the PR: its description must keep the template's AI
      disclosure section, filled in. Everything below that needs the
      certificate waits for this.
- [ ] Register the app on apps.nextcloud.com (**Register app**) with the
      certificate and `./scripts/appstore-keys.sh register-signature <key>`.
      Needs the certificate.
- [x] App Store API token: the token belongs to the store account, not to an
      app, so the release account's existing token is used.
- [x] GitHub: the `Release` environment exists, set up as Cassini's: required
      reviewers, deployments from `main` and `v*` tags.
- [x] GitHub: `APP_PRIVATE_KEY` and `APPSTORE_TOKEN` are in that environment.
- [ ] GitHub: add `APP_PUBLIC_CRT` to that environment. Needs the certificate.
- [ ] GitHub: add the `skip-changelog` label. Can be done now.
- [ ] GHCR: make `ghcr.io/codemyriad/cassini-live` public after its first
      push; AppAPI pulls without credentials. The package does not exist yet:
      a manual run of `image.yml` on `main` creates it (it pushes only a
      `sha-` tag) and is the first test of the amd64 and arm64 builds.
- [x] `codemyriad/cassini-live` is public: the store links to it, and
      screenshot URLs must resolve.

## 5. First release

In this order:

- [ ] Merge this branch to `main` (and, if they are to ship, the branches
      stacked on it).
- [ ] Run `image.yml` by hand on `main`; make the GHCR package public.
- [ ] Cut the first pre-release on `main`:
      `./scripts/prepare-release.sh --bump minor --push` (0.11.0 →
      0.12.0-alpha.1). It folds the changelog fragments and pushes the tag,
      which starts `image.yml` and `release.yml`; `release.yml` then waits
      for approval in the `Release` environment.
- [ ] Before approving, rehearse with a manual `release.yml` run on that tag
      with `dry_run=true`. Then approve.
- [ ] Install from the store on a clean Nextcloud 33, 34 and 35 with HaRP; run
      a captioned call through the transcript panel.
- [ ] Then switch staging, and later production, from their current
      hand-made installs to the store's `cassini_live`, following the Cassini
      Live runbook in `codemyriad/systems`
      (`systems/codemyriad/cassini-live/`). That runbook's scripts default to
      the old container name and need the new one first.
