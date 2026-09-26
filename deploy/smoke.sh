#!/usr/bin/env bash
# The compose stack end to end on a throwaway project (STORY-013 AC-4): build, up, health through caddy, the
# synthetic book uploaded → review → plan → cut → a zip with 3 PDFs; then what the gate asked for (STORY-013
# addendum + gates r1/r2): the api has no egress (and a WARNING when the host itself answers at the backend
# gateway), an upload flood is refused before a byte of body is read, the in-flight cap holds, spoofed
# X-Forwarded-For straight at the api is ignored, an IPv6 client is counted under its /64, a trickling client
# can't pin the slots and held-open PUTs can't take the api down; delete, `down -v`. Exit 0 only when every step
# passed AND nothing of the project is left behind. Needs docker compose, curl and the repo's dev environment
# (uv) for the fixture book and the hashes.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/.." && pwd)
PROJECT=${SMOKE_PROJECT:-pdfsplit-smoke}

# Everything the base file interpolates is pinned to throwaway values, so a run never touches a real stack on
# the same host: its own image tag, high host ports, its own subnets (v4 and v6) and a random salt.
export PDFSPLIT_TAG=smoke
export PDFSPLIT_HTTP_PORT=${SMOKE_HTTP_PORT:-18080}
export PDFSPLIT_HTTPS_PORT=${SMOKE_HTTPS_PORT:-18443}
export PDFSPLIT_SUBNET=${SMOKE_SUBNET:-172.27.13.0/24}
export PDFSPLIT_SUBNET6=${SMOKE_SUBNET6:-fd27:5eaf:9a13::/64}
export PDFSPLIT_CADDY_IP=${SMOKE_CADDY_IP:-172.27.13.10}
export PDFSPLIT_CADDY_IP6=${SMOKE_CADDY_IP6:-fd27:5eaf:9a13::10}
export PDFSPLIT_EDGE_SUBNET=${SMOKE_EDGE_SUBNET:-172.27.14.0/24}
export PDFSPLIT_EDGE_SUBNET6=${SMOKE_EDGE_SUBNET6:-fd27:5eaf:9a13:1::/64}
export PDFSPLIT_IP_SALT="smoke-$RANDOM$RANDOM$RANDOM"
export PDFSPLIT_RATE_PER_HOUR=6
export PUBLIC_HOST=localhost
export PDFSPLIT_PUBLIC_URL="https://localhost:$PDFSPLIT_HTTPS_PORT"
# The one-off clients' fixed addresses: one on `backend` (straight at the api), one on `edge` (through caddy
# over IPv6). The rate budget below is per address, so the whole run is planned around them.
CLIENT4="${PDFSPLIT_SUBNET%.0/24}.77"
CLIENT6="${PDFSPLIT_EDGE_SUBNET6%/64}77"
EDGE_GW6="${PDFSPLIT_EDGE_SUBNET6%/64}1"
EDGE_GW4="${PDFSPLIT_EDGE_SUBNET%.0/24}.1"
BACKEND_GW4="${PDFSPLIT_SUBNET%.0/24}.1"
BACKEND_GW6="${PDFSPLIT_SUBNET6%/64}1"
# Host ports the api is asked to reach at the backend gateway (gate r2): sshd is the one every VM has; add what
# this host binds to 0.0.0.0/[::] to see the warning fire.
HOST_PORTS=${SMOKE_HOST_PORTS:-22}
# Two more addresses on `backend` for the slow-body checks: the caps are per client, so the trickler must not
# be CLIENT4 (whose hour is spent by then) and the client it must not lock out must be a third.
TRICKLER="${PDFSPLIT_SUBNET%.0/24}.78"
OTHER="${PDFSPLIT_SUBNET%.0/24}.79"

BASE="https://localhost:$PDFSPLIT_HTTPS_PORT"
# Not /tmp: it is RAM-backed on some hosts. Readable by the one-off clients, which run as the image's uid 10001.
WORK=$(mktemp -d -p /var/tmp pdfsplit-smoke.XXXXXX)
chmod 755 "$WORK"
MiB=1048576

