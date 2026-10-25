<?php
/*
 * CAWP extension provisioning API (port 8092, vhost cawp-provision.conf). Apache admits only
 * 10.0.40.6 with the X-CAWP-Token header; this file runs as the php-fpm user (asterisk) and
 * passes ONE validated operation to the root script through sudo (sudoers-cawp-ext-provision).
 *
 *   GET    /v1/extensions                 list (IssabelPBX extensions + live PJSIP state)
 *   GET    /v1/extensions/<ext>           one extension
 *   POST   /v1/extensions                 {"extension": "141", "name": "..."}   create
 *   POST   /v1/extensions/<ext>/rename    {"name": "..."}                       rename
 *   DELETE /v1/extensions/<ext>                                                  delete
 *   POST   /v1/extensions/<ext>/secret    {}  new SIP password, once (chan_sip script only)
 *   POST   /v1/extensions/<ext>/webrtc    {"enabled": true|false}  in-app softphone on/off (chan_sip script only)
 *   PUT    /v1/sounds/<campaign>-<kind>   message-campaign audio (2026-10-25): an 8 kHz mono 16-bit PCM WAV
 *                                       (<= 4 MB) written as /var/lib/asterisk/sounds/cawp/<name>.wav
 *   DELETE /v1/sounds/<campaign>-<kind>   remove it. Checked and written here (the vhost runs as asterisk),
 *                                       no root script and no sudo.
 *
 * Answers the script's JSON with the HTTP status it names. Install: /var/www/cawp-provision/index.php
 * (root:root 0644).
 *
 * This file (index-php54.php) is index.php for PHP 5.4 (Issabel 4 / CentOS 7 mod_php, e.g. Alitqan):
 * no scalar type hints, no null-coalescing operator. Keep the two in step.
 */
const SCRIPT = '/usr/local/sbin/cawp-ext-provision';

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');

