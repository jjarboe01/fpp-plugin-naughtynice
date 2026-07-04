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

// Two named environments so you can flip between prod and dev for plugin/
// server testing without re-typing tokens each time. Each keeps its own
// base URL + show token; 'environment' picks which one the daemon uses.
// Must stay in sync with ENVIRONMENTS/ENVIRONMENT_DEFAULTS in daemon/config.py.
//
// NOTE: this toggle is manual/human-operated by design. Nothing in this
// codebase should set 'environment' to a different value on its own —
// Joe flips it deliberately on the setup page (content.php) when he wants
// to test against dev vs prod. Don't "helpfully" switch it during
// unrelated changes.
define('NNL_ENVIRONMENT_NAMES', array('prod', 'dev'));

function nnl_default_environments() {
    return array(
        'prod' => array('cloud_base_url' => 'https://naughtynicefpp.com', 'token' => ''),
        'dev' => array('cloud_base_url' => 'https://dev.naughtynicefpp.com', 'token' => ''),
    );
}

function nnl_default_settings() {
    return array(
        'environment' => 'prod',
        'environments' => nnl_default_environments(),
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
    if (!file_exists(NNL_SETTINGS_PATH)) {
        return $settings;
    }

    $raw = json_decode(file_get_contents(NNL_SETTINGS_PATH), true);
    if (!is_array($raw)) {
        return $settings;
    }

    // Migrate pre-environment-toggle settings.json (flat cloud_base_url/
    // token fields, no 'environments' key) into the 'prod' slot the first
    // time this runs against an old file. Whatever was configured before
    // this feature existed was, by definition, the production setup.
    if (!isset($raw['environments']) && (isset($raw['cloud_base_url']) || isset($raw['token']))) {
        if (!empty($raw['cloud_base_url'])) {
            $settings['environments']['prod']['cloud_base_url'] = $raw['cloud_base_url'];
        }
        $settings['environments']['prod']['token'] = isset($raw['token']) ? $raw['token'] : '';
        $settings['environment'] = 'prod';
    }

    $scalarKeys = array('environment', 'fpp_base_url', 'poll_interval_seconds', 'playlist',
                         'ticker_model', 'matrix_width', 'photo_zone_height', 'enabled');
    foreach ($scalarKeys as $key) {
        if (isset($raw[$key])) {
            $settings[$key] = $raw[$key];
        }
    }

    if (isset($raw['environments']) && is_array($raw['environments'])) {
        foreach (NNL_ENVIRONMENT_NAMES as $envName) {
            if (isset($raw['environments'][$envName]) && is_array($raw['environments'][$envName])) {
                $settings['environments'][$envName] = array_merge(
                    $settings['environments'][$envName],
                    array_intersect_key($raw['environments'][$envName], $settings['environments'][$envName])
                );
            }
        }
    }

    if (!in_array($settings['environment'], NNL_ENVIRONMENT_NAMES, true)) {
        $settings['environment'] = 'prod';
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

// The env currently selected for use by the daemon, plus its base URL/token.
function nnl_active_env($settings) {
    $envName = isset($settings['environment']) ? $settings['environment'] : 'prod';
    if (!in_array($envName, NNL_ENVIRONMENT_NAMES, true)) {
        $envName = 'prod';
    }
    $env = isset($settings['environments'][$envName]) ? $settings['environments'][$envName] : array();
    return array(
        'name' => $envName,
        'cloud_base_url' => isset($env['cloud_base_url']) ? $env['cloud_base_url'] : '',
        'token' => isset($env['token']) ? $env['token'] : '',
    );
}

// Quick outbound test of the cloud API using the active environment's token.
// Returns array('ok' => bool, 'message' => string).
function nnl_test_connection($settings) {
    $active = nnl_active_env($settings);
    if (empty($active['token']) || empty($active['cloud_base_url'])) {
        return array('ok' => false, 'message' => "Set the token and cloud URL for the '{$active['name']}' environment first.");
    }
    $url = rtrim($active['cloud_base_url'], '/') . '/api/v1/ping';
    $ch = curl_init($url);
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_HTTPHEADER, array('Authorization: Bearer ' . $active['token']));
    curl_setopt($ch, CURLOPT_TIMEOUT, 10);
    curl_setopt($ch, CURLOPT_SSL_VERIFYPEER, true);
    $body = curl_exec($ch);
    $err = curl_error($ch);
    $code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);

    $envLabel = strtoupper($active['name']);

    if ($body === false) {
        return array('ok' => false, 'message' => "[$envLabel] Connection failed: $err");
    }
    if ($code === 401) {
        return array('ok' => false, 'message' => "[$envLabel] Cloud rejected the token (401) — check it was copied correctly.");
    }
    if ($code !== 200) {
        return array('ok' => false, 'message' => "[$envLabel] Cloud returned HTTP $code: " . substr($body, 0, 200));
    }
    $data = json_decode($body, true);
    $license = isset($data['license']) ? $data['license'] : 'unknown';
    return array('ok' => true, 'message' => "[$envLabel] Connected to {$active['cloud_base_url']}. License status: $license");
}