compose() { docker compose -p "$PROJECT" -f "$HERE/compose.yaml" "$@"; }
py() { uv run --quiet --project "$ROOT" python "$@"; }
field() { py -c 'import json,sys; v=json.load(open(sys.argv[1]))[sys.argv[2]]; print("" if v is None else v)' "$1" "$2"; }
say() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*"; }
step() { say "  ok  $*"; }
fail() { say "FAIL  $*"; exit 1; }
# Status code on stdout, body in $2, any curl options after. -k: the localhost certificate is caddy's own CA's.
req() { curl -ksS -o "$2" -w '%{http_code}' "${@:3}" "$1" 2>/dev/null || true; }
# `grep -v` exits 1 when nothing survives it (a host running nothing else), which is not a failure here.
others() { docker ps -a --format '{{.Names}}' | { grep -v "^$PROJECT-" || true; } | sort; }
# A one-off client on one of the stack's networks, with a fixed address (deploy/smoke_client.py).
client() {
  local net=$1 ip4=$2 ip6=$3
  shift 3
  # A name per run, not per script: several clients run at once, from background subshells — whose $RANDOM
  # streams are copies of each other, hence the subshell's own pid in the name.
  docker run --rm --name "$PROJECT-client-$BASHPID-$RANDOM" --network "${PROJECT}_$net" --ip "$ip4" --ip6 "$ip6" \
    -v "$WORK:/w:ro" -v "$HERE/smoke_client.py:/client.py:ro" --entrypoint python "pdfsplit-app:$PDFSPLIT_TAG" /client.py "$@"
}
backend() { client backend "$CLIENT4" "${PDFSPLIT_SUBNET6%/64}77" "$@"; }
edge() { client edge "${PDFSPLIT_EDGE_SUBNET%.0/24}.77" "$CLIENT6" "$@"; }
trickler() { client backend "$TRICKLER" "${PDFSPLIT_SUBNET6%/64}78" "$@"; }
other() { client backend "$OTHER" "${PDFSPLIT_SUBNET6%/64}79" "$@"; }
# The `rate` table, read inside the running api (sqlite on the volume): counts, and the hashes newest-last.
rate_rows() { compose exec -T api python -c 'import sqlite3; print(*sqlite3.connect("/jobs/jobs.db").execute("select count(*), count(distinct ip_hash) from rate").fetchone())'; }
rate_hashes() { compose exec -T api python -c 'import sqlite3; print(*[r[0] for r in sqlite3.connect("/jobs/jobs.db").execute("select ip_hash from rate order by at, rowid")])'; }
# What the api would record for an address today, under this run's salt (ratelimit.ip_hash).
hash_of() { py -c 'import sys; from pdf_splitter.ratelimit import ip_hash; print(ip_hash(sys.argv[1], secret=sys.argv[2]))' "$1" "$PDFSPLIT_IP_SALT"; }
newest_hash() { rate_hashes | awk '{print $NF}'; }
api_state() { docker inspect -f 'oom_killed={{.State.OOMKilled}} restarts={{.RestartCount}} health={{.State.Health.Status}} mem={{.HostConfig.Memory}}' "$PROJECT-api-1"; }

teardown() {
  local rc=$? left
  set +e
  say "teardown: docker compose -p $PROJECT down -v; untag the :$PDFSPLIT_TAG images"
  docker ps -aq --filter "name=^$PROJECT-client" | xargs -r docker rm -f >/dev/null 2>&1
  compose down -v --remove-orphans >/dev/null 2>&1
  # Untag, never `down --rmi`: an unchanged build shares its image ID with a running stack's `:local` tag, and
  # removing by ID would take that tag with it.
  docker image rm "pdfsplit-app:$PDFSPLIT_TAG" "pdfsplit-caddy:$PDFSPLIT_TAG" >/dev/null 2>&1
  rm -rf "$WORK"
  left=$({ docker ps -a --format '{{.Names}}'; docker volume ls --format '{{.Name}}'; docker network ls --format '{{.Name}}'; \
           docker images --format '{{.Repository}}:{{.Tag}}'; } | grep -c -e "^$PROJECT" -e ":smoke$")
  if [ "$left" != 0 ]; then say "FAIL  $left leftover container/volume/network/image of $PROJECT"; rc=1; fi
  if [ "$(others)" != "$OTHERS_BEFORE" ]; then say "FAIL  the other containers on this host changed"; rc=1; fi
  say "other containers untouched: $(others | wc -l) before and after; leftovers of $PROJECT: $left"
  [ "$rc" = 0 ] && say "SMOKE PASSED" || say "SMOKE FAILED (exit $rc)"
  exit "$rc"
}
trap teardown EXIT

