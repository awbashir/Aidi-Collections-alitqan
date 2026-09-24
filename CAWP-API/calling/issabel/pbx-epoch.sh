#!/bin/sh
# CAWP PBX epoch pre-start hook (docs/issabel-commissioning.md, step 6).
#
# Writes a NEW UUIDv4 as the Asterisk global COLLECTIONS_HUMAN_PBX_EPOCH before EVERY Asterisk
# process start. Run it from the service supervisor (systemd ExecStartPre, or the init script's
# start function) - never on a dialplan reload or an AMI reconnect. The calling service reads
# it with AMI Getvar and combines it with the process start time, so evidence from a previous
# PBX process is never mixed with the new one.
#
#   install -o root -g root -m 0755 pbx-epoch.sh /usr/local/sbin/cawp-pbx-epoch.sh
#   /usr/local/sbin/cawp-pbx-epoch.sh /etc/asterisk/collections-pbx-epoch.conf
#
# The file has no section header; include it inside [globals] (globals_custom.conf on Issabel).
# It refuses symlinks and files it did not write. Never copy the file between PBXs.
set -eu

OUT=${1:-/etc/asterisk/collections-pbx-epoch.conf}
OWNER=${CAWP_EPOCH_OWNER:-asterisk}
DIR=$(dirname "$OUT")

[ -d "$DIR" ] || { echo "cawp-pbx-epoch: $DIR does not exist" >&2; exit 1; }
if [ -L "$OUT" ]; then echo "cawp-pbx-epoch: refusing symlink $OUT" >&2; exit 1; fi
if [ -e "$OUT" ] && ! grep -Eq '^COLLECTIONS_HUMAN_PBX_EPOCH=[0-9a-f-]{36}$' "$OUT"; then
    echo "cawp-pbx-epoch: $OUT exists and was not written by this hook; not touching it" >&2
    exit 1
fi

UUID=$(cat /proc/sys/kernel/random/uuid)
echo "$UUID" | grep -Eq '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' \
    || { echo "cawp-pbx-epoch: kernel did not return a UUIDv4" >&2; exit 1; }

TMP=$(mktemp "$DIR/.cawp-epoch.XXXXXX")
printf 'COLLECTIONS_HUMAN_PBX_EPOCH=%s\n' "$UUID" > "$TMP"
chown "root:$OWNER" "$TMP" 2>/dev/null || true
chmod 0640 "$TMP"
mv -f "$TMP" "$OUT"
logger -t cawp-pbx-epoch "new PBX epoch $UUID" 2>/dev/null || true
echo "cawp-pbx-epoch: $UUID"
