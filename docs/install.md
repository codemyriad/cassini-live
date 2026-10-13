# Install the development ExApp

The image path in `appinfo/info.xml` is a planned release destination,
`ghcr.io/codemyriad/cassini-live:0.6.0`. This checkout builds locally; it does not
publish to GHCR or the Nextcloud App Store.

## With an existing deploy daemon

1. Check that Talk calls work through HPB. Obtain the HPB's internal secret.
2. If the official `live_transcription` ExApp is installed, disable it and
   unregister it through External Apps before registering this replacement.
   Keep its installation details for rollback. Do not install both providers.
3. Build and tag the image on the Docker host used by your deploy daemon:

   ```sh
   docker build -t ghcr.io/codemyriad/cassini-live:0.6.0 .
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
   host; [the safety policy](capacity.md) reserves at least half its CPU and RAM.
   Persist CPU and memory limits in the AppAPI daemon's deployment options.
   AppAPI may not preserve PID and swap limits across container recreation;
   reapply them after upgrades. Until then, captions refuse to start. Corrections
   are detected automatically; no manual capacity check is needed.

   Keep the recovery journal on persistent storage across container recreation
   (see [recovery policy](capacity.md)). Give the app user write access.

   Configure Docker log rotation on the deployment host before container creation
   (for example, the `local` logging driver with `max-size=10m`, `max-file=3`).
   Changing daemon logging defaults only affects newly created containers.
   Do not use an unbounded log driver on a shared server.

7. Open **Cassini Live** in Nextcloud. Wait for its measured-capacity result (usually under two minutes).
   See [capacity assessment](capacity.md) for automatic recovery and configuration errors. Join a Talk
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

## Check before a release

Verify on a disposable Nextcloud with AppAPI and a real HPB, for each supported
Nextcloud/Talk release. The automated signalling fixture does not replace this
check. Verify participant caption preferences, room language changes, guest
calls, reconnect after an HPB restart, overflow behaviour, TLS and HaRP routing.
Call media and captions must not appear in the container filesystem or logs.

The default is one recognition worker per two quota CPUs, capped at four.
`CASSINI_LIVE_WORKERS` can reduce this count; requesting more than the budget
allows blocks startup. Speech waits briefly for workers, and sustained overload
stops caption rooms. The Talk meeting itself continues without captions.

## Rollback

Disable and unregister Cassini Live through External Apps, then register the
previous provider with its original image and deploy options. Cassini Live
creates no room history, service account or recording configuration to undo.
Keep `gocassini` installed if you use it for recordings.
