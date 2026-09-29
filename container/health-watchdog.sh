#!/bin/sh
# THE APP'S HEALTHCHECK, WITH A WATCHDOG IN IT.
#
# Docker marks a container `unhealthy` and then does nothing: `restart: unless-stopped`
# fires only when the process exits. A JVM that has stopped answering but not exited —
# heap exhausted, or wedged — therefore sat unhealthy until a person noticed (2026-09-29:
# about 25 minutes, the console showing "still starting" the whole time). This script is
# the healthcheck Docker already runs, so it needs no Docker socket and no extra container:
# after APP_WATCHDOG_FAILURES consecutive probes with NO ANSWER AT ALL from a process that
# has answered before, it ends that process and the restart policy brings it back.
#
# Three rules keep it from making things worse:
#
#   * NO ANSWER, NOT A BAD ANSWER. /actuator/health answers 503 when a dependency (the
#     graph) is down. The app is alive then, and restarting it fixes nothing, so any HTTP
#     status resets the count. Only a timeout, a refused connection or an empty reply counts.
#   * NEVER A START. A process that has not answered once since it started is never ended,
#     however long it takes — first boot loads realms and can be slow. The count is kept
#     per process (its start time), so a restart begins again from nothing.
#   * TERM, THEN KILL. SIGTERM first, so a JVM that can still shut down does; SIGKILL after
#     APP_WATCHDOG_GRACE_SECONDS for one that cannot. This needs `init: true` on the service:
#     PID 1 ignores a SIGKILL sent from inside its own container, so the JVM must not be PID 1.
#
# The exit status is the plain health result either way, so `docker ps` still says
# healthy or unhealthy exactly as before.

url="http://localhost:${SERVER_PORT:-4242}/actuator/health"
limit="${APP_WATCHDOG_FAILURES:-5}"
grace="${APP_WATCHDOG_GRACE_SECONDS:-20}"
probe_timeout="${APP_WATCHDOG_PROBE_SECONDS:-4}"
state=/tmp/embabel-health-watchdog

say() { echo "[health-watchdog] $*" > /proc/1/fd/1 2>/dev/null; }

# Anchored on `java`: with `init: true`, PID 1 is docker-init, whose own command line
# (`docker-init -- sh -c exec java ... assistant.jar`) would otherwise match first.
pid=$(pgrep -o -f '^java .*assistant\.jar')
[ -n "$pid" ] || exit 1
# Field 22 of /proc/<pid>/stat: when this process started. The command name (field 2)
# is "(java)", which has no spaces, so plain field splitting is safe.
started=$(cut -d' ' -f22 "/proc/$pid/stat" 2>/dev/null)

code=$(curl -s -o /dev/null -w '%{http_code}' --max-time "$probe_timeout" "$url" 2>/dev/null)
[ -n "$code" ] || code=000

seen=0
failures=0
if [ -f "$state" ]; then
    read -r was_started was_seen was_failures < "$state"
    if [ "$was_started" = "$started" ]; then
        seen=${was_seen:-0}
        failures=${was_failures:-0}
    fi
fi

if [ "$code" != 000 ]; then
    echo "$started 1 0" > "$state"
    [ "$code" = 200 ] && exit 0
    exit 1
fi

failures=$((failures + 1))
echo "$started $seen $failures" > "$state"

# Every LIMIT failures, not every failure past it: one signal and one escalation per round.
if [ "$limit" -gt 0 ] && [ "$seen" = 1 ] && [ $((failures % limit)) -eq 0 ]; then
    say "no answer from $url for $failures consecutive checks; ending pid $pid so the container restarts"
    kill -TERM "$pid" 2>/dev/null
    # Detached, so the escalation outlives this check's own time limit.
    setsid sh -c "sleep $grace; kill -0 $pid 2>/dev/null && kill -KILL $pid" > /dev/null 2>&1 &
fi
exit 1
