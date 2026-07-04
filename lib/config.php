<?php
// Shared settings/status file helpers for this plugin's PHP pages.
// Mirrors daemon/config.py exactly (same file paths, same JSON shape) so
// the PHP UI and the Python daemon can both read/write without stepping
// on each other. Deliberately NOT using FPP's native plugin.<name> ini
// settings file (WriteSettingToFile/parse_ini_file) — that format is
// PHP-oriented and would need re-implementing carefully on the Python
// side to match urlencoding behavior exactly. Plain JSON here is simple,
// atomic (write-then-rename), and equally readable from both languages.

define('NNL_PLUGIN_DIR', dirname(__DIR__));
define('NNL_CONFIG_DIR', NNL_PLUGIN_DIR . '/config');
define('NNL_SETTINGS_PATH', NNL_CONFIG_DIR . '/settings.json');
define('NNL_STATUS_PATH', NNL_CONFIG_DIR . '/status.json');

function nnl_default_settings() {
    return array(
        'token' => '',
        'cloud_base_url' => '',
        'fpp_base_url' => 'http://localhost',
        'poll_interval_seconds' => 10,
        'playlist' => 'breaking_news',
        'ticker_model' => 'TickerZone',
        'matrix_width' => 192,
        'photo_zone_height' => 140,
        'enabled' => true,
    );
}

function nnl_load_settings() {
    $settings = nnl_default_settings();
    if (file_exists(NNL_SETTINGS_PATH)) {
        $raw = json_decode(file_get_contents(NNL_SETTINGS_PATH), true);
        if (is_array($raw)) {
            $settings = array_merge($settings, array_intersect_key($raw, $settings));
        }
    }
    return $settings;
}

function nnl_save_settings($settings) {
    if (!is_dir(NNL_CONFIG_DIR)) {
        mkdir(NNL_CONFIG_DIR, 0755, true);
    }
    $tmp = NNL_SETTINGS_PATH . '.tmp';
    file_put_contents($tmp, json_encode($settings, JSON_PRETTY_PRINT));
    rename($tmp, NNL_SETTINGS_PATH);
}

function nnl_read_status() {
    if (file_exists(NNL_STATUS_PATH)) {
        $raw = json_decode(file_get_contents(NNL_STATUS_PATH), true);
        if (is_array($raw)) {
            return $raw;
        }
    }
    return array();
}

// Quick outbound test of the cloud API using the currently-saved token.
// Returns array('ok' => bool, 'message' => string).
function nnl_test_connection($settings) {
    if (empty($settings['token']) || empty($settings['cloud_base_url'])) {
        return array('ok' => false, 'message' => 'Set both the token and cloud URL first.');
    }
    $url = rtrim($settings['cloud_base_url'], '/') . '/api/v1/ping';
    $ch = curl_init($url);
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_HTTPHEADER, array('Authorization: Bearer ' . $settings['token']));
    curl_setopt($ch, CURLOPT_TIMEOUT, 10);
    curl_setopt($ch, CURLOPT_SSL_VERIFYPEER, true);
    $body = curl_exec($ch);
    $err = curl_error($ch);
    $code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);

    if ($body === false) {
        return array('ok' => false, 'message' => "Connection failed: $err");
    }
    if ($code === 401) {
        return array('ok' => false, 'message' => 'Cloud rejected the token (401) — check it was copied correctly.');
    }
    if ($code !== 200) {
        return array('ok' => false, 'message' => "Cloud returned HTTP $code: " . substr($body, 0, 200));
    }
    $data = json_decode($body, true);
    $license = isset($data['license']) ? $data['license'] : 'unknown';
    return array('ok' => true, 'message' => "Connected. License status: $license");
}
