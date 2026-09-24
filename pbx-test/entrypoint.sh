#!/bin/sh
# CAWP test PBX entrypoint (TEST ONLY). Runs as root to render config, then execs Asterisk as
# the unprivileged `asterisk` user (PID 1 is docker's init; see `init: true` in compose).
#
# Every container start is a new Asterisk process and gets a NEW PBX epoch
# (COLLECTIONS_HUMAN_PBX_EPOCH, a UUIDv4) - the same contract the Issabel pre-start hook
# implements (docs/issabel-commissioning.md). Never reuse or bake in an epoch.
set -eu

die() { echo "pbx-test: $*" >&2; exit 64; }

# Secrets come from the environment only (test-only values live in docker-compose.yml).
# A strict character set keeps a value from injecting config lines.
check() {
    name=$1; value=$(printenv "$name" || true)
    [ -n "$value" ] || die "$name is required"
    printf '%s' "$value" | grep -Eq '^[A-Za-z0-9._-]{8,128}$' || die "$name must be 8-128 of [A-Za-z0-9._-]"
}
check PBX_TEST_AMI_SECRET
check PBX_TEST_SIP_PASSWORD
check PBX_TEST_AGI_KEY
AMI_USER=${PBX_TEST_AMI_USER:-cawp-calling}
printf '%s' "$AMI_USER" | grep -Eq '^[a-z][a-z0-9_-]{2,31}$' || die "PBX_TEST_AMI_USER is invalid"
AGI_URL=${PBX_TEST_AGI_URL:-agi://calling:4573/collections-authorize}
printf '%s' "$AGI_URL" | grep -Eq '^agi://[A-Za-z0-9.-]+(:[0-9]{1,5})?/[A-Za-z0-9_-]+$' || die "PBX_TEST_AGI_URL is invalid"
SHORT=${PBX_TEST_SHORT_CALL_SECONDS:-3}
printf '%s' "$SHORT" | grep -Eq '^[0-9]{1,3}$' || die "PBX_TEST_SHORT_CALL_SECONDS is invalid"

# Self-signed certificate for HTTPS/WSS (8089) and AMI over TLS (5039), kept in a volume.
KEYS=/etc/asterisk/keys
if [ ! -s "$KEYS/pbx-test.crt" ] || [ ! -s "$KEYS/pbx-test.key" ]; then
    openssl req -x509 -newkey rsa:2048 -nodes -days 825 -sha256 \
        -subj "/CN=pbx-test/O=CAWP test PBX (not trusted)" \
        -addext "subjectAltName=DNS:pbx-test,DNS:localhost,IP:127.0.0.1" \
        -addext "basicConstraints=critical,CA:TRUE" \
        -keyout "$KEYS/pbx-test.key" -out "$KEYS/pbx-test.crt" 2>/dev/null
fi
chown -R root:asterisk "$KEYS"; chmod 0750 "$KEYS"; chmod 0640 "$KEYS/pbx-test.key"; chmod 0644 "$KEYS/pbx-test.crt"

# Render the configuration (templates use @@NAME@@ placeholders).
for src in /opt/pbx-test/conf/*; do
    dst=/etc/asterisk/$(basename "$src" .in)
    sed -e "s|@@AMI_USER@@|$AMI_USER|g" \
        -e "s|@@AMI_SECRET@@|$PBX_TEST_AMI_SECRET|g" \
        -e "s|@@SIP_PASSWORD@@|$PBX_TEST_SIP_PASSWORD|g" \
        "$src" > "$dst"
done
chown -R root:asterisk /etc/asterisk; chmod 0750 /etc/asterisk; chmod 0640 /etc/asterisk/*.conf

# Protected globals: the PBX epoch (new on every start) and the AGI hook address + key.
mkdir -p /run/asterisk
EPOCH=$(cat /proc/sys/kernel/random/uuid)
printf 'COLLECTIONS_HUMAN_PBX_EPOCH=%s\n' "$EPOCH" > /run/asterisk/collections-pbx-epoch.conf
printf 'COLLECTIONS_AGI_URL=%s\nCOLLECTIONS_AGI_KEY=%s\nPBX_TEST_SHORT_CALL_SECONDS=%s\n' \
    "$AGI_URL" "$PBX_TEST_AGI_KEY" "$SHORT" > /run/asterisk/collections-globals.conf
chown -R asterisk:asterisk /run/asterisk
chmod 0640 /run/asterisk/collections-pbx-epoch.conf /run/asterisk/collections-globals.conf
echo "pbx-test: PBX epoch $EPOCH"

mkdir -p /var/spool/asterisk/monitor /var/log/asterisk/cdr-csv /var/lib/asterisk
chown -R asterisk:asterisk /var/spool/asterisk /var/log/asterisk /var/lib/asterisk

# A real SIP test client: pjsua registers extension 7101 over UDP from 127.0.0.1 and
# auto-answers with silent (null) audio. It retries registration until Asterisk is up.
if [ "${PBX_TEST_PHONE:-1}" = "1" ]; then
    ( sleep 4; sleep 2147483647 | su -s /bin/sh asterisk -c "exec pjsua \
        --id sip:7101@127.0.0.1 --registrar sip:127.0.0.1:5060 --realm '*' \
        --username 7101 --password '$PBX_TEST_SIP_PASSWORD' --reg-timeout 120 \
        --auto-answer 200 --null-audio --no-tcp --local-port 5070 --bound-addr 127.0.0.1 \
        --rtp-port 4000 --max-calls 4 --log-level 2 --app-log-level 2" \
        > /var/log/asterisk/pjsua-7101.log 2>&1 ) &
fi

exec /usr/sbin/asterisk -f -U asterisk -G asterisk -vvv
