#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
BFF_URL="${BFF_URL:-}"
CHILD_AGENT_A2A_BASE_URL="${CHILD_AGENT_A2A_BASE_URL:-}"
CHILD_AGENT_CARD_URL="${CHILD_AGENT_CARD_URL:-}"
CHILD_AGENT_ID="${CHILD_AGENT_ID:-}"
RUN_ID="${RUN_ID:-$(date +%s)-$$-${RANDOM:-0}}"

json_value() {
  local expression="$1"
  python -c 'import json, sys
payload = json.load(sys.stdin)
value = payload
for part in sys.argv[1].split("."):
    if not part:
        continue
    value = value[part]
print(value)' "$expression"
}

require_contains() {
  local haystack="$1"
  local needle="$2"
  if [[ "$haystack" != *"$needle"* ]]; then
    echo "expected output to contain: $needle" >&2
    echo "$haystack" >&2
    exit 1
  fi
}

require_http_status() {
  local expected="$1"
  local url="$2"
  local method="${3:-GET}"
  local status
  status="$(curl -sS -o /dev/null -w '%{http_code}' -X "$method" "$url")"
  if [[ "$status" != "$expected" ]]; then
    echo "expected $method $url to return $expected, got $status" >&2
    exit 1
  fi
}

post_json() {
  local url="$1"
  local body="$2"
  curl -fsS -X POST "$url" -H "Content-Type: application/json" --data "$body"
}

message_send_payload() {
  local request_id="$1"
  local message_id="$2"
  local text="$3"
  local execution_mode="$4"
  python - "$request_id" "$message_id" "$text" "$execution_mode" <<'PY'
import json
import sys

request_id, message_id, text, execution_mode = sys.argv[1:5]
print(json.dumps({
    "jsonrpc": "2.0",
    "id": request_id,
    "method": "message/send",
    "params": {
        "message": {
            "kind": "message",
            "role": "user",
            "messageId": message_id,
            "parts": [{"kind": "text", "text": text}],
        },
        "metadata": {"executionMode": execution_mode},
    },
}, separators=(",", ":")))
PY
}

registered_agent_payload() {
  local agent_id="$1"
  local name="$2"
  local card_url="$3"
  python - "$agent_id" "$name" "$card_url" <<'PY'
import json
import sys

agent_id, name, card_url = sys.argv[1:4]
print(json.dumps({
    "agent_id": agent_id,
    "name": name,
    "card_url": card_url,
    "enabled": True,
    "metadata": {"keywords": ["child-smoke"]},
}, separators=(",", ":")))
PY
}

wait_for_completed_task() {
  local task_id="$1"
  local timeout_seconds="${2:-180}"
  local deadline=$((SECONDS + timeout_seconds))
  local response
  local state

  while ((SECONDS < deadline)); do
    response="$(
      post_json "$BASE_URL/rpc" "{\"jsonrpc\":\"2.0\",\"id\":\"get-smoke-$RUN_ID\",\"method\":\"tasks/get\",\"params\":{\"id\":\"$task_id\"}}"
    )"
    state="$(printf '%s' "$response" | json_value "result.status.state")"
    case "$state" in
      completed)
        printf '%s' "$response"
        return 0
        ;;
      failed|canceled|rejected|input-required|auth-required)
        echo "task reached unexpected state while waiting for completion: id=$task_id state=$state" >&2
        echo "$response" >&2
        return 1
        ;;
    esac
    sleep 1
  done

  echo "task did not complete within ${timeout_seconds}s: id=$task_id" >&2
  return 1
}

echo "A2A smoke: backend=$BASE_URL"
echo "Checking canonical /rpc surface"

agent_card="$(curl -fsS "$BASE_URL/.well-known/agent-card.json")"
printf '%s' "$agent_card" | python -c 'import json, sys
card = json.load(sys.stdin)
assert card.get("protocolVersion") == "0.3.0", card
assert card.get("preferredTransport") == "JSONRPC", card
assert card.get("url", "").endswith("/rpc"), card
extensions = card.get("capabilities", {}).get("extensions", [])
if not any(
    item.get("uri") == "urn:vermay:a2a:task-approval-resume:0.1"
    and item.get("params", {}).get("method") == "tasks/resume"
    for item in extensions
    if isinstance(item, dict)
):
    raise SystemExit(f"missing Vermay task-resume extension: {card}")'

message_response="$(
  post_json "$BASE_URL/rpc" "$(
    message_send_payload "smoke-message-$RUN_ID" "msg-smoke-message-$RUN_ID" "hello smoke" "message"
  )"
)"
message_kind="$(printf '%s' "$message_response" | json_value "result.kind")"
if [[ "$message_kind" != "message" ]]; then
  echo "expected message result, got: $message_kind" >&2
  exit 1
