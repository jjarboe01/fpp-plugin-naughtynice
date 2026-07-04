<?php
require_once __DIR__ . '/lib/config.php';

$nnlMessage = '';
$nnlMessageClass = '';

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $nnlSettings = nnl_load_settings();

    if (isset($_POST['nnl_action']) && $_POST['nnl_action'] === 'save') {
        $nnlSettings['token'] = trim($_POST['token']);
        $nnlSettings['cloud_base_url'] = rtrim(trim($_POST['cloud_base_url']), '/');
        $nnlSettings['fpp_base_url'] = rtrim(trim($_POST['fpp_base_url']), '/');
        $nnlSettings['poll_interval_seconds'] = max(5, intval($_POST['poll_interval_seconds']));
        $nnlSettings['playlist'] = trim($_POST['playlist']);
        $nnlSettings['ticker_model'] = trim($_POST['ticker_model']);
        $nnlSettings['matrix_width'] = max(1, intval($_POST['matrix_width']));
        $nnlSettings['photo_zone_height'] = max(1, intval($_POST['photo_zone_height']));
        $nnlSettings['enabled'] = isset($_POST['enabled']);

        nnl_save_settings($nnlSettings);
        $nnlMessage = 'Settings saved. The daemon picks up changes on its next poll cycle (no restart needed).';
        $nnlMessageClass = 'nnl-ok';
    } elseif (isset($_POST['nnl_action']) && $_POST['nnl_action'] === 'test') {
        $result = nnl_test_connection($nnlSettings);
        $nnlMessage = $result['message'];
        $nnlMessageClass = $result['ok'] ? 'nnl-ok' : 'nnl-error';
    }
}

$nnlSettings = nnl_load_settings();
$nnlStatus = nnl_read_status();
$actionUrl = htmlspecialchars($_SERVER['REQUEST_URI']);
?>
<style>
  .nnl-box { max-width: 640px; }
  .nnl-box label { display: block; margin-top: 10px; font-weight: bold; }
  .nnl-box input[type=text], .nnl-box input[type=password], .nnl-box input[type=number] {
    width: 100%; max-width: 420px; padding: 6px; box-sizing: border-box;
  }
  .nnl-msg { padding: 8px 12px; margin: 10px 0; border-radius: 4px; }
  .nnl-ok { background: #e2f7e2; color: #1a5d1a; }
  .nnl-error { background: #fde2e2; color: #8a1c1c; }
  .nnl-status-table td { padding: 2px 10px 2px 0; }
</style>

<div class="nnl-box">
  <h3>NaughtyNice Cloud — Setup</h3>

  <?php if ($nnlMessage): ?>
    <div class="nnl-msg <?php echo $nnlMessageClass; ?>"><?php echo htmlspecialchars($nnlMessage); ?></div>
  <?php endif; ?>

  <fieldset>
    <legend>Status</legend>
    <table class="nnl-status-table">
      <tr><td>License:</td><td><strong><?php echo htmlspecialchars($nnlStatus['license'] ?? 'unknown — daemon hasn\'t polled yet'); ?></strong></td></tr>
      <tr><td>License expires:</td><td><?php echo htmlspecialchars($nnlStatus['license_expires'] ?? '—'); ?></td></tr>
      <tr><td>Last poll:</td><td><?php echo htmlspecialchars($nnlStatus['last_poll_at'] ?? 'never'); ?></td></tr>
      <tr><td>Submissions delivered (total):</td><td><?php echo htmlspecialchars($nnlStatus['items_processed_total'] ?? 0); ?></td></tr>
      <tr><td>Last error:</td><td><?php echo htmlspecialchars($nnlStatus['last_error'] ?? '—'); ?></td></tr>
      <tr><td>Plugin version:</td><td><?php echo htmlspecialchars($nnlStatus['plugin_version'] ?? '—'); ?></td></tr>
    </table>
  </fieldset>

  <form method="post" action="<?php echo $actionUrl; ?>">
    <input type="hidden" name="nnl_action" value="save">

    <label for="token">Show token</label>
    <input type="password" id="token" name="token" value="<?php echo htmlspecialchars($nnlSettings['token']); ?>"
           placeholder="nnl_..." autocomplete="off">
    <small>From your NaughtyNice Cloud dashboard, under the show's "Regenerate plugin token" action.</small>

    <label for="cloud_base_url">Cloud service URL</label>
    <input type="text" id="cloud_base_url" name="cloud_base_url" value="<?php echo htmlspecialchars($nnlSettings['cloud_base_url']); ?>"
           placeholder="https://your-domain.example.com">

    <label for="fpp_base_url">Local FPP API URL</label>
    <input type="text" id="fpp_base_url" name="fpp_base_url" value="<?php echo htmlspecialchars($nnlSettings['fpp_base_url']); ?>">
    <small>Leave as http://localhost unless FPP is on a non-default port.</small>

    <label for="poll_interval_seconds">Poll interval (seconds)</label>
    <input type="number" id="poll_interval_seconds" name="poll_interval_seconds" min="5" max="120"
           value="<?php echo htmlspecialchars($nnlSettings['poll_interval_seconds']); ?>">

    <label for="playlist">Playlist to trigger</label>
    <input type="text" id="playlist" name="playlist" value="<?php echo htmlspecialchars($nnlSettings['playlist']); ?>">

    <label for="ticker_model">Ticker overlay model name</label>
    <input type="text" id="ticker_model" name="ticker_model" value="<?php echo htmlspecialchars($nnlSettings['ticker_model']); ?>">

    <label for="matrix_width">Matrix width (px)</label>
    <input type="number" id="matrix_width" name="matrix_width" value="<?php echo htmlspecialchars($nnlSettings['matrix_width']); ?>">

    <label for="photo_zone_height">Photo zone height (px)</label>
    <input type="number" id="photo_zone_height" name="photo_zone_height" value="<?php echo htmlspecialchars($nnlSettings['photo_zone_height']); ?>">

    <label><input type="checkbox" name="enabled" <?php echo $nnlSettings['enabled'] ? 'checked' : ''; ?>> Enabled</label>

    <p>
      <button type="submit">Save settings</button>
    </p>
  </form>

  <form method="post" action="<?php echo $actionUrl; ?>">
    <input type="hidden" name="nnl_action" value="test">
    <button type="submit">Test connection to cloud</button>
  </form>
</div>
