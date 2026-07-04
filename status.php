<?php
require_once __DIR__ . '/lib/config.php';
$nnlStatus = nnl_read_status();
$nnlSettings = nnl_load_settings();
?>
<style>
  .nnl-status-table td { padding: 3px 12px 3px 0; }
</style>
<div style="max-width: 560px;">
  <h3>NaughtyNice Cloud — Status</h3>
  <table class="nnl-status-table">
    <tr><td>Enabled:</td><td><?php echo $nnlSettings['enabled'] ? 'yes' : 'no'; ?></td></tr>
    <tr><td>License:</td><td><strong><?php echo htmlspecialchars($nnlStatus['license'] ?? 'unknown — daemon hasn\'t polled yet'); ?></strong></td></tr>
    <tr><td>License expires:</td><td><?php echo htmlspecialchars($nnlStatus['license_expires'] ?? '—'); ?></td></tr>
    <tr><td>Last poll:</td><td><?php echo htmlspecialchars($nnlStatus['last_poll_at'] ?? 'never'); ?></td></tr>
    <tr><td>Submissions delivered (total):</td><td><?php echo htmlspecialchars($nnlStatus['items_processed_total'] ?? 0); ?></td></tr>
    <tr><td>Last error:</td><td><?php echo htmlspecialchars($nnlStatus['last_error'] ?? '—'); ?></td></tr>
    <tr><td>Plugin version:</td><td><?php echo htmlspecialchars($nnlStatus['plugin_version'] ?? '—'); ?></td></tr>
  </table>
  <p><a href="plugin.php?plugin=fpp-plugin-naughtynice&page=content.php">Go to setup page</a></p>
</div>
