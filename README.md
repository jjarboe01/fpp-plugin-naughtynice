# fpp-plugin-naughtynice

FPP plugin for [NaughtyNice Cloud](https://github.com/jjarboe01/naughtynice-cloud).

Runs a poll daemon on your FPP box that connects **outbound** to the cloud
service with your show token, pulls new NaughtyNice list submissions, and
triggers the display sequence via the local FPP API. No port forwarding,
no inbound firewall rules, works behind CGNAT.

## Status

**M2 in progress.** Daemon, FPP client, image compositing, and lifecycle
scripts are written and tested against mock FPP/cloud servers (see
"Testing" below) — not yet run on real FPP hardware. Do that before trusting
it on a live show.

## Install (once published / added as a plugin source)

FPP UI -> Content Setup -> Plugin Manager -> install by URL:

    https://github.com/jjarboe01/fpp-plugin-naughtynice.git

Then go to **Content Setup -> NaughtyNice Cloud - Setup**, paste in your
show token (from the naughtynice-cloud dashboard) and the cloud service
URL, and save.

## How it works

- `scripts/postStart.sh` launches `daemon/nnl_daemon.py` as a detached
  background process when fppd starts (PID file at
  `nnl_daemon.pid`, same pattern as the Remote Falcon FPP plugin).
- `scripts/preStart.sh` / `postStop.sh` clean up stale or running instances
  on start/stop; `fpp_install.sh` creates an isolated Python venv (falls
  back to `pip3 install --user` if `python3-venv` isn't available) so the
  plugin's dependencies never conflict with fppd's own Python environment.
- The daemon polls `GET /api/v1/queue` on the configured interval (default
  10s). For each submission it downloads the photo (or falls back to a
  boy/girl silhouette), composites a "breaking news" overlay via Pillow,
  uploads it to FPP (`POST /api/file/images/current_display.png`), breaks
  into the configured playlist (`GET /api/playlist/{name}/start`), and
  triggers the ticker text effect (`POST /api/command`, `Overlay Model
  Effect` / `Text`). Success -> ack; any failure -> nack (cloud expires the
  item after 3 nacks).
- Settings and status live in `config/settings.json` / `config/status.json`
  — plain JSON shared between the Python daemon and the PHP UI pages
  (`content.php`, `status.php`), deliberately not using FPP's native
  `plugin.<name>` ini settings format. See `lib/config.php` for why.

## Structure

    content.php         FPP UI: token/settings form, status panel, test-connection button
    status.php           FPP UI: read-only status page
    about.php             FPP UI: credits/links
    menu.inc               registers the above with FPP's plugin menu
    lib/config.php     PHP settings/status file helpers (mirrors daemon/config.py)
    daemon/
      nnl_daemon.py    main poll loop
      cloud_client.py  talks to naughtynice-cloud's /api/v1/*
      fpp_client.py    talks to the local FPP REST API (ported from phase-1 fpp_client.py)
      image_processor.py  photo/silhouette compositing (ported from phase-1)
      config.py            settings/status file I/O
    scripts/
      fpp_install.sh / fpp_uninstall.sh
      preStart.sh / postStart.sh / postStop.sh / preStop.sh
      _lib.sh          shared PID-file helpers

## Testing so far

No FPP hardware or Pi available in the environment this was built in, so
the daemon was tested against two mock Flask servers standing in for FPP's
local API and the naughtynice-cloud API: ping/queue/ack/nack/telemetry
flows, license-expired (402) and invalid-token (401) handling, photo
download + silhouette fallback, the exact FPP 9.x array-args command
format, and a full run of the real daemon entrypoint including graceful
SIGTERM shutdown. All passed. **Still needed:** a real end-to-end test
against an actual FPP box and P5 matrix, and a real-hardware check that
`fpp_install.sh`'s venv creation works on the FPP OS image.
