#!/usr/bin/env bash
# Session affinity across two machines (SPEC 10.4 / issue #1 FLY-3).
#
# tests/test_fly_affinity.py drives the middleware as raw ASGI, which
# proves the routing decision and nothing about the server it is wrapped
# around. This runs the real thing: two containers with distinct
# FLY_MACHINE_IDs, a real MCP session, real headers.
#
#   scripts/check_affinity.sh http://127.0.0.1:8101 <A-id> \
#                             http://127.0.0.1:8102 <B-id> <token>
#
# What it cannot show is that Fly's proxy honours `fly-replay`. That
# needs a deployment; everything on our side of the header is here.
set -uo pipefail

A_URL="${1:?usage: check_affinity.sh A_URL A_ID B_URL B_ID TOKEN}"
A_ID="${2:?missing A machine id}"
B_URL="${3:?missing B url}"
B_ID="${4:?missing B machine id}"
TOKEN="${5:-}"

ACCEPT='Accept: application/json, text/event-stream'
CT='Content-Type: application/json'
AUTH=()
[ -n "$TOKEN" ] && AUTH=(-H "Authorization: Bearer $TOKEN")

INIT='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"affinity-check","version":"1"}}}'
LIST='{"jsonrpc":"2.0","id":2,"method":"tools/list"}'

fail=0
ok()  { printf '  [ok] %s\n' "$1"; }
bad() { printf '  [FAIL] %s\n' "$1"; fail=1; }

hdrs=$(mktemp); body=$(mktemp)
trap 'rm -f "$hdrs" "$body"' EXIT

echo "affinity check: A=$A_ID ($A_URL)  B=$B_ID ($B_URL)"

# 1. A issues a session id, and it must carry A's machine id.
curl -s -D "$hdrs" -o "$body" -X POST "$A_URL/mcp" \
  "${AUTH[@]}" -H "$CT" -H "$ACCEPT" -d "$INIT" >/dev/null
SID=$(tr -d '\r' < "$hdrs" | awk 'tolower($1)=="mcp-session-id:"{print $2}')

if [ -z "$SID" ]; then
  bad "A issued no Mcp-Session-Id (body: $(head -c 200 "$body"))"
  exit 1
fi
case "$SID" in
  "$A_ID"~*) ok "A issued a session wrapped with its own id" ;;
  *) bad "A issued an unwrapped session id: $SID" ;;
esac

curl -s -o /dev/null -X POST "$A_URL/mcp" "${AUTH[@]}" -H "$CT" -H "$ACCEPT" \
  -H "Mcp-Session-Id: $SID" \
  -d '{"jsonrpc":"2.0","method":"notifications/initialized"}'

# 2. B must refuse to serve A's session, and say where it belongs.
#    Serving it locally is the bug: the client would see an empty model.
code=$(curl -s -D "$hdrs" -o "$body" -w '%{http_code}' -X POST "$B_URL/mcp" \
  "${AUTH[@]}" -H "$CT" -H "$ACCEPT" -H "Mcp-Session-Id: $SID" -d "$LIST")
replay=$(tr -d '\r' < "$hdrs" | awk 'tolower($1)=="fly-replay:"{print $2}')

if [ "$replay" = "instance=$A_ID" ]; then
  ok "B answered fly-replay: instance=$A_ID (status $code)"
else
  bad "B did not ask for a replay to A (status $code, fly-replay: '$replay')"
fi
if grep -q '"tools"' "$body"; then
  bad "B served a session it does not own"
else
  ok "B served no tools for a session it does not own"
fi

# 3. Already replayed once and still not ours: the owner is gone. That
#    must be a clean MCP error, not a 500 and not another bounce.
code=$(curl -s -D "$hdrs" -o "$body" -w '%{http_code}' -X POST "$B_URL/mcp" \
  "${AUTH[@]}" -H "$CT" -H "$ACCEPT" -H "Mcp-Session-Id: $SID" \
  -H "fly-replay-src: instance=elsewhere" -d "$LIST")
replay=$(tr -d '\r' < "$hdrs" | awk 'tolower($1)=="fly-replay:"{print $2}')

if [ "$code" -ge 500 ]; then
  bad "a vanished owner produced $code; a gone session is not a server fault"
elif [ -n "$replay" ]; then
  bad "B bounced an already-replayed request: replay loop"
elif grep -q '\-32600' "$body"; then
  ok "a vanished owner gives JSON-RPC -32600 (status $code), no loop"
else
  bad "expected -32600, got $code: $(head -c 200 "$body")"
fi

# 4. And the owner still serves it, so none of the above broke the session.
code=$(curl -s -o "$body" -w '%{http_code}' -X POST "$A_URL/mcp" \
  "${AUTH[@]}" -H "$CT" -H "$ACCEPT" -H "Mcp-Session-Id: $SID" -d "$LIST")

if [ "$code" = "200" ] && grep -q '"execute_cad"' "$body"; then
  ok "A still serves its own session (status $code)"
else
  bad "A failed to serve its own session: $code $(head -c 200 "$body")"
fi

echo
if [ "$fail" -ne 0 ]; then
  echo "FAILED: session affinity is broken across machines"
  exit 1
fi
echo "PASSED: affinity routes correctly across two machines"