OTHERS_BEFORE=$(others)
say "smoke: project $PROJECT, caddy https://localhost:$PDFSPLIT_HTTPS_PORT, backend $PDFSPLIT_SUBNET + $PDFSPLIT_SUBNET6, edge $PDFSPLIT_EDGE_SUBNET + $PDFSPLIT_EDGE_SUBNET6, $(others | wc -l) other containers running"

# The same 6-page, 3-heading book tests/test_api_e2e.py drives (tests/fixtures/books.py).
(cd "$ROOT" && uv run --quiet python -c \
  'import sys; from pathlib import Path; from tests.fixtures.books import headed_book; headed_book(Path(sys.argv[1]), outline=False)' \
  "$WORK/book.pdf")
step "fixture book: $(stat -c %s "$WORK/book.pdf") bytes"

say "docker compose up -d --build --wait"
compose up -d --build --wait --wait-timeout 300 2>&1 | sed 's/^/      /'
step "stack up: $(compose ps --format '{{.Service}}={{.Status}}' | tr '\n' ' ')"

for _ in $(seq 1 60); do
  code=$(req "$BASE/api/health" "$WORK/health.json")
  [ "$code" = 200 ] && [ "$(field "$WORK/health.json" ok)" = True ] && break
  sleep 1
done
[ "$code" = 200 ] || fail "health through caddy: $code"
[ "$(field "$WORK/health.json" engine_version)" = 0.4.2 ] || fail "engine_version: $(cat "$WORK/health.json")"
step "GET /api/health via caddy → $code $(tr -d '\n' <"$WORK/health.json")"

# The SPA: index.html at /, the same file for a deep link, the security headers and compression on the bundle.
[ "$(req "$BASE/" "$WORK/index.html")" = 200 ] && grep -q '<div id="app">' "$WORK/index.html" || fail "GET / is not the SPA"
[ "$(req "$BASE/j/no-such-job" "$WORK/deep.html")" = 200 ] && cmp -s "$WORK/index.html" "$WORK/deep.html" || fail "SPA fallback for /j/<id>"
curl -ksSI "$BASE/" >"$WORK/headers.txt"
grep -qi "^content-security-policy: default-src 'self'; img-src 'self' blob:" "$WORK/headers.txt" || fail "CSP header: $(cat "$WORK/headers.txt")"
grep -qi "^x-content-type-options: nosniff" "$WORK/headers.txt" || fail "nosniff header"
bundle=$(grep -o '/assets/index-[^"]*\.js' "$WORK/index.html" | head -1)
curl -ksS -H 'Accept-Encoding: gzip' -D "$WORK/bundle-headers.txt" -o /dev/null "$BASE$bundle"
grep -qi "^content-encoding: gzip" "$WORK/bundle-headers.txt" || fail "no gzip on $bundle"
step "SPA: / and /j/<id> serve index.html; CSP + nosniff present; $bundle gzip-encoded"

# Egress (gate r1): the api's only network is internal — no DNS, no route to the host's other bridges or the
# LAN — while caddy (edge) still reaches the ACME servers, and the worker has no network at all.
compose exec -T api python - "$EDGE_GW4" "$PDFSPLIT_HTTPS_PORT" >"$WORK/egress.txt" <<'PY'
import socket, sys
for host, port in (("github.com", 443), (sys.argv[1], int(sys.argv[2])), ("1.1.1.1", 53)):
    try:
        socket.create_connection((host, port), timeout=4).close()
        print("REACHED", host, port)
    except OSError as e:
        print("blocked", host, port, type(e).__name__)
