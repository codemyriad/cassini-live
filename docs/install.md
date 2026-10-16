# Install the development ExApp

The image path in `appinfo/info.xml` is a planned release destination,
`ghcr.io/codemyriad/cassini-live:0.8.2`. This checkout builds locally; it does not
publish to GHCR or the Nextcloud App Store.

## With an existing deploy daemon

1. Check that Talk calls work through HPB. Obtain the HPB's internal secret.
2. If the official `live_transcription` ExApp is installed, disable it and
   unregister it through External Apps before registering this replacement.
   Keep its installation details for rollback. Do not install both providers.
3. Build and tag the image on the Docker host used by your deploy daemon:

   ```sh
   docker build -t ghcr.io/codemyriad/cassini-live:0.8.2 .
   ```

4. For a dedicated development daemon, configure AppAPI's local registry
   override so it uses that local image rather than trying to pull it:

   ```sh
   occ app_api:daemon:registry:add --registry-from=ghcr.io --registry-to=local DAEMON_NAME
   ```

   The override applies to that daemon's GHCR images. Use a dedicated development
   daemon if other ExApps depend on GHCR pulls. Building on a different Docker
   host will not make the image available to the deployment host.

5. Register using this manifest. Run `occ` on your Nextcloud server; the XML path
   must be readable there. Replace `DAEMON_NAME` and `YOUR_HPB_INTERNAL_SECRET`:

   ```sh
   occ app_api:app:register live_transcription DAEMON_NAME \
       --info-xml /path/on/nextcloud/cassini-live/appinfo/info.xml \
       --env CASSINI_TALK_SIGNALING_INTERNAL_SECRET=YOUR_HPB_INTERNAL_SECRET \
       --wait-finish
   ```

   Prefer External Apps deploy options when you want to avoid placing the secret
   in shell history. Do not paste it into screenshots, issues or logs.

6. Apply enforced limits **before enabling captions**. A newly registered container
   without these limits stays blocked and does not load recognition workers.
   For an example host with at least eight available CPUs and 4 GiB RAM:

   ```sh
   docker update --cpus=4 --memory=2g --memory-swap=2g --pids-limit=128 nc_app_live_transcription
   docker exec nc_app_live_transcription python -m cassini_live.capacity --installed
   ```

   Equal memory and memory-swap values disable swap. Size the limits for your
   host; we advise leaving it at least half its CPU and RAM ([load and
   admission](capacity.md)), and a low CPU weight (`docker update --cpu-shares`)
   makes the kernel take CPU from captions first when the host is busy.
   Persist CPU and memory limits in the AppAPI daemon's deployment options.
   AppAPI may not preserve PID and swap limits across container recreation;
   reapply them after upgrades. Until then, captions refuse to start. Corrections
   are detected automatically within 15 seconds.

   Keep the recovery journal on persistent storage across container recreation
   (see [recovery policy](capacity.md)). Give the app user write access.

   Configure Docker log rotation on the deployment host before container creation
   (for example, the `local` logging driver with `max-size=10m`, `max-file=3`).
   Changing daemon logging defaults only affects newly created containers.
   Do not use an unbounded log driver on a shared server.

7. Wait for the engine to load (seconds once its files are on disk). As an
   administrator, open **Administration settings → Assistant →
   Cassini Live** and reload the page until it shows "Captions are ready.", or run
   the `cassini_live.capacity --installed` command from step 6.
   See [load, admission and capacity](capacity.md) for recovery and configuration errors. Join a Talk
   call with another participant, enable captions from the call menu, and speak.
   Test your languages, a late joiner, microphone mute/unmute and stop/start.

If the public HPB URL is unreachable from the container, set the optional
`CASSINI_TALK_HPB_URL` deploy variable to its reachable URL. Keep certificate
verification enabled. Network routing and TLS still need to be valid.

The app uses ordinary WebRTC to receive HPB audio, using the STUN/TURN settings
already configured in Talk. Permit the necessary UDP/ICE traffic from the app
container. No recording server, bot account, storage mount or GPU is needed.
HaRP deployments start the included FRP client and use the SDK's Unix socket;
direct deployments use `APP_HOST` and `APP_PORT`.
The image permits HaRP to install certificates as root. Its entrypoint drops
to the `cassini` user before starting FRP, the API or model workers.

## Choose a speech engine

Cassini Live runs one speech engine for the whole server. Whistle is bundled and
needs the least memory. Two optional NVIDIA Nemotron engines are more accurate
but need much more memory. They are downloaded only when an administrator
chooses one.

| Engine | Languages | Word errors on our English meetings | Container memory | Download |
|---|---|---|---|---|
| Whistle (default) | 7, automatic detection | 19% | 384 MiB + 192 MiB per worker; 2 GiB recommended | none, bundled |
| Nemotron English | English only | 8.9% | at least 2.25 GiB; 3 GiB recommended | 442 MiB |
| Nemotron Multilingual | 7, automatic detection | 12.8% | at least 2.25 GiB; 3 GiB recommended | 453 MiB |

