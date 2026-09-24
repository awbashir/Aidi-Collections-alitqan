#!/bin/bash
# CAWP read-only commissioning checks ON the Issabel/Asterisk host (docs/issabel-commissioning.md).
# Changes nothing. Prints PASS / WARN / FAIL per check; exit 1 if anything FAILed.
#
#   sudo ./pbx-verify.sh --ami-user cawp-calling --extension 7001 --calling-host 10.0.0.5 \
#        --trunk my-trunk --monitor /var/spool/asterisk/monitor
set -u
AMI_USER=cawp-calling; EXT=""; CALLING_HOST=""; AGI_PORT=4573; TRUNK=""; MONITOR=/var/spool/asterisk/monitor
CONTEXT=collections-human-outbound; CERT=""
while [ $# -gt 0 ]; do
    case "$1" in
        --ami-user) AMI_USER=$2; shift 2;;
        --extension) EXT=$2; shift 2;;
        --calling-host) CALLING_HOST=$2; shift 2;;
        --agi-port) AGI_PORT=$2; shift 2;;
        --trunk) TRUNK=$2; shift 2;;
        --monitor) MONITOR=$2; shift 2;;
        --context) CONTEXT=$2; shift 2;;
        --cert) CERT=$2; shift 2;;
        *) echo "unknown option $1" >&2; exit 2;;
    esac
done
FAILED=0
pass() { printf 'PASS  %-12s %s\n' "$1" "$2"; }
warn() { printf 'WARN  %-12s %s\n' "$1" "$2"; }
fail() { printf 'FAIL  %-12s %s\n' "$1" "$2"; FAILED=1; }
ax() { asterisk -rx "$1" 2>/dev/null; }

command -v asterisk >/dev/null || { echo "asterisk not found (run this on the PBX)"; exit 2; }

# 1. version and channel driver
VER=$(ax "core show version" | head -1)
case "$VER" in
    *"Asterisk 1[3-9]"*|*"Asterisk 2"*) pass version "$VER";;
    *"Asterisk 1"[0-2]*) warn version "$VER (old: PJSIP WebRTC needs 13+, webrtc=yes needs 16+)";;
    *) warn version "${VER:-unknown}";;
esac
PJSIP=$(ax "module show like chan_pjsip" | grep -c "chan_pjsip.so")
SIP=$(ax "module show like chan_sip" | grep -c "chan_sip.so")
[ "$PJSIP" -gt 0 ] && pass driver "chan_pjsip loaded"
[ "$SIP" -gt 0 ] && warn driver "chan_sip loaded (deprecated; the runbook has the chan_sip WebRTC variant)"
[ "$PJSIP" -eq 0 ] && [ "$SIP" -eq 0 ] && fail driver "no SIP channel driver loaded"
for m in res_http_websocket res_srtp res_agi app_mixmonitor func_pjsip_endpoint app_userevent; do
    if ax "module show like $m" | grep -q "$m.so"; then pass module "$m"; else
        case $m in func_pjsip_endpoint) [ "$PJSIP" -gt 0 ] && fail module "$m missing" || warn module "$m (PJSIP only)";;
                   *) fail module "$m missing";; esac
    fi
done
[ "$PJSIP" -gt 0 ] && { ax "module show like res_pjsip_transport_websocket" | grep -q websocket \
    && pass module res_pjsip_transport_websocket || fail module "res_pjsip_transport_websocket missing"; }

# 2. AMI account and manager settings
USERINFO=$(ax "manager show user $AMI_USER")
if echo "$USERINFO" | grep -q "username: $AMI_USER"; then
    R=$(echo "$USERINFO" | awk -F': ' '/read perm/ {print $2}')
    W=$(echo "$USERINFO" | awk -F': ' '/write perm/ {print $2}')
    echo "$W" | grep -Eq '(^|,)(system|command|config|all|call|agent|user|dtmf)(,|$)' \
        && fail ami-write "write = $W (must be originate,reporting)" || pass ami-write "write = $W"
    echo "$R" | grep -q call && echo "$R" | grep -q user && echo "$R" | grep -q system \
        && pass ami-read "read = $R" || fail ami-read "read = $R (needs call,user,system)"
    echo "$USERINFO" | grep -qi "acl: yes\|acl.*yes\|permit" && pass ami-acl "ACL present" \
        || warn ami-acl "no ACL shown: add deny=0.0.0.0/0.0.0.0 + permit=<calling host>/32"
else
    fail ami-user "manager user $AMI_USER not found"
fi
SETTINGS=$(ax "manager show settings")
echo "$SETTINGS" | grep -Ei "timestamp events" | grep -qi yes && pass ami-events "timestampevents=yes" \
    || fail ami-events "timestampevents is not yes"
echo "$SETTINGS" | grep -Ei "tls enable" | grep -qi yes && pass ami-tls "AMI TLS enabled" \
    || warn ami-tls "AMI TLS disabled (use TLS 5039 or a loopback tunnel)"

# 3. HTTPS/WSS for WebRTC
HTTP=$(ax "http show status")
echo "$HTTP" | grep -q "HTTPS Server Enabled" && pass https "$(echo "$HTTP" | grep 'HTTPS Server')" \
    || fail https "HTTPS (TLS) HTTP server not enabled"
echo "$HTTP" | grep -q "/ws" && pass wss "/ws WebSocket URI enabled" || fail wss "/ws not enabled"
if [ -n "$CERT" ] && [ -r "$CERT" ]; then
    END=$(openssl x509 -enddate -noout -in "$CERT" | cut -d= -f2)
    if openssl x509 -checkend 2592000 -noout -in "$CERT" >/dev/null; then pass cert "valid until $END"
    else fail cert "expires within 30 days ($END)"; fi
