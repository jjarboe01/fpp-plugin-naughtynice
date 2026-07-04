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

FPP UI -> Content Setup -> Plugin Manager. The "Get Plugin Info" box wants
a URL to **pluginInfo.json itself**, not the repo's `.git` clone URL —
paste this exactly:

    https://raw.githubusercontent.com/jjarboe01/fpp-plugin-naughtynice/main/pluginInfo.json

(Pasting the plain `https://github.com/.../fpp-plugin-naughtynice.git` URL
does not work — FPP's `ManualLoadInfo()` in `plugins.php` only swaps the
`github.com` host for `raw.githubusercontent.com`, it doesn't append the
branch or filename, so the fetch 404s. A bug in that same function's error
handler then throws before its `alert()` runs, so the "Get Plugin Info"
button silently does nothing instead of showing an error — always use the
raw pluginInfo.json URL above.)

Then go to **Content Setup -> NaughtyNice Cloud - Setup**, paste in your
show token (from the naughtynice-cloud dashboard) and the cloud service
URL, and save.

**After installing (or updating) the plugin, restart FPPD** (Status/Control
-> Restart FPPD, or reboot the Pi) so `scripts/postStart.sh` actually
launches the poll daemon. FPP only runs a plugin's `postStart.sh` when
`fppd` itself starts — installing a plugin while `fppd` is already running
does not start its daemon. Until you restart, the setup/status pages will
correctly show "Test connection" succeeding (that's a live PHP curl call)
while the **Status** panel stays empty/stale, because nothing has ever
written `config/status.json`. If you don't want to restart the whole show
right away, you can start it manually once over SSH:

    /home/fpp/media/plugins/fpp-plugin-naughtynice/scripts/postStart.sh

## Dev/prod environment toggle

The setup page has two credential slots — **Production** and
**Development** — each with its own cloud service URL and show token,
defaulted to `https://naughtynicefpp.com` and `https://dev.naughtynicefpp.com`.
A radio button picks which one the daemon actually polls (`environment` in
`config/settings.json`). Both slots are always saved, so flipping back and
forth for plugin/server testing never requires re-pasting a token. Both
the setup page and the status page show a colored **ACTIVE ENVIRONMENT**
banner (green prod, orange dev) so it's obvious at a glance which one is
live — important since the Pi only runs one daemon and one FSEQ/matrix, so
"dev" and "prod" are a config toggle on the same hardware, not separate
boxes.

Settings files created before this feature (flat `cloud_base_url`/`token`,
no `environments` key) are migrated automatically into the `prod` slot the
first time either `content.php` or the daemon loads them — no manual
migration needed.

**This toggle is manual/human-operated only.** Which environment is active
should only ever change because Joe clicked the radio button on the setup
page — not as a side effect of unrelated code changes, deploys, or
automated edits. If you're working on something else in this plugin,
leave `environment` alone.

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

## Compared against fpp-plugin-Template

Checked 2026-07-04 against
[FalconChristmas/fpp-plugin-Template](https://github.com/FalconChristmas/fpp-plugin-Template).
Everything required is present (`pluginInfo.json`, `menu.inc`, `status.php`,
`content.php`, `about.php`, the `scripts/` lifecycle hooks, and
`fpp_install.sh`/`fpp_uninstall.sh`). The template's other files are
optional hooks this plugin doesn't need and deliberately omits:

- `api.php` — lets a plugin add custom `/api/plugin/<name>/*` REST
  endpoints. Not needed; `content.php`'s settings form does a plain POST
  and reloads, no AJAX API required.
- `output.php` — an Output Setup menu page for configuring show outputs
  (channels/controllers). Not applicable — this plugin doesn't touch
  output config.
- `commands/` (`descriptions.json` + `on.sh`/`off.sh`) — registers custom
  actions in FPP's Commands list (schedulable/triggerable independently).
  Not needed — this plugin has nothing that makes sense as a standalone
  FPP Command.

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
