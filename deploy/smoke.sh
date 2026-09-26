#!/usr/bin/env bash
# The compose stack end to end on a throwaway project (STORY-013 AC-4): build, up, health through caddy, the
# synthetic book uploaded → review → plan → cut → a zip with 3 PDFs, the spoofed X-Forwarded-For check straight
# at the api (STORY-013 addendum), delete, `down -v`. Exit 0 only when every step passed AND nothing of the
# project is left behind. Needs docker compose, curl and the repo's dev environment (uv) for the fixture book.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/.." && pwd)
PROJECT=${SMOKE_PROJECT:-pdfsplit-smoke}

# Everything the base file interpolates is pinned to throwaway values, so a run never touches a real stack on
# the same host: its own image tag, high host ports, its own subnet and a random salt. Two uploads per hour
# make the XFF check cost three direct uploads instead of seven.
export PDFSPLIT_TAG=smoke
export PDFSPLIT_HTTP_PORT=${SMOKE_HTTP_PORT:-18080}
export PDFSPLIT_HTTPS_PORT=${SMOKE_HTTPS_PORT:-18443}
export SMOKE_API_PORT=${SMOKE_API_PORT:-18000}
export PDFSPLIT_SUBNET=${SMOKE_SUBNET:-172.31.0.0/24}
export PDFSPLIT_CADDY_IP=${SMOKE_CADDY_IP:-172.31.0.10}
export PDFSPLIT_IP_SALT="smoke-$RANDOM$RANDOM$RANDOM"
export PDFSPLIT_RATE_PER_HOUR=2
export PUBLIC_HOST=localhost
export PDFSPLIT_PUBLIC_URL="https://localhost:$PDFSPLIT_HTTPS_PORT"

BASE="https://localhost:$PDFSPLIT_HTTPS_PORT"
API_DIRECT="http://127.0.0.1:$SMOKE_API_PORT"
WORK=$(mktemp -d)

compose() { docker compose -p "$PROJECT" -f "$HERE/compose.yaml" -f "$HERE/compose.smoke.yaml" "$@"; }
py() { uv run --quiet --project "$ROOT" python "$@"; }
field() { py -c 'import json,sys; v=json.load(open(sys.argv[1]))[sys.argv[2]]; print("" if v is None else v)' "$1" "$2"; }
say() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*"; }
step() { say "  ok  $*"; }
fail() { say "FAIL  $*"; exit 1; }
# Status code on stdout, body in $2, any curl options after. -k: the localhost certificate is caddy's own CA's.
req() { curl -ksS -o "$2" -w '%{http_code}' "${@:3}" "$1" 2>/dev/null || true; }
others() { docker ps -a --format '{{.Names}}' | grep -v "^$PROJECT-" | sort; }

teardown() {
  local rc=$? left
  set +e
  say "teardown: docker compose -p $PROJECT down -v; untag the :$PDFSPLIT_TAG images"
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
say "smoke: project $PROJECT, caddy https://localhost:$PDFSPLIT_HTTPS_PORT, api direct $API_DIRECT, $(others | wc -l) other containers running"

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

# Addendum: X-Forwarded-For from anyone but caddy is ignored. Three uploads, each claiming a fresh address,
# go straight to the api port; if the header were believed each would open its own 2-per-hour window and all
# three would be 201. Counted under the real peer, the third is 429 whatever the caddy upload's client was.
# The `rate` rows say the same thing: the accepted ones added at most ONE hash (the peer's).
rate_rows() {
  compose run --rm --no-deps -T --entrypoint python api -c \
    'import sqlite3; print(*sqlite3.connect("/jobs/jobs.db").execute("select count(*), count(distinct ip_hash) from rate").fetchone())' 2>/dev/null
}
read -r rows_before hashes_before < <(rate_rows)
codes=()
for xff in 203.0.113.1 203.0.113.2 203.0.113.3; do
  codes+=("$(req "$API_DIRECT/api/jobs" "$WORK/xff.json" -H "X-Forwarded-For: $xff" -F "file=@$WORK/book.pdf;type=application/pdf")")
done
read -r rows_after hashes_after < <(rate_rows)
accepted=$(printf '%s\n' "${codes[@]}" | grep -c '^201$' || true)
[ "${codes[2]}" = 429 ] || fail "spoofed XFF was believed: direct uploads answered ${codes[*]}"
[ $((rows_after - rows_before)) = "$accepted" ] || fail "rate rows grew by $((rows_after - rows_before)) for $accepted accepted uploads"
[ $((hashes_after - hashes_before)) -le 1 ] || fail "spoofed XFF produced $((hashes_after - hashes_before)) new client hashes"
step "XFF check: 3 uploads straight to the api with spoofed X-Forwarded-For → ${codes[*]}; rate rows $rows_before→$rows_after, distinct client hashes $hashes_before→$hashes_after"

[ "$(req "$BASE/api/jobs/$JOB" /dev/null -X DELETE)" = 204 ] || fail "DELETE"
[ "$(req "$BASE/api/jobs/$JOB" "$WORK/gone.json")" = 410 ] || fail "GET after DELETE: $(cat "$WORK/gone.json")"
step "DELETE → 204, GET → 410 ($(field "$WORK/gone.json" code))"
