<?php
require_once __DIR__ . '/lib/config.php';
$nnlStatus = nnl_read_status();
$nnlSettings = nnl_load_settings();
$nnlActive = nnl_active_env($nnlSettings);
?>
<style>
  .nnl-status-table td { padding: 3px 12px 3px 0; }
  .nnl-env-banner {
    display: inline-block; padding: 4px 12px; border-radius: 4px; font-weight: bold;
    margin-bottom: 10px; letter-spacing: 0.5px;
  }
  .nnl-env-banner.nnl-env-prod { background: #1a5d1a; color: #fff; }
  .nnl-env-banner.nnl-env-dev { background: #b35c00; color: #fff; }
</style>
<div style="max-width: 560px;">
  <h3>NaughtyNice Cloud — Status</h3>
  <div class="nnl-env-banner nnl-env-<?php echo htmlspecialchars($nnlActive['name']); ?>">
    ACTIVE ENVIRONMENT: <?php echo htmlspecialchars(strtoupper($nnlActive['name'])); ?>
    (<?php echo htmlspecialchars($nnlActive['cloud_base_url'] ?: 'no URL set'); ?>)
  </div>
  <table class="nnl-status-table">
    <tr><td>Enabled:</td><td><?php echo $nnlSettings['enabled'] ? 'yes' : 'no'; ?></td></tr>
    <tr><td>Plan:</td><td><?php echo htmlspecialchars($nnlStatus['plan'] ?? '—'); ?></td></tr>
      <tr><td>License:</td><td><strong><?php echo htmlspecialchars($nnlStatus['license'] ?? 'unknown — daemon hasn\'t polled yet'); ?></strong></td></tr>
    <tr><td>License expires:</td><td><?php echo htmlspecialchars($nnlStatus['license_expires'] ?? '—'); ?></td></tr>
    <tr><td>Last poll:</td><td><?php echo htmlspecialchars($nnlStatus['last_poll_at'] ?? 'never'); ?></td></tr>
    <tr><td>Submissions delivered (total):</td><td><?php echo htmlspecialchars($nnlStatus['items_processed_total'] ?? 0); ?></td></tr>
    <tr><td>Last error:</td><td><?php echo htmlspecialchars($nnlStatus['last_error'] ?? '—'); ?></td></tr>
    <tr><td>Plugin version:</td><td><?php echo htmlspecialchars($nnlStatus['plugin_version'] ?? '—'); ?></td></tr>
  </table>
  <p><a href="plugin.php?plugin=fpp-plugin-naughtynice&page=content.php">Go to setup page</a></p>
</div>