fi

task_response="$(
  post_json "$BASE_URL/rpc" "$(
    message_send_payload "smoke-task-$RUN_ID" "msg-smoke-task-$RUN_ID" "Reply with exactly: task smoke passed" "task"
  )"
)"
task_id="$(printf '%s' "$task_response" | json_value "result.id")"
task_state="$(printf '%s' "$task_response" | json_value "result.status.state")"
if [[ -z "$task_id" || "$task_state" == "failed" || "$task_state" == "canceled" || "$task_state" == "rejected" ]]; then
  echo "expected accepted task, got id=$task_id state=$task_state" >&2
  exit 1
fi

get_response="$(wait_for_completed_task "$task_id")"
get_task_id="$(printf '%s' "$get_response" | json_value "result.id")"
if [[ "$get_task_id" != "$task_id" ]]; then
  echo "task get mismatch: expected=$task_id got=$get_task_id" >&2
  exit 1
fi

rpc_stream_response="$(
  post_json "$BASE_URL/rpc" "$(
    message_send_payload "rpc-stream-smoke-$RUN_ID" "msg-rpc-stream-smoke-$RUN_ID" "Reply with exactly: stream smoke passed" "task" \
      | python -c 'import json, sys
payload = json.load(sys.stdin)
payload["method"] = "message/stream"
print(json.dumps(payload, separators=(",", ":")))'
  )"
)"
require_contains "$rpc_stream_response" "event: task"
require_contains "$rpc_stream_response" "event: artifact-update"
require_contains "$rpc_stream_response" "event: status-update"
require_contains "$rpc_stream_response" "\"id\": \"rpc-stream-smoke-$RUN_ID\""

rpc_subscribe_response="$(
  post_json "$BASE_URL/rpc" "{\"jsonrpc\":\"2.0\",\"id\":\"rpc-subscribe-smoke-$RUN_ID\",\"method\":\"tasks/resubscribe\",\"params\":{\"id\":\"$task_id\",\"afterEventId\":0}}"
)"
require_contains "$rpc_subscribe_response" "event: artifact-update"
require_contains "$rpc_subscribe_response" "event: status-update"
require_contains "$rpc_subscribe_response" "\"id\": \"rpc-subscribe-smoke-$RUN_ID\""

rpc_cancel_error="$(
  curl -sS -X POST "$BASE_URL/rpc" \
    -H "Content-Type: application/json" \
    --data "{\"jsonrpc\":\"2.0\",\"id\":\"rpc-cancel-smoke-$RUN_ID\",\"method\":\"tasks/cancel\",\"params\":{\"id\":\"$task_id\",\"reason\":\"too late\"}}"
)"
require_contains "$rpc_cancel_error" "\"id\":\"rpc-cancel-smoke-$RUN_ID\""
require_contains "$rpc_cancel_error" '"localCode":"invalid_session_state"'
require_contains "$rpc_cancel_error" '"errorInfo"'

echo "A2A backend smoke passed: task=$task_id"

require_http_status 404 "$BASE_URL/message:send" POST
require_http_status 404 "$BASE_URL/message:stream" POST
require_http_status 404 "$BASE_URL/tasks/$task_id"
require_http_status 404 "$BASE_URL/tasks/${task_id}:cancel" POST
require_http_status 404 "$BASE_URL/tasks/${task_id}:resume" POST
require_http_status 404 "$BASE_URL/tasks/${task_id}:subscribe" POST
require_http_status 404 "$BASE_URL/api/sessions"
require_http_status 404 "$BASE_URL/api/tasks/$task_id"
require_http_status 404 "$BASE_URL/api/tasks/$task_id/events"

if [[ -n "$CHILD_AGENT_A2A_BASE_URL" ]]; then
  child_agent_base="${CHILD_AGENT_A2A_BASE_URL%/}"
  child_agent_id="${CHILD_AGENT_ID:-agent-child-smoke-$RUN_ID}"
  child_agent_card_url="${CHILD_AGENT_CARD_URL:-$child_agent_base/.well-known/agent-card.json}"

  echo "Checking registered child-agent delegation: child=$child_agent_base"

  post_json "$BASE_URL/api/registered-agents" "$(
    registered_agent_payload "$child_agent_id" "Smoke child agent" "$child_agent_card_url"
  )" >/dev/null

  child_delegate_response="$(
    post_json "$BASE_URL/rpc" "$(
      message_send_payload "rpc-child-smoke-$RUN_ID" "msg-rpc-child-smoke-$RUN_ID" "delegate smoke via child agent" "message" \
        | python -c 'import json, sys
