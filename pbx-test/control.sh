#!/bin/sh
# CAWP test PBX control port (TEST ONLY; never part of the Issabel runbook).
# One command per TCP connection on the compose network (busybox `nc -lk -p 5099 -e`):
#     "<PBX_TEST_CONTROL_KEY> restart"   -> "ok <new epoch uuid>"   (graceful: channels are hung up)
#     "<PBX_TEST_CONTROL_KEY> crash"     -> "ok crashed"            (SIGKILL; restarted with a new epoch)
# `restart` does what the Issabel pre-start hook + a process restart do: it writes a NEW PBX
# epoch UUID, then restarts Asterisk in place (`core restart now`: every channel is hung up,
# every AMI session is closed, the process start time changes). The live click-to-call tests
# use it to prove "a PBX restart mid-call -> uncertain -> reconciled".
set -u
IFS= read -r line || exit 0
line=$(printf '%s' "$line" | tr -d '\r')
key=${line%% *}
cmd=${line#* }
if [ -z "${PBX_TEST_CONTROL_KEY:-}" ] || [ "$key" != "$PBX_TEST_CONTROL_KEY" ]; then
    echo "denied"
    exit 0
fi
case "$cmd" in
    restart)
        EPOCH=$(cat /proc/sys/kernel/random/uuid)
        printf 'COLLECTIONS_HUMAN_PBX_EPOCH=%s\n' "$EPOCH" > /run/asterisk/collections-pbx-epoch.conf.new
        chown asterisk:asterisk /run/asterisk/collections-pbx-epoch.conf.new
        chmod 0640 /run/asterisk/collections-pbx-epoch.conf.new
        mv -f /run/asterisk/collections-pbx-epoch.conf.new /run/asterisk/collections-pbx-epoch.conf
        echo "pbx-test: control restart, new PBX epoch $EPOCH" >&2
        asterisk -rx "core restart now" >/dev/null 2>&1
        echo "ok $EPOCH"
        ;;
    crash)
        # A CRASH, not a shutdown: SIGKILL gives Asterisk no chance to hang up channels or to
        # tell AMI clients anything. The entrypoint's supervisor loop starts a new process with
        # a new epoch (like safe_asterisk + the pre-start hook on Issabel).
        pid=$(cat /run/pbx-test/asterisk.pid 2>/dev/null || true)
        [ -n "$pid" ] && kill -KILL "$pid"
        echo "ok crashed"
        ;;
    *)
        echo "unknown"
        ;;
esac