PY
grep -q REACHED "$WORK/egress.txt" && fail "the api has egress: $(tr '\n' ';' <"$WORK/egress.txt")"
compose exec -T caddy wget -q -T 10 -O /dev/null https://acme-v02.api.letsencrypt.org/directory || fail "caddy cannot reach the ACME directory"
[ "$(docker inspect -f '{{.HostConfig.NetworkMode}}' "$PROJECT-worker-1")" = none ] || fail "worker has a network"
[ "$(docker port "$PROJECT-api-1" | wc -l)" = 0 ] || fail "the api publishes a port: $(docker port "$PROJECT-api-1")"
step "egress: api $(awk '{printf "%s:%s %s; ", $2, $3, $1}' "$WORK/egress.txt")caddy reached acme-v02.api.letsencrypt.org; worker network=none; api publishes nothing"

# The host itself (gate r2): `internal` stops Docker forwarding for the network, but the host is ON it — its
# gateway address — so anything the host binds to 0.0.0.0/[::] answers the api there unless the host's firewall
# drops INPUT from the backend subnets (README § Deploy has the rule). The smoke can only report what this host
# does: a WARNING, not a failure, since the fix is outside the stack.
# shellcheck disable=SC2086
compose exec -T api python - "$BACKEND_GW4" "$BACKEND_GW6" $HOST_PORTS >"$WORK/host.txt" <<'PY'
import socket, sys
for host in sys.argv[1:3]:
    for port in sys.argv[3:]:
        try:
            socket.create_connection((host, int(port)), timeout=2).close()
            print("REACHED", host, port)
        except OSError as e:
            print("blocked", host, port, type(e).__name__)
PY
if grep -q REACHED "$WORK/host.txt"; then
  say "WARN  the api reaches the host at its backend gateway: $(awk '/REACHED/ {printf "[%s]:%s ", $2, $3}' "$WORK/host.txt")— drop INPUT from $PDFSPLIT_SUBNET and $PDFSPLIT_SUBNET6 on the host (README § Deploy) before going public"
else
  step "host reachability: nothing on the host answered the api at $BACKEND_GW4 / $BACKEND_GW6 (ports $HOST_PORTS)"
fi

code=$(req "$BASE/api/jobs" "$WORK/job.json" -F "file=@$WORK/book.pdf;type=application/pdf;filename=Synthetic Book.pdf")
[ "$code" = 201 ] || fail "upload: $code $(cat "$WORK/job.json")"
JOB=$(field "$WORK/job.json" id)
step "POST /api/jobs → 201 (state $(field "$WORK/job.json" state))"

wait_state() {
  local want=$1 timeout=$2 state=""
  for ((i = 0; i < timeout; i++)); do
    [ "$(req "$BASE/api/jobs/$JOB" "$WORK/status.json")" = 200 ] || fail "GET /api/jobs/{id}: $(cat "$WORK/status.json")"
    state=$(field "$WORK/status.json" state)
    [ "$state" = "$want" ] && return 0
    [ "$state" = failed ] && fail "job failed: $(field "$WORK/status.json" error_code) $(field "$WORK/status.json" message)"
    sleep 1
  done
  fail "timed out after ${timeout}s waiting for $want (state $state)"
}
wait_state review 180
step "analyze → review ($(field "$WORK/status.json" progress)/$(field "$WORK/status.json" total) pages)"

[ "$(req "$BASE/api/jobs/$JOB/plan" "$WORK/plan.json")" = 200 ] || fail "GET plan"
sections=$(py -c 'import json,sys; print(len(json.load(open(sys.argv[1]))["sections"]))' "$WORK/plan.json")
[ "$sections" = 3 ] || fail "expected 3 suggested sections, got $sections"
code=$(req "$BASE/api/jobs/$JOB/plan" "$WORK/plan-put.json" -X PUT -H 'Content-Type: application/json' --data-binary "@$WORK/plan.json")
[ "$code" = 200 ] || fail "PUT plan: $code $(cat "$WORK/plan-put.json")"
step "GET /plan → 200 (source $(field "$WORK/plan.json" source), 3 sections); PUT /plan → 200"