fi

# 4. globals: epoch, AGI hook
G=$(ax "dialplan show globals")
EP=$(echo "$G" | sed -n 's/.*COLLECTIONS_HUMAN_PBX_EPOCH=\([0-9a-f-]*\).*/\1/p' | head -1)
echo "$EP" | grep -Eq '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' \
    && pass epoch "$EP" || fail epoch "COLLECTIONS_HUMAN_PBX_EPOCH missing (install pbx-epoch.sh as a pre-start hook)"
AGIURL=$(echo "$G" | sed -n 's/.*COLLECTIONS_AGI_URL=\([^ ]*\).*/\1/p' | head -1)
echo "$AGIURL" | grep -Eq '^agi://' && pass agi-url "$AGIURL" || fail agi-url "COLLECTIONS_AGI_URL missing"
echo "$G" | grep -q "COLLECTIONS_AGI_KEY=." && pass agi-key "set (not shown)" || fail agi-key "COLLECTIONS_AGI_KEY missing"
if [ -n "$CALLING_HOST" ]; then
    if timeout 3 bash -c "</dev/tcp/$CALLING_HOST/$AGI_PORT" 2>/dev/null; then
        pass agi-reach "$CALLING_HOST:$AGI_PORT reachable from the PBX"
    else fail agi-reach "$CALLING_HOST:$AGI_PORT NOT reachable from the PBX"; fi
fi

# 5. the restricted dialplan
DP=$(ax "dialplan show $CONTEXT")
if echo "$DP" | grep -q "AGI(\${GLOBAL(COLLECTIONS_AGI_URL)})"; then pass dialplan "$CONTEXT with the AGI hook"
else fail dialplan "$CONTEXT missing or without AGI(\${GLOBAL(COLLECTIONS_AGI_URL)})"; fi
[ -n "$TRUNK" ] && { echo "$DP" | grep -Eq "Dial\((PJSIP/\\\$\{COLLECTIONS_GATE_DESTINATION\}@$TRUNK|SIP/$TRUNK/)" \
    && pass trunk "Dial uses $TRUNK" || fail trunk "Dial does not use the fixed trunk $TRUNK"; }
INCL=$(grep -Rls "include *=> *$CONTEXT" /etc/asterisk 2>/dev/null)
[ -z "$INCL" ] && pass isolation "$CONTEXT is not included in any other context" \
    || fail isolation "$CONTEXT is included from: $INCL"

# 6. the WebRTC test extension
if [ -n "$EXT" ]; then
    if [ "$PJSIP" -gt 0 ] && ax "pjsip show endpoint $EXT" | grep -q "Endpoint:"; then
        E=$(ax "pjsip show endpoint $EXT")
        # webrtc=yes is a shorthand; check it and what it expands to
        for k in "webrtc *: *(yes|true)" "allow_transfer *: *(false|no)" "media_encryption *: *dtls" \
                 "ice_support *: *(true|yes)" "use_avpf *: *(true|yes)" "rtcp_mux *: *(true|yes)" \
                 "dtls_verify *: *(yes|fingerprint)" "media_use_received_transport *: *(true|yes)"; do
            echo "$E" | grep -Eqi "$k" && pass endpoint "$EXT $k" || fail endpoint "$EXT not '$k'"
        done
        echo "$E" | grep -Eq "allow *: *\(.*(ulaw|alaw|opus)" && pass codecs "$(echo "$E" | grep -E ' allow ' | head -1 | xargs)" \
            || warn codecs "check that ulaw/alaw (or opus with codec_opus) is allowed"
    elif [ "$SIP" -gt 0 ]; then
        S=$(ax "sip show peer $EXT")
        for k in "Transport.*WS" "AVPF *: *Yes" "ICE Support *: *Yes"; do
            echo "$S" | grep -Eqi "$k" && pass peer "$EXT $k" || fail peer "$EXT not '$k'"
        done
    else fail endpoint "$EXT not found"; fi
fi

# 7. recordings and CDR
if [ -d "$MONITOR" ]; then
    OWN=$(stat -c %U "$MONITOR"); FREE=$(df -Pk "$MONITOR" | awk 'NR==2 {print int($4/1024/1024)}')
    [ "$OWN" = asterisk ] && pass monitor "$MONITOR owner asterisk, ${FREE} GiB free" \
        || fail monitor "$MONITOR owner is $OWN (MixMonitor runs as asterisk)"
    [ "${FREE:-0}" -lt 10 ] && warn monitor "less than 10 GiB free"
else fail monitor "$MONITOR does not exist (RECORDINGS_ROOT on the API must match)"; fi
CDR=$(ax "cdr show status")
echo "$CDR" | grep -Eqi "mysql|odbc|adaptive" && pass cdr "$(echo "$CDR" | grep -Ei 'mysql|odbc' | head -1 | xargs)" \
    || warn cdr "no MySQL/ODBC CDR backend listed (the API's /cdr reads asteriskcdrdb.cdr)"

# 8. firewall hints (read-only)
if command -v iptables >/dev/null; then
    RULES=$(iptables -S 2>/dev/null)
    for p in 5039 8089 "$AGI_PORT"; do
        echo "$RULES" | grep -q -- "--dport $p" && pass firewall "a rule mentions port $p" \
            || warn firewall "no iptables rule mentions port $p (check the runbook's firewall table)"
    done
fi
exit $FAILED
