#!/bin/sh
# CAWP WebRTC test PBX entrypoint (TEST ONLY). Renders the configuration with secrets from the
# environment (test-only values in docker-compose.yml), then runs Asterisk as `asterisk`.
set -eu

die() { echo "pbx-webrtc-test: $*" >&2; exit 64; }

# A strict character set keeps a value from injecting configuration lines.
check() {
    name=$1; value=$(printenv "$name" || true)
    [ -n "$value" ] || die "$name is required"
    printf '%s' "$value" | grep -Eq '^[A-Za-z0-9._-]{8,128}$' || die "$name must be 8-128 of [A-Za-z0-9._-]"
}
check PBX_WEBRTC_AMI_SECRET
check PBX_WEBRTC_DB_PASSWORD
AMI_USER=${PBX_WEBRTC_AMI_USER:-cawp-webrtc-test}
DB_HOST=${PBX_WEBRTC_DB_HOST:-test-db}
DB_NAME=${PBX_WEBRTC_DB_NAME:-cawp_test_call3_e2e}
DB_USER=${PBX_WEBRTC_DB_USER:-cawp_pbx_realtime}
REALM=${PBX_WEBRTC_REALM:-cawp-webrtc-test}
printf '%s' "$AMI_USER" | grep -Eq '^[a-z][a-z0-9_-]{2,31}$' || die "PBX_WEBRTC_AMI_USER is invalid"
printf '%s' "$DB_HOST" | grep -Eq '^[A-Za-z0-9.-]{1,253}$' || die "PBX_WEBRTC_DB_HOST is invalid"
printf '%s' "$DB_NAME" | grep -Eq '^[a-z][a-z0-9_]{2,62}$' || die "PBX_WEBRTC_DB_NAME is invalid"
printf '%s' "$DB_USER" | grep -Eq '^[a-z][a-z0-9_]{2,62}$' || die "PBX_WEBRTC_DB_USER is invalid"
printf '%s' "$REALM" | grep -Eq '^[A-Za-z0-9._-]{1,64}$' || die "PBX_WEBRTC_REALM is invalid"

# The fallback ("locked") auth objects used when the realtime view has no current credential for
# an extension: an HA1 of a random value nobody knows, new on every start, never printed.
LOCKED_HA1=$(head -c 32 /dev/urandom | md5sum | cut -c1-32)

# Self-signed certificate for HTTPS/WSS (8089), kept in a volume. TEST ONLY.
KEYS=/etc/asterisk/keys
if [ ! -s "$KEYS/pbx-webrtc-test.crt" ] || [ ! -s "$KEYS/pbx-webrtc-test.key" ]; then
    openssl req -x509 -newkey rsa:2048 -nodes -days 825 -sha256 \
        -subj "/CN=pbx-webrtc-test/O=CAWP WebRTC test PBX (not trusted)" \
        -addext "subjectAltName=DNS:pbx-webrtc-test,DNS:localhost,IP:127.0.0.1" \
        -keyout "$KEYS/pbx-webrtc-test.key" -out "$KEYS/pbx-webrtc-test.crt" 2>/dev/null
fi
chown -R root:asterisk "$KEYS"; chmod 0750 "$KEYS"
chmod 0640 "$KEYS/pbx-webrtc-test.key"; chmod 0644 "$KEYS/pbx-webrtc-test.crt"

for src in /opt/pbx-webrtc-test/conf/*; do
    dst=/etc/asterisk/$(basename "$src" .in)
    sed -e "s|@@AMI_USER@@|$AMI_USER|g" \
        -e "s|@@AMI_SECRET@@|$PBX_WEBRTC_AMI_SECRET|g" \
        -e "s|@@DB_HOST@@|$DB_HOST|g" \
        -e "s|@@DB_NAME@@|$DB_NAME|g" \
        -e "s|@@DB_USER@@|$DB_USER|g" \
        -e "s|@@DB_PASSWORD@@|$PBX_WEBRTC_DB_PASSWORD|g" \
        -e "s|@@REALM@@|$REALM|g" \
        -e "s|@@LOCKED_HA1@@|$LOCKED_HA1|g" \
        "$src" > "$dst"
done
chown -R root:asterisk /etc/asterisk; chmod 0750 /etc/asterisk; chmod 0640 /etc/asterisk/*.conf

mkdir -p /run/asterisk /var/spool/asterisk /var/log/asterisk /var/lib/asterisk
chown -R asterisk:asterisk /run/asterisk /var/spool/asterisk /var/log/asterisk /var/lib/asterisk

echo "pbx-webrtc-test: realtime auths from $DB_USER@$DB_HOST/$DB_NAME (view pbx_ps_auths), realm $REALM"
exec /usr/sbin/asterisk -f -U asterisk -G asterisk -vvv