function reply($status, $body) {
    http_response_code($status);
    echo json_encode($body, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    exit;
}

function body() {
    $raw = file_get_contents('php://input', false, null, 0, 4097);
    if ($raw === false || strlen($raw) > 4096) {
        reply(413, array('ok' => false, 'code' => 'body_too_large', 'message' => 'at most 4 KB'));
    }
    $j = json_decode($raw, true);
    if (!is_array($j)) {
        reply(400, ['ok' => false, 'code' => 'invalid_json', 'message' => 'the body must be a JSON object']);
    }
    return $j;
}

function run($args) {
    foreach ($args as $a) {
        if (!is_string($a) || strlen($a) > 400 || strpos($a, "\0") !== false) {
            reply(422, ['ok' => false, 'code' => 'invalid_arguments', 'message' => 'invalid argument']);
        }
    }
    // PHP 5.4: proc_open takes a command string (the array form is 7.4+); every part is quoted
    $cmd = implode(' ', array_map('escapeshellarg', array_merge(array('/usr/bin/sudo', '-n', SCRIPT), $args)));
    $p = proc_open($cmd, array(1 => array('pipe', 'w'), 2 => array('pipe', 'w')), $pipes);
    if (!is_resource($p)) {
        reply(500, ['ok' => false, 'code' => 'exec_failed', 'message' => 'cannot run the provisioning script']);
    }
    $out = stream_get_contents($pipes[1]);
    $err = stream_get_contents($pipes[2]);
    fclose($pipes[1]);
    fclose($pipes[2]);
    proc_close($p);
    $lines = array_values(array_filter(explode("\n", trim((string)$out)), 'strlen'));
    $j = $lines ? json_decode(end($lines), true) : null;
    if (!is_array($j)) {
        error_log('cawp-provision: script gave no JSON: ' . substr((string)$err, -300));
        reply(500, ['ok' => false, 'code' => 'script_failed', 'message' => 'the provisioning script gave no answer']);
    }
    $status = !empty($j['ok']) ? (!empty($j['created']) ? 201 : 200) : (int)(isset($j['http']) ? $j['http'] : 500);
    reply($status >= 200 && $status < 600 ? $status : 500, $j);
}

$method = isset($_SERVER['REQUEST_METHOD']) ? $_SERVER['REQUEST_METHOD'] : 'GET';
$path = parse_url(isset($_SERVER['REQUEST_URI']) ? $_SERVER['REQUEST_URI'] : '/', PHP_URL_PATH);

if ($path === '/v1/extensions') {
    if ($method === 'GET') {
        run(array('list'));
    }
    if ($method === 'POST') {
        $b = body();
        run(array('create', (string)(isset($b['extension']) ? $b['extension'] : ''), (string)(isset($b['name']) ? $b['name'] : '')));
    }
} elseif (preg_match('#^/v1/extensions/([0-9]{2,6})$#', $path, $m)) {
    if ($method === 'GET') {
        run(array('status', $m[1]));
    }
    if ($method === 'DELETE') {
        run(array('delete', $m[1]));
    }
} elseif (preg_match('#^/v1/extensions/([0-9]{2,6})/secret$#', $path, $m) && $method === 'POST') {
    run(array('secret', $m[1]));
} elseif (preg_match('#^/v1/extensions/([0-9]{2,6})/webrtc$#', $path, $m) && $method === 'POST') {
    $b = body();
    run(array('webrtc', $m[1], !empty($b['enabled']) ? 'on' : 'off'));
} elseif (preg_match('#^/v1/extensions/([0-9]{2,6})/rename$#', $path, $m) && $method === 'POST') {
    $b = body();
    run(array('rename', $m[1], (string)(isset($b['name']) ? $b['name'] : '')));
}
if (preg_match('#^/v1/sounds/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}-(message|callback|goodbye))$#', $path, $m)) {
    $dir = '/var/lib/asterisk/sounds/cawp';
    $file = $dir . '/' . $m[1] . '.wav';
    if ($method === 'DELETE') {
        if (is_file($file) && !@unlink($file)) {
            reply(500, array('ok' => false, 'code' => 'delete_failed', 'message' => 'cannot remove the sound'));
        }
        reply(200, array('ok' => true, 'deleted' => $m[1]));
    }
    if ($method === 'PUT') {
        $raw = file_get_contents('php://input', false, null, 0, 4000001);
        if ($raw === false || strlen($raw) > 4000000 || strlen($raw) < 1000) {
            reply(413, array('ok' => false, 'code' => 'bad_size', 'message' => 'a WAV of 1 KB .. 4 MB'));
        }
        // RIFF/WAVE, a 16-byte PCM fmt chunk first: format 1, 1 channel, 8000 Hz, 16 bits
        $h = unpack('a4riff/Vsize/a4wave/a4fmt/Vfmtlen/vformat/vchannels/Vrate/Vbyterate/vblock/vbits', substr($raw, 0, 36));
        if (!$h || $h['riff'] !== 'RIFF' || $h['wave'] !== 'WAVE' || $h['fmt'] !== 'fmt ' || $h['format'] != 1
            || $h['channels'] != 1 || $h['rate'] != 8000 || $h['bits'] != 16) {
            reply(422, array('ok' => false, 'code' => 'bad_wav', 'message' => 'an 8 kHz mono 16-bit PCM WAV is required'));
        }
        if (!is_dir($dir)) {
            reply(500, array('ok' => false, 'code' => 'no_sound_dir', 'message' => 'the sound directory is missing'));
        }
        $tmp = $file . '.tmp' . getmypid();
        if (@file_put_contents($tmp, $raw) !== strlen($raw) || !@chmod($tmp, 0640) || !@rename($tmp, $file)) {
            @unlink($tmp);
            reply(500, array('ok' => false, 'code' => 'write_failed', 'message' => 'cannot write the sound'));
        }
        reply(200, array('ok' => true, 'installed' => $m[1], 'bytes' => strlen($raw)));
    }
}
reply(404, ['ok' => false, 'code' => 'no_route', 'message' => 'unknown route']);
