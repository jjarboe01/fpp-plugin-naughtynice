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
URL, and save. The PhotoZone/TickerZone overlay models and the
breaking-news playlist are set up for you automatically during install —
see "Zone auto-provisioning" below.

**After installing (or updating) the plugin, restart FPPD using the
"Restart FPPD (full)" button on this plugin's own setup page** (Content
Setup -> NaughtyNice Cloud - Setup -> **FPP Control**), not FPP's normal
Status/Control -> Restart FPPD button.

**Incident, 2026-07-06 — FPP's own "Restart FPPD" button doesn't reliably
restart the plugin daemon.** FPP's UI defaults to a *quick* restart
(`GET api/system/fppd/restart?quick=1`, see FPP's own `js/fpp.js`) whenever
it doesn't think a full restart is required. A quick restart just reloads
fppd's config in place — it does **not** re-run `preStart.sh`/`postStart.sh`
— so an already-running daemon (a detached `nohup`'d process, outside
fppd's own process tree) is never touched, even though fppd itself shows a
fresh uptime immediately after. This showed up as: plugin files updated to
a new version on disk, FPPD uptime freshly reset, but the daemon's own log
(`logs/fpp-plugin-naughtynice-daemon.log`) showing no new "Starting
NaughtyNice Cloud plugin daemon" line at all — the old process was simply
never replaced. Only a full stop/start of fppd (no `quick` flag) or a full
Pi reboot actually re-invokes the plugin hooks and picks up new code.
Fixed by adding **Restart FPPD (full)** and **Reboot Pi** buttons directly
to this plugin's setup page (`content.php`), which call FPP's own
`/api/system/fppd/restart` (without `quick=1`) and `/api/system/reboot`
respectively — no more relying on FPP's own button defaulting the right way.

Until the daemon has actually (re)started, the setup/status pages will
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

## Zone auto-provisioning

`fpp_install.sh` runs `daemon/fpp_provision.py` once automatically, so the
plugin is ready to run right after install — no hand-building Pixel Overlay
Models or a playlist first:

- Finds your existing matrix's channel-output model (the largest-channel
  "Channel" type model not already named PhotoZone/TickerZone), derives its
  pixel width/height, and appends two new Pixel Overlay Models —
  `PhotoZone` and `TickerZone` — right after your existing channels. The
  photo/ticker split matches phase 1's proven ratio (~73%/27%) scaled to
  your matrix's actual height.
- Creates a `breaking_news` playlist with a single **script** entry
  pointing at `nnl_display_image.py` (deployed to
  `/home/fpp/media/scripts/` by `fpp_install.sh`). Holds the display for
  `display_duration_seconds` (default 20s) then hands control back.
- **Idempotent and safe to re-run** (a "Re-run zone setup" button lives on
  the setup page): if PhotoZone/TickerZone already exist, their *current*
  on-FPP size is treated as the source of truth and synced into
  `settings.json` — so resizing either model by hand in FPP's own editor,
  then re-running, picks up your change without a code edit. Pass
  `--force-recreate` (the "Full reset" checkbox on the setup page) to
  instead delete and rebuild both from freshly detected geometry.
- The playlist itself is only ever *created* if missing, never overwritten
  by a re-run — safe to customize (add lead-in content, etc.) as long as
  the script entry stays.

**Incident, 2026-07-06 — a fresh install wiped the customer's real matrix
model.** The original `fpp_provision.py` created PhotoZone/TickerZone by
POSTing one bare model object per call. `POST /api/models` in FPP does
not append — confirmed from FPP's own source
(`PixelOverlayManager::render_POST`, `src/overlays/PixelOverlay.cpp`) —
it truncates and rewrites `config/model-overlays.json` with the raw
request body, verbatim, every time. Two consequences: the pre-existing
real matrix model (originally named `P5Large`) was destroyed by the
first call, and neither new zone model even loaded correctly itself
(a bare object has no top-level `"models"` key for FPP's loader to
find). FPP's own channel-output subsystem auto-recreated a bare
replacement (`P5`, `autoCreated: true`) after the fact so the box didn't
go fully dark, but the original model's name and any manual tweaks were
lost. **Fixed:** every model-list change now does GET the full current
list → modify in memory → POST the complete `{"models": [...]}` list
back in exactly one call, so anything already on FPP (including the
customer's own real matrix) is always preserved. Also added `--dry-run`
(and a matching checkbox on the setup page) to preview the exact
payload before writing anything — verified live against the real Pi5
post-fix: the plan correctly preserved the existing model and added
both zones without a single write.

**Why a script entry and not FPP's native "image" playlist entry type?**
Tested live against this plugin's own Pi5 dev rig on two different FPP
9.5.3 builds — the native `"image"` entry hung indefinitely (never
reported finished), even with a known-good file. A `"script"` entry
running the same shared-memory-buffer approach as phase 1's
`show_display_image.py` was confirmed to run to completion reliably on
the same hardware. Don't switch this back to a native image entry without
retesting on real hardware first.

**Why `nnl_display_image.py` uses `ffmpeg`, not Pillow.** FPP doesn't exec
a playlist script entry directly — it dispatches through
`$FPPDIR/scripts/eventScript`, which for any `.py` file hardcodes
`exec /usr/bin/python3 "$@"`, ignoring the script's own shebang and
executable bit entirely. That means this script always runs under the
bare system `python3`, never this plugin's venv, so it can't rely on
Pillow (a venv-only dependency used by the *daemon*, which is launched
differently — see `scripts/postStart.sh`, which invokes the venv python
explicitly rather than going through `eventScript`). Shelling out to
`ffmpeg` (already relied on by FPP itself, and by phase 1's original
script on this exact hardware) needs nothing beyond the standard library,
so it's unaffected by which interpreter FPP decides to invoke. An earlier
version of this file imported PIL directly and would have raised
`ModuleNotFoundError` every time it actually ran under FPP — caught before
shipping to a real customer, not from a live failure.

## Uninstalling

FPP's Plugin Manager removes the plugin's own directory
(`/home/fpp/media/plugins/fpp-plugin-naughtynice`) after
`scripts/fpp_uninstall.sh` runs — that script itself only stops the poll
daemon. Neither step touches the FPP-level artifacts that install-time
auto-provisioning created **outside** the plugin directory:

- The `PhotoZone` and `TickerZone` Pixel Overlay Models
- The `breaking_news` playlist
- `/home/fpp/media/scripts/nnl_display_image.py`

These are left in place on purpose — they're the show's own content, not
disposable plugin state, so a routine uninstall/reinstall (e.g. to update
the plugin) doesn't wipe out a manually-tuned matrix layout. That also
means a normal uninstall leaves the "sync from existing" path as the one
`fpp_provision.py` will take on the next install, not the "detect and
create" path.

**For a true from-scratch test** (or to fully remove every trace of the
plugin), also do the following before reinstalling:

1. Content Setup -> Pixel Overlay Models -> delete `PhotoZone` and
   `TickerZone`.
2. Content Setup -> Playlists -> delete `breaking_news`.
3. Over SSH: `rm /home/fpp/media/scripts/nnl_display_image.py`

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
- Each poll also reports plugin + FPP version to the cloud (`POST
  /api/v1/telemetry`) via `FPPClient.get_fpp_version()` (`GET
  /api/fppd/version`), stored as `PluginCheckin.plugin_version`/
  `fpp_version` and shown on both the plugin's own status page and the
  cloud admin Shows page — support/troubleshooting info, not used for any
  compatibility gating.
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
      fpp_provision.py     auto-creates PhotoZone/TickerZone models + playlist (see Zone auto-provisioning above)
    scripts/
      fpp_install.sh / fpp_uninstall.sh
      preStart.sh / postStart.sh / postStop.sh / preStop.sh
      _lib.sh          shared PID-file helpers
      nnl_display_image.py  deployed to /home/fpp/media/scripts/ — the playlist script entry that
                             writes each submission's composited photo into PhotoZone's overlay buffer

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
