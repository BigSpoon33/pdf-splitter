#!/usr/bin/env bash
# The compose stack end to end on a throwaway project (STORY-013 AC-4): build, up, health through caddy, the
# synthetic book uploaded → review → plan → cut → a zip with 3 PDFs; then what the gate asked for (STORY-013
# addendum + gate r1): the api has no egress, an upload flood is refused before a byte of body is read, the
# in-flight cap holds, spoofed X-Forwarded-For straight at the api is ignored, an IPv6 client is counted under
# its own address; delete, `down -v`. Exit 0 only when every step passed AND nothing of the project is left
# behind. Needs docker compose, curl and the repo's dev environment (uv) for the fixture book and the hashes.
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
  docker run --rm --name "$PROJECT-client" --network "${PROJECT}_$net" --ip "$ip4" --ip6 "$ip6" \
    -v "$WORK:/w:ro" -v "$HERE/smoke_client.py:/client.py:ro" --entrypoint python "pdfsplit-app:$PDFSPLIT_TAG" /client.py "$@"
}
backend() { client backend "$CLIENT4" "${PDFSPLIT_SUBNET6%/64}77" "$@"; }
edge() { client edge "${PDFSPLIT_EDGE_SUBNET%.0/24}.77" "$CLIENT6" "$@"; }
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
  docker rm -f "$PROJECT-client" >/dev/null 2>&1
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

# The in-flight cap: 6 × 60 MiB, throttled so the bodies overlap; PDFSPLIT_MAX_UPLOADS=4 stream (to the spool
# on the volume: their fds sit under /jobs/.spool, not /tmp) and end as `not_pdf`, the other two are 503
# `overloaded` at once, without a slot.
( sleep 2; compose exec -T api sh -c 'ls -l /proc/1/fd | grep -c "/jobs/.spool/"' >"$WORK/spool-fds.txt" 2>/dev/null ) &
backend http://api:8000/api/jobs --zeros $((60 * MiB)) --n 6 --rate $((12 * MiB)) --family 4 >"$WORK/cap.txt" || fail "cap client: $(cat "$WORK/cap.txt")"
wait
codes=$(awk '{print $1}' "$WORK/cap.txt" | sort | uniq -c | awk '{printf "%s×%s ", $1, $2}')
[ "$(grep -c '^503 .*overloaded' "$WORK/cap.txt")" = 2 ] && [ "$(grep -c '^400 .*not_pdf' "$WORK/cap.txt")" = 4 ] || fail "cap answered: $codes"
spool_fds=$(cat "$WORK/spool-fds.txt" 2>/dev/null || echo 0)
[ "${spool_fds:-0}" -ge 1 ] || fail "no upload was spooling under /jobs/.spool while the bodies streamed"
read -r rows_2 hashes_2 < <(rate_rows)
[ "$((rows_2 - rows_1))" = 4 ] && [ "$hashes_2" = "$((hashes_1 + 1))" ] || fail "cap check: rate rows $rows_1→$rows_2, hashes $hashes_1→$hashes_2"
[ "$(newest_hash)" = "$(hash_of "$CLIENT4")" ] || fail "the cap check's uploads were not counted under $CLIENT4"
state=$(api_state)
[[ $state == *"oom_killed=false restarts=0 health=healthy"* ]] || fail "api after the cap check: $state"
step "in-flight cap: 6 × 60 MiB at once → $codes(4 streamed to the spool: $spool_fds open /jobs/.spool fds mid-upload; 2 refused unread); rate rows +4 under $CLIENT4; api $state"

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

# IPv6 (gate r1): a v6 client through caddy is recorded under ITS address (caddy forwards it, the api believes
# caddy's v6 address). Two paths: a container on `edge` talking to caddy over v6, and the host reaching the
# published port at the edge network's v6 gateway — the netfilter DNAT path a visitor from the internet takes
# (loopback goes through docker-proxy instead and is not representative).
edge https://caddy/api/jobs --file /w/book.pdf --family 6 --sni localhost >"$WORK/v6.txt" || fail "v6 client: $(cat "$WORK/v6.txt")"
grep -q "^201 .*local=$CLIENT6 " "$WORK/v6.txt" || fail "v6 upload through caddy: $(cat "$WORK/v6.txt")"
[ "$(newest_hash)" = "$(hash_of "$CLIENT6")" ] || fail "the v6 upload was not counted under $CLIENT6"
code=$(req "$BASE/api/jobs" "$WORK/v6-host.json" -6 --resolve "localhost:$PDFSPLIT_HTTPS_PORT:[$EDGE_GW6]" -F "file=@$WORK/book.pdf;type=application/pdf")
[ "$code" = 201 ] || fail "host → [$EDGE_GW6]:$PDFSPLIT_HTTPS_PORT: $code $(cat "$WORK/v6-host.json")"
[ "$(newest_hash)" = "$(hash_of "$EDGE_GW6")" ] || fail "the host's v6 upload was not counted under $EDGE_GW6"
read -r rows_4 hashes_4 < <(rate_rows)
step "IPv6: edge client $CLIENT6 → caddy over v6 → 201, counted under $CLIENT6; host → [$EDGE_GW6]:$PDFSPLIT_HTTPS_PORT (v6 DNAT) → 201, counted under $EDGE_GW6; distinct client hashes $hashes_3→$hashes_4"

[ "$(req "$BASE/api/jobs/$JOB" /dev/null -X DELETE)" = 204 ] || fail "DELETE"
[ "$(req "$BASE/api/jobs/$JOB" "$WORK/gone.json")" = 410 ] || fail "GET after DELETE: $(cat "$WORK/gone.json")"
step "DELETE → 204, GET → 410 ($(field "$WORK/gone.json" code))"