code=$(req "$BASE/api/jobs/$JOB/cut" "$WORK/cut.json" -X POST)
[ "$code" = 202 ] && [ "$(field "$WORK/cut.json" state)" = queued ] || fail "POST cut: $code $(cat "$WORK/cut.json")"
wait_state done 300
step "POST /cut → 202; cut → done ($(field "$WORK/status.json" progress)/$(field "$WORK/status.json" total) sections)"

code=$(curl -ksS -o "$WORK/result.zip" -D "$WORK/zip-headers.txt" -w '%{http_code}' "$BASE/api/jobs/$JOB/result.zip")
[ "$code" = 200 ] && grep -qi '^content-type: application/zip' "$WORK/zip-headers.txt" || fail "result.zip: $code"
mapfile -t pdfs < <(py -c 'import sys,zipfile; print(*[n for n in zipfile.ZipFile(sys.argv[1]).namelist() if n.endswith(".pdf")], sep="\n")' "$WORK/result.zip")
[ "${#pdfs[@]}" = 3 ] || fail "expected 3 PDFs in result.zip, got ${#pdfs[@]}: ${pdfs[*]}"
py -c 'import sys,zipfile; z=zipfile.ZipFile(sys.argv[1]); assert "manifest.json" in z.namelist(); assert all(z.read(n).startswith(b"%PDF-") for n in z.namelist() if n.endswith(".pdf"))' "$WORK/result.zip"
step "GET /result.zip → 200 application/zip, $(stat -c %s "$WORK/result.zip") bytes, 3 PDFs + manifest.json:"
printf '        %s\n' "${pdfs[@]}"

# ── Straight at the api from CLIENT4 on `backend` (gate r1 + the addendum). Its 6-per-hour budget is spent
# exactly: the flood spends none (refused before the slot), the cap check four, the XFF check the last two.
read -r rows_0 hashes_0 < <(rate_rows)

# Flood: 8 × 300 MiB at once, all declared up front. Each is 413 from the headers alone, with the connection
# closed under the client after a few hundred KB of the body; the api's memory is untouched (the review saw
# this exact flood OOM-kill it at 2 GiB).
backend http://api:8000/api/jobs --zeros $((300 * MiB)) --n 8 --family 4 >"$WORK/flood.txt" || fail "flood client: $(cat "$WORK/flood.txt")"
[ "$(grep -c '^413 ' "$WORK/flood.txt")" = 8 ] || fail "flood answered: $(awk '{print $1}' "$WORK/flood.txt" | tr '\n' ' ')"
most_sent=$(sed -n 's/.*sent=\([0-9]*\).*/\1/p' "$WORK/flood.txt" | sort -n | tail -1)
[ "$most_sent" -lt $((8 * MiB)) ] || fail "a 413 came only after $most_sent bytes of body"
state=$(api_state)
[[ $state == *"oom_killed=false restarts=0 health=healthy"* ]] || fail "api after the flood: $state"
read -r rows_1 hashes_1 < <(rate_rows)
[ "$rows_1" = "$rows_0" ] || fail "the flood spent $((rows_1 - rows_0)) rate slots"
step "flood: 8 × 300 MiB straight at the api → 8 × 413 too_large, at most $((most_sent / 1024)) KB of body read each, 0 slots spent; api $state, $(docker stats --no-stream --format '{{.MemUsage}}' "$PROJECT-api-1" | cut -d/ -f1 | tr -d ' ') resident"