target_agent_id = sys.argv[1]
payload = json.load(sys.stdin)
payload["params"]["metadata"]["route"] = "remote_agent"
payload["params"]["metadata"]["targetAgentId"] = target_agent_id
print(json.dumps(payload, separators=(",", ":")))' "$child_agent_id"
    )"
  )"
  child_delegate_kind="$(printf '%s' "$child_delegate_response" | json_value "result.kind")"
  child_delegate_route_kind="$(printf '%s' "$child_delegate_response" | json_value "result.metadata.routeKind")"
  child_delegate_remote_agent_id="$(printf '%s' "$child_delegate_response" | json_value "result.metadata.remoteAgentId")"
  child_delegate_context_id="$(printf '%s' "$child_delegate_response" | json_value "result.contextId")"
  if [[ "$child_delegate_kind" != "message" || "$child_delegate_route_kind" != "remote_agent" ]]; then
    echo "expected child delegation message result, got: $child_delegate_response" >&2
    exit 1
  fi
  if [[ "$child_delegate_remote_agent_id" != "$child_agent_id" ]]; then
    echo "child delegation remote agent mismatch: expected=$child_agent_id got=$child_delegate_remote_agent_id" >&2
    exit 1
  fi

  child_delegations="$(curl -fsS "$BASE_URL/api/contexts/$child_delegate_context_id/delegations")"
  printf '%s' "$child_delegations" | python -c 'import json, sys
delegations = json.load(sys.stdin)
target_agent_id = sys.argv[1]
if not any(item.get("remote_agent_id") == target_agent_id for item in delegations):
    raise SystemExit(f"expected delegation for {target_agent_id}, got: {delegations}")' "$child_agent_id"

  curl -fsS -X DELETE "$BASE_URL/api/registered-agents/$child_agent_id" >/dev/null || true
  echo "A2A child-agent delegation smoke passed: child=$child_agent_id"
fi

if [[ -n "$BFF_URL" ]]; then
  echo "A2A BFF smoke: bff=$BFF_URL"

  bff_error="$(curl -sS "$BFF_URL/api/bff/agent/a2a/tasks/missing-task")"
  require_contains "$bff_error" '"message":"task not found"'
  require_contains "$bff_error" '"code":"task_not_found"'

  bff_message="$(
    post_json "$BFF_URL/api/bff/agent/a2a/message" '{
      "text": "hello bff message smoke",
      "executionMode": "message"
    }'
  )"
  bff_message_kind="$(printf '%s' "$bff_message" | json_value "kind")"
  if [[ "$bff_message_kind" != "message" ]]; then
    echo "expected BFF message result, got: $bff_message_kind" >&2
    exit 1
  fi

  bff_stream="$(
    post_json "$BFF_URL/api/bff/agent/a2a/message-stream" '{
      "text": "run bff smoke task",
      "executionMode": "task"
    }'
  )"
  require_contains "$bff_stream" "event: task"
  require_contains "$bff_stream" "event: artifact-update"
  require_contains "$bff_stream" "Dev mock task completed: run bff smoke task"

  bff_task="$(
    post_json "$BFF_URL/api/bff/agent/a2a/message" '{
      "text": "run bff cancel smoke task",
      "executionMode": "task"
    }'
  )"
  bff_task_id="$(printf '%s' "$bff_task" | json_value "task.id")"
  bff_task_snapshot="$(curl -fsS "$BFF_URL/api/bff/agent/a2a/tasks/$bff_task_id")"
  bff_task_snapshot_id="$(printf '%s' "$bff_task_snapshot" | json_value "id")"
  if [[ "$bff_task_snapshot_id" != "$bff_task_id" ]]; then
    echo "BFF task snapshot mismatch: expected=$bff_task_id got=$bff_task_snapshot_id" >&2
    exit 1
  fi
  bff_events="$(curl -fsS "$BFF_URL/api/bff/agent/a2a/tasks/$bff_task_id/events")"
  require_contains "$bff_events" "event: artifact-update"
  require_contains "$bff_events" "event: status-update"
  bff_cancel_error="$(
    curl -sS -X POST "$BFF_URL/api/bff/agent/a2a/tasks/$bff_task_id/cancel" \
      -H "Content-Type: application/json" \
      --data '{"reason":"too late"}'
  )"
  require_contains "$bff_cancel_error" '"status":409'
  require_contains "$bff_cancel_error" '"code":"invalid_session_state"'

  require_http_status 404 "$BFF_URL/api/bff/agent/sessions"
  require_http_status 404 "$BFF_URL/api/bff/agent/tasks/$bff_task_id"

  echo "A2A BFF smoke passed"
fi
