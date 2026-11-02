# Run against an existing development harness

`scripts/dev_harness.py` builds this checkout and installs it into an already
running Nextcloud/Talk harness. It uses Python's standard library and the Docker
CLI; it does not read another checkout, invoke Compose, discover containers, or
start the harness. Cassini and Cassini Live can be independent sibling clones.

```text
Cassini checkout                     Cassini Live checkout
  starts harness                       scripts/dev_harness.py
       |                                  |
       v                                  v
Nextcloud + Talk + HPB <--- Docker network + registered Live container
```

## Prerequisites and invocation

Install Python 3.12+ and Docker with a working Docker CLI. Start a full harness
with working Talk calls, HPB and AppAPI. The defaults below match the original
Cassini harness. Run against the same Docker engine that hosts that harness.
The first image build downloads the bundled speech models and needs internet.

Supply the HPB internal secret (the signaling server's `[clients] internalsecret`,
not Talk's shared backend secret) and its HTTP(S) URL reachable **from the Live
container**. For example, if HPB is reachable through Docker Desktop's host alias:

```sh
cd /path/to/cassini-live
export LIVE_DEV_HPB_URL=http://host.docker.internal:28082
read -r -s LIVE_DEV_HPB_SECRET  # Enter the internal secret, then press Return.
export LIVE_DEV_HPB_SECRET
python3 scripts/dev_harness.py --check-only
python3 scripts/dev_harness.py
```

Use the links printed by the script. Join a Talk call with another participant
and turn on captions in the call menu. The transcript panel requires the
bookmarklet from the printed panel link.

`--check-only` performs read-only configuration, harness and ownership checks;
it does not build, deploy, or test the supplied HPB credential. A full run checks
engine readiness, authenticates to HPB without joining a call, and checks the
panel and settings routes. A real call remains the check for audio/ICE delivery.

## Configuration

Every option supports **CLI flag > environment variable > default**. Empty
explicit values fail validation. Run `python3 scripts/dev_harness.py --help` for
all flags. Environment variables use `LIVE_DEV_` followed by the uppercase flag
name with hyphens replaced by underscores: `--public-url` maps to
`LIVE_DEV_PUBLIC_URL`.

| Flag | Default |
| --- | --- |
| `--nextcloud-container` | `spreedtest-nextcloud-1` |
| `--signaling-container` | `spreedtest-signaling-1` |
| `--network` | `spreedtest_default` |
| `--nextcloud-url` | `http://nextcloud` (container-to-Nextcloud callbacks) |
| `--public-url` | `http://localhost:28080` (browser and host checks) |
| `--hpb-url` | Required |
| `--hpb-secret` | Required |
| `--admin-user` / `--admin-password` | `admin` / `admin` (development credentials) |
| `--container` | `nc_app_live_transcription` |
| `--daemon` | `cassini_live_dev` |
| `--image` | `cassini-live:dev` |
| `--aa-version` | `5.0.0` (AppAPI deployment environment) |
| `--cpus` / `--memory` / `--pids-limit` | `4` / `2g` / `128` |
| `--volume` | `cassini_live_dev_data` |
| `--timeout` | `180` seconds per operation/readiness wait |
| `--build-timeout` | `3600` seconds |
| `--app-secret` | Generated per deployment; shared with AppAPI registration |
| `--check-only [true\|false]` | `false`; flag without a value means `true` |

For example, `LIVE_DEV_MEMORY=2g python3 scripts/dev_harness.py --memory 3g`
uses 3 GiB. Swap is disabled by setting memory and memory-swap to the same value.
The app ID, version and routes come from this checkout's `appinfo/info.xml`.
The script derives its build context from its own location, so any working
directory works. It needs no Python packages installed on the host.

## Checks, repeat runs and failures

The script checks the named containers, network, Nextcloud readiness, enabled
Talk/AppAPI apps, admin access, URL consistency and resource ownership before
building. Missing prerequisites fail without installing apps or changing the
harness configuration.

```text
Resolve options -> read-only preflight -> build image
                                            |
                                            v
                           replace owned container + register ExApp
                                            |
                                            v
                           engine readiness -> HPB auth -> UI checks
```

The public URL must match Nextcloud's `overwrite.cli.url`. Using `localhost` in
one place and `127.0.0.1` in another can put caption participants in a different
HPB room. The script fails on a mismatch and prints the command shape to correct
it; it never silently changes that setting. Use exactly the printed browser URL.

Repeat runs rebuild and replace this script's labeled container, refresh the
registration/routes, and preserve the named volume containing downloaded models
and recovery state. This interrupts active captions. Image build failures leave
the previous deployment running. Later failures leave resources available for
inspection and retry; the script does not automatically roll back.

An existing unowned container or caption provider causes an early failure.
For a one-time migration from the old wrapper script, explicitly unregister the
old development `live_transcription` ExApp and remove its container after saving
any state you need. The new helper does not adopt or overwrite that installation.
Cassini's `gocassini` recording ExApp remains separate. An existing daemon is
reused only if its type, host, protocol and Nextcloud URL match the supplied values.

Secrets are not printed by the script. Prefer environment variables to CLI flags
for credentials. Docker administrators can inspect container environment values.
Container logs use Docker's bounded `local` driver. To inspect a failure with the
default names:

```sh
docker logs --tail 100 nc_app_live_transcription
docker exec nc_app_live_transcription python -m cassini_live.capacity --installed
```

To remove the helper's default installation while retaining its data:

```sh
docker exec -u www-data spreedtest-nextcloud-1 php occ \
  app_api:app:unregister live_transcription --force
docker rm -f nc_app_live_transcription
docker exec -u www-data spreedtest-nextcloud-1 php occ \
  app_api:daemon:unregister cassini_live_dev
```

The volume `cassini_live_dev_data` remains until you explicitly remove it.