# The client's cap (gate r2): 6 × 60 MiB at once from ONE address, throttled so the bodies overlap. Two stream
# (and end as `not_pdf`), the other four are 429 `rate_limited` at once, unread and without a slot — one address
# can no longer take every slot. Two more afterwards bring CLIENT4 to four spent, which the XFF check counts on.
backend http://api:8000/api/jobs --zeros $((60 * MiB)) --n 6 --rate $((12 * MiB)) --family 4 >"$WORK/cap-client.txt" || fail "cap client: $(cat "$WORK/cap-client.txt")"
codes=$(awk '{print $1}' "$WORK/cap-client.txt" | sort | uniq -c | awk '{printf "%s×%s ", $1, $2}')
[ "$(grep -c '^429 .*rate_limited' "$WORK/cap-client.txt")" = 4 ] && [ "$(grep -c '^400 .*not_pdf' "$WORK/cap-client.txt")" = 2 ] || fail "per-client cap answered: $codes"
read -r rows_2a hashes_2a < <(rate_rows)
[ "$((rows_2a - rows_1))" = 2 ] && [ "$hashes_2a" = "$((hashes_1 + 1))" ] || fail "per-client cap: rate rows $rows_1→$rows_2a, hashes $hashes_1→$hashes_2a"
[ "$(newest_hash)" = "$(hash_of "$CLIENT4")" ] || fail "the per-client cap's uploads were not counted under $CLIENT4"
backend http://api:8000/api/jobs --zeros $((60 * MiB)) --n 2 --rate $((12 * MiB)) --family 4 >"$WORK/cap-client2.txt" || fail "cap client: $(cat "$WORK/cap-client2.txt")"
[ "$(grep -c '^400 .*not_pdf' "$WORK/cap-client2.txt")" = 2 ] || fail "two more from $CLIENT4 answered: $(awk '{print $1}' "$WORK/cap-client2.txt" | tr '\n' ' ')"
step "per-client cap: 6 × 60 MiB at once from $CLIENT4 → $codes(2 streamed, 4 refused unread, no slot); rate rows +2 under $CLIENT4; then 2 more → 2 × 400"

# The server's cap: 6 × 60 MiB at once from THREE addresses (two each, inside their own caps);
# PDFSPLIT_MAX_UPLOADS=4 stream (to the spool on the volume: their fds sit under /jobs/.spool, not /tmp) and
# end as `not_pdf`, the other two are 503 `overloaded` at once, without a slot.
( sleep 3; compose exec -T api sh -c 'ls -l /proc/1/fd | grep -c "/jobs/.spool/"' >"$WORK/spool-fds.txt" 2>/dev/null ) &
for i in 80 81 82; do
  client backend "${PDFSPLIT_SUBNET%.0/24}.$i" "${PDFSPLIT_SUBNET6%/64}$i" http://api:8000/api/jobs --zeros $((60 * MiB)) --n 2 --rate $((12 * MiB)) --family 4 >"$WORK/cap-$i.txt" &
done
wait
cat "$WORK"/cap-8?.txt >"$WORK/cap.txt"
codes=$(awk '{print $1}' "$WORK/cap.txt" | sort | uniq -c | awk '{printf "%s×%s ", $1, $2}')
[ "$(grep -c '^503 .*overloaded' "$WORK/cap.txt")" = 2 ] && [ "$(grep -c '^400 .*not_pdf' "$WORK/cap.txt")" = 4 ] || fail "server cap answered: $codes"
spool_fds=$(cat "$WORK/spool-fds.txt" 2>/dev/null || echo 0)
[ "${spool_fds:-0}" -ge 1 ] || fail "no upload was spooling under /jobs/.spool while the bodies streamed"
read -r rows_2 hashes_2 < <(rate_rows)
[ "$((rows_2 - rows_2a))" = 6 ] || fail "server cap: rate rows $rows_2a→$rows_2 (expected +4 for the streamed uploads, +2 for CLIENT4's)"
state=$(api_state)
[[ $state == *"oom_killed=false restarts=0 health=healthy"* ]] || fail "api after the cap check: $state"
step "server cap: 6 × 60 MiB at once from 3 addresses → $codes(4 streamed to the spool: $spool_fds open /jobs/.spool fds mid-upload; 2 refused unread); rate rows +4 across $((hashes_2 - hashes_2a)) new hashes; api $state"