Both Nemotron engines also need a CPU quota of at least one CPU, AVX2 on x86_64
processors, host RAM of at least twice the container memory limit (so at least
4.5 GiB), and about 1.3 GiB of free persistent storage during the download.
They need less CPU than Whistle: two simultaneous speakers per recognizer
thread, against one worker per two CPUs. The memory figures are measured; the
CPU figures come from a test harness until the staging lag measurement confirms
them. Host RAM below twice the container limit is shown as a warning, not a
block.

The settings live in **Administration settings → Assistant →
Cassini Live** (`/settings/admin/ai`; Nextcloud 33 and earlier label this
section "Artificial intelligence"). Only Nextcloud administrators see it. The
form shows a short status line and the engine choice. The **?** next to the
"Cassini Live" title opens the full settings page (admin-only, served by the app
through AppAPI): a card per engine with accuracy, languages, requirements and
license, live download and switch progress, cancel, removal of downloaded models,
the caption status, the live speaker count and how far captions trail speech, and
**Measure capacity** with the last result (only while no call has captions;
captions pause for up to two minutes). The page refreshes itself.

* Each option label in the form says whether the engine is in use, ready,
  available, or "unavailable:" followed by a short form of the first reason, for
  example "needs more container memory". Nextcloud cannot grey out an option; the
  settings page disables the button and lists every reason. If you choose an
  unavailable engine in the form, Cassini Live puts the previous choice back
  within a few seconds and says why at the start of the status line.
* Nemotron English is licensed under the NVIDIA Open Model License and Nemotron
  Multilingual under OpenMDW-1.1. The settings page names each license, links its
  full text and shows the required notice. There is no acceptance step: using an
  engine means its license applies. Both license texts and notices are stored
  next to the downloaded model files. See [third-party notices](../NOTICE.md).
* The model downloads from the sherpa-onnx release on GitHub into
  `APP_PERSISTENT_STORAGE/models/<engine>/`, or
  `/home/cassini/.local/state/models/` when that variable is unset. Keep this
  directory on persistent storage, or the model downloads again after the
  container is recreated. Captions keep working with the old engine during the
  download. Cancel on the settings page, or select the old engine again in the
  form.
* After the download, Cassini Live checks the files, then **ends captions in all
  active calls** and loads the new engine: seconds from a warm disk, up to two
  minutes cold. Participants turn captions on again afterwards.
* If the new engine fails to load, Cassini Live rolls back to the previous
  engine and shows the reason. At startup, a missing or
  damaged model falls back to Whistle. A failed engine is never retried
  automatically: choose it again after you fix the cause.
* To free disk space, remove a downloaded model that is not in use on the
  settings page (**Remove downloaded files** on its card).

Upgrading from 0.7.0: 0.7.1 adds admin-only proxy routes for the settings page.
Update with `occ app_api:app:update live_transcription` (or reinstall); AppAPI
re-reads routes only on a version change.

An AppAPI update recreates the container without its swap and PID limits until
you re-apply them. If Cassini Live starts in that state, it runs Whistle and
says why, but keeps a selected Nemotron engine: it returns by itself at the next
start with suitable limits. The same happens when, at startup, the container's
free memory cannot hold Nemotron but can hold Whistle: Whistle captions calls
and Nemotron returns at the next start where it fits. Until then its files
cannot be removed. To stay on
Whistle instead, choose Whistle in Administration settings or click "Keep using
Whistle" on the settings page. A missing or damaged model, by contrast, switches
the choice back to Whistle for good.

Raise the limits before you choose a Nemotron engine. For example, on a host
with at least 6 GiB RAM and eight CPUs:

```sh
docker update --cpus=4 --memory=3g --memory-swap=3g nc_app_live_transcription
```

Persist the same values in the AppAPI daemon's deployment options. The number
of simultaneous speakers follows the CPU limit; there is nothing else to set.

## Check before a release

Verify on a disposable Nextcloud with AppAPI and a real HPB, for each supported
Nextcloud/Talk release. The automated signalling fixture does not replace this
check. Verify participant caption preferences, room language changes, guest
calls, reconnect after an HPB restart, overflow behaviour, TLS and HaRP routing.
Call media and captions must not appear in the container filesystem or logs.

With Whistle, the default is one recognition worker per two quota CPUs, capped
at four. With Nemotron, one shared engine process serves up to four simultaneous
speakers, depending on the CPU quota. To serve fewer speakers, lower the CPU
limit. Speech waits briefly for workers. When captions fall behind, the newest speaker's captions
pause for a few seconds while the others keep theirs; only an OOM kill or
repeated engine crashes stop caption rooms. The Talk meeting itself continues
without captions.

## Upgrading to 0.8.2

0.8.2 removes the `CASSINI_LIVE_WORKERS` setting: Cassini Live sizes itself
from the container's CPU limit. A value left in the deploy options from an
earlier registration is ignored, whatever it is, and logged once at startup.
It disappears when the ExApp is registered again with this manifest.

## Rollback

Disable and unregister Cassini Live through External Apps, then register the
previous provider with its original image and deploy options. Cassini Live
creates no room history, service account or recording configuration to undo.
Keep `gocassini` installed if you use it for recordings.
