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
 *
 * Answers the script's JSON with the HTTP status it names. Install: /var/www/cawp-provision/index.php
 * (root:root 0644).
 *
 * index-php54.php is the same file for PHP 5.4 (Issabel 4 / CentOS 7 mod_php, e.g. Alitqan):
 * no scalar type hints, no null-coalescing operator. Keep the two in step.
 */
const SCRIPT = '/usr/local/sbin/cawp-ext-provision';

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');

function reply(int $status, array $body) {
    http_response_code($status);
    echo json_encode($body, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    exit;
}

function body(): array {
    $raw = file_get_contents('php://input', false, null, 0, 4097);
    if ($raw === false || strlen($raw) > 4096) {
        reply(413, ['ok' => false, 'code' => 'body_too_large', 'message' => 'at most 4 KB']);
    }
    $j = json_decode($raw, true);
    if (!is_array($j)) {
        reply(400, ['ok' => false, 'code' => 'invalid_json', 'message' => 'the body must be a JSON object']);
    }
    return $j;
}

function run(array $args) {
    foreach ($args as $a) {
        if (!is_string($a) || strlen($a) > 400 || strpos($a, "\0") !== false) {
            reply(422, ['ok' => false, 'code' => 'invalid_arguments', 'message' => 'invalid argument']);
        }
    }
    $p = proc_open(array_merge(['/usr/bin/sudo', '-n', SCRIPT], $args), [1 => ['pipe', 'w'], 2 => ['pipe', 'w']], $pipes);
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
    $status = !empty($j['ok']) ? (!empty($j['created']) ? 201 : 200) : (int)($j['http'] ?? 500);
    reply($status >= 200 && $status < 600 ? $status : 500, $j);
}

$method = $_SERVER['REQUEST_METHOD'] ?? 'GET';
$path = parse_url($_SERVER['REQUEST_URI'] ?? '/', PHP_URL_PATH);

if ($path === '/v1/extensions') {
    if ($method === 'GET') {
        run(['list']);
    }
    if ($method === 'POST') {
        $b = body();
        run(['create', (string)($b['extension'] ?? ''), (string)($b['name'] ?? '')]);
    }
} elseif (preg_match('#^/v1/extensions/([0-9]{2,6})$#', $path, $m)) {
    if ($method === 'GET') {
        run(['status', $m[1]]);
    }
    if ($method === 'DELETE') {
        run(['delete', $m[1]]);
    }
} elseif (preg_match('#^/v1/extensions/([0-9]{2,6})/secret$#', $path, $m) && $method === 'POST') {
    run(['secret', $m[1]]);
} elseif (preg_match('#^/v1/extensions/([0-9]{2,6})/webrtc$#', $path, $m) && $method === 'POST') {
    $b = body();
    run(['webrtc', $m[1], !empty($b['enabled']) ? 'on' : 'off']);
} elseif (preg_match('#^/v1/extensions/([0-9]{2,6})/rename$#', $path, $m) && $method === 'POST') {
    $b = body();
    run(['rename', $m[1], (string)($b['name'] ?? '')]);
}
reply(404, ['ok' => false, 'code' => 'no_route', 'message' => 'unknown route']);