# Addendum: X-Forwarded-For from anyone but caddy is ignored. Three uploads, each claiming a fresh address,
# straight at the api; if the header were believed each would open its own window and all three would be
# 201. Counted under the real peer, whose budget has two left, the third is 429 — and the rows gained no hash.
codes=()
for xff in 203.0.113.1 203.0.113.2 203.0.113.3; do
  codes+=("$(backend http://api:8000/api/jobs --file /w/book.pdf --family 4 --header "X-Forwarded-For: $xff" | awk '{print $1}')")
done
read -r rows_3 hashes_3 < <(rate_rows)
[ "${codes[*]}" = "201 201 429" ] || fail "spoofed XFF: direct uploads answered ${codes[*]}"
[ "$((rows_3 - rows_2))" = 2 ] && [ "$hashes_3" = "$hashes_2" ] || fail "XFF check: rate rows $rows_2→$rows_3, hashes $hashes_2→$hashes_3"
[ "$(newest_hash)" = "$(hash_of "$CLIENT4")" ] || fail "the XFF uploads were not counted under $CLIENT4"
step "XFF check: 3 uploads straight at the api with spoofed X-Forwarded-For → ${codes[*]}; rate rows $rows_2→$rows_3, all under $CLIENT4, no new client hash"

# IPv6 (gate r1 + r2): a v6 client through caddy is recorded under ITS /64 (caddy forwards its address, the api
# believes caddy's v6 address, ratelimit.rate_key folds it to the prefix). Two paths, both in the edge /64: a
# container on `edge` talking to caddy over v6, and the host reaching the published port at the edge network's
# v6 gateway — the netfilter DNAT path a visitor from the internet takes (loopback goes through docker-proxy
# instead and is not representative — it dials caddy from the edge gateway, so the run's very first upload
# already opened the edge /64's bucket when curl chose ::1). Two addresses, one /64: one hash between them.
edge https://caddy/api/jobs --file /w/book.pdf --family 6 --sni localhost >"$WORK/v6.txt" || fail "v6 client: $(cat "$WORK/v6.txt")"
grep -q "^201 .*local=$CLIENT6 " "$WORK/v6.txt" || fail "v6 upload through caddy: $(cat "$WORK/v6.txt")"
[ "$(newest_hash)" = "$(hash_of "$CLIENT6")" ] || fail "the v6 upload was not counted under $CLIENT6"
read -r rows_4a hashes_4a < <(rate_rows)
code=$(req "$BASE/api/jobs" "$WORK/v6-host.json" -6 --resolve "localhost:$PDFSPLIT_HTTPS_PORT:[$EDGE_GW6]" -F "file=@$WORK/book.pdf;type=application/pdf")
[ "$code" = 201 ] || fail "host → [$EDGE_GW6]:$PDFSPLIT_HTTPS_PORT: $code $(cat "$WORK/v6-host.json")"
[ "$(newest_hash)" = "$(hash_of "$EDGE_GW6")" ] || fail "the host's v6 upload was not counted under $EDGE_GW6"
[ "$(hash_of "$EDGE_GW6")" = "$(hash_of "$CLIENT6")" ] || fail "$EDGE_GW6 and $CLIENT6 are one /64 and should hash alike"
read -r rows_4 hashes_4 < <(rate_rows)
[ "$((rows_4 - rows_4a))" = 1 ] && [ "$hashes_4" = "$hashes_4a" ] || fail "two addresses in one /64 should share a bucket: rows $rows_4a→$rows_4, hashes $hashes_4a→$hashes_4"
step "IPv6: edge client $CLIENT6 → caddy over v6 → 201, counted under its /64; host → [$EDGE_GW6]:$PDFSPLIT_HTTPS_PORT (v6 DNAT) → 201, same /64 → the same bucket (rows $rows_4a→$rows_4, hashes $hashes_4a→$hashes_4; rate_key $(py -c 'import sys; from pdf_splitter.ratelimit import rate_key; print(rate_key(sys.argv[1]))' "$CLIENT6"))"

# ── Slow bodies (gate r2), straight at the api from two fresh addresses on `backend`.
# Four uploads from one address, each a real trickle (256 B/s into a declared 64 KiB): two are 429 at once (the
# per-client cap, PDFSPLIT_MAX_UPLOADS_PER_CLIENT=2, no slot spent) and the two admitted fall under the 32 KiB
# per 30 s floor and are 408 `too_slow` — while they hold, a second client is not locked out (two of the
# server's four slots are free). Before this, four such uploads pinned every slot for as long as the client liked.
read -r rows_5 hashes_5 < <(rate_rows)
trickler http://api:8000/api/jobs --zeros $((64 * 1024)) --chunk 256 --rate 256 --n 4 --family 4 >"$WORK/trickle.txt" &
trickle_pid=$!
sleep 4
code=$(other http://api:8000/api/jobs --file /w/book.pdf --family 4 | awk '{print $1}')
[ "$code" = 201 ] || fail "a second client was locked out while one client trickled: $code"
wait "$trickle_pid" || fail "trickle client: $(cat "$WORK/trickle.txt")"
codes=$(awk '{print $1}' "$WORK/trickle.txt" | sort | uniq -c | awk '{printf "%s×%s ", $1, $2}')
[ "$(grep -c '^429 .*rate_limited' "$WORK/trickle.txt")" = 2 ] && [ "$(grep -c '^408 .*too_slow' "$WORK/trickle.txt")" = 2 ] || fail "trickle answered: $codes"
most_sent=$(grep '^408' "$WORK/trickle.txt" | sed -n 's/.*sent=\([0-9]*\).*/\1/p' | sort -n | tail -1)
read -r rows_6 hashes_6 < <(rate_rows)
[ "$((rows_6 - rows_5))" = 3 ] && [ "$hashes_6" = "$((hashes_5 + 2))" ] || fail "trickle: rate rows $rows_5→$rows_6, hashes $hashes_5→$hashes_6"
state=$(api_state)
[[ $state == *"oom_killed=false restarts=0 health=healthy"* ]] || fail "api after the trickle: $state"
step "slow uploads: 4 × 64 KiB at 256 B/s from $TRICKLER → $codes(2 refused by the per-client cap unread, 2 abandoned after a 30 s window with ≤ $((most_sent / 1024)) KB in); $OTHER got 201 meanwhile; rate rows +3 (2 admitted trickles + 1), 2 new hashes; api $state"

# 63 held-open PUT /plan bodies (limit_concurrency is 64; the job need not exist — the body used to be read
# before the route looked the job up): each is 408 after the 20 s body bound, so the api is answering again
# within half a minute instead of 503ing for as long as the bodies were held.
trickler http://api:8000/api/jobs/no-such-job/plan --method PUT --raw --declare 100000 --zeros 100 --n 63 --family 4 >"$WORK/puts.txt" || fail "put client: $(cat "$WORK/puts.txt")"
[ "$(grep -c '^408 .*too_slow' "$WORK/puts.txt")" = 63 ] || fail "held PUTs answered: $(awk '{print $1}' "$WORK/puts.txt" | sort | uniq -c | tr '\n' ' ')"
code=$(req "$BASE/api/health" "$WORK/health2.json")
[ "$code" = 200 ] && [ "$(field "$WORK/health2.json" ok)" = True ] || fail "health after the held PUTs: $code"
state=$(api_state)
[[ $state == *"restarts=0 health=healthy"* ]] || fail "api after the held PUTs: $state"
step "held PUTs: 63 × PUT /plan declaring 100 KB and sending 100 B, straight at the api → 63 × 408 too_slow after the 20 s bound; health via caddy 200 after; api $state"

[ "$(req "$BASE/api/jobs/$JOB" /dev/null -X DELETE)" = 204 ] || fail "DELETE"
[ "$(req "$BASE/api/jobs/$JOB" "$WORK/gone.json")" = 410 ] || fail "GET after DELETE: $(cat "$WORK/gone.json")"
step "DELETE → 204, GET → 410 ($(field "$WORK/gone.json" code))"
