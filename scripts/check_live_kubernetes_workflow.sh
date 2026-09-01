#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
RUN_ID="${RUN_ID:-$(date +%s)-$$-${RANDOM:-0}}"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-240}"
LIVE_K8S_RESOURCE="${LIVE_K8S_RESOURCE:-nodes}"
LIVE_K8S_NAMESPACE="${LIVE_K8S_NAMESPACE:-all}"
LIVE_K8S_NAME="${LIVE_K8S_NAME:-}"
LIVE_K8S_PROMPT="${LIVE_K8S_PROMPT:-}"
LIVE_K8S_EXPECTED_TOOLS="${LIVE_K8S_EXPECTED_TOOLS:-ssh_kubectl_get}"

if [[ "${RUN_LIVE_K8S:-0}" != "1" ]]; then
  cat >&2 <<'EOF'
This check reaches the configured live model, MCP/SSH transport, and Kubernetes cluster.
Re-run with RUN_LIVE_K8S=1 after verifying that the target is read-only and correctly configured.
EOF
  exit 2
fi

if [[ -n "$LIVE_K8S_NAME" && "$LIVE_K8S_EXPECTED_TOOLS" == "ssh_kubectl_get" ]]; then
  LIVE_K8S_EXPECTED_TOOLS="ssh_kubectl_get,ssh_kubectl_describe"
fi

post_json() {
  local url="$1"
  local body="$2"
  curl -fsS -X POST "$url" -H "Content-Type: application/json" --data "$body"
}

json_value() {
  local expression="$1"
  python -c 'import json, sys
payload = json.load(sys.stdin)
value = payload
for part in sys.argv[1].split("."):
    if part:
        value = value[part]
print(value)' "$expression"
}

build_prompt() {
  if [[ -n "$LIVE_K8S_PROMPT" ]]; then
    printf '%s' "$LIVE_K8S_PROMPT"
  elif [[ -n "$LIVE_K8S_NAME" ]]; then
    printf 'Use only read-only Kubernetes tools. First list %s in namespace %s, then describe the exact resource named %s, and report the evidence.' \
      "$LIVE_K8S_RESOURCE" "$LIVE_K8S_NAMESPACE" "$LIVE_K8S_NAME"
  else
    printf 'Use only read-only Kubernetes tools to list %s in namespace %s and report the observed state. Do not modify the cluster.' \
      "$LIVE_K8S_RESOURCE" "$LIVE_K8S_NAMESPACE"
  fi
}

message_send_payload() {
  local prompt="$1"
  python - "$RUN_ID" "$prompt" <<'PY'
import json
import sys

run_id, prompt = sys.argv[1:3]
print(json.dumps({
    "jsonrpc": "2.0",
    "id": f"live-k8s-{run_id}",
    "method": "message/send",
    "params": {
        "message": {
            "kind": "message",
            "role": "user",
            "messageId": f"msg-live-k8s-{run_id}",
            "parts": [{"kind": "text", "text": prompt}],
        },
        "metadata": {"executionMode": "task"},
    },
}, separators=(",", ":")))
PY
}

wait_for_terminal_task() {
  local task_id="$1"
  local deadline=$((SECONDS + TIMEOUT_SECONDS))
  local response
  local state

  while ((SECONDS < deadline)); do
    response="$(
      post_json "$BASE_URL/rpc" \
        "{\"jsonrpc\":\"2.0\",\"id\":\"get-live-k8s-$RUN_ID\",\"method\":\"tasks/get\",\"params\":{\"id\":\"$task_id\"}}"
    )"
    state="$(printf '%s' "$response" | json_value "result.status.state")"
    case "$state" in
      completed)
        printf '%s' "$response"
        return 0
        ;;
      failed|canceled|rejected|input-required|auth-required)
        echo "live Kubernetes Task reached unexpected terminal state: id=$task_id state=$state" >&2
        echo "$response" >&2
        return 1
        ;;
    esac
    sleep 1
  done

  echo "live Kubernetes Task did not finish within ${TIMEOUT_SECONDS}s: id=$task_id" >&2
  return 1
}

prompt="$(build_prompt)"
echo "Live Kubernetes workflow: backend=$BASE_URL resource=$LIVE_K8S_RESOURCE namespace=$LIVE_K8S_NAMESPACE"
echo "Prompt: $prompt"

task_response="$(post_json "$BASE_URL/rpc" "$(message_send_payload "$prompt")")"
task_id="$(printf '%s' "$task_response" | json_value "result.id")"
if [[ -z "$task_id" ]]; then
  echo "A2A response did not include a Task id: $task_response" >&2
  exit 1
fi

wait_for_terminal_task "$task_id" >/dev/null
observations_response="$(curl -fsS "$BASE_URL/api/tasks/$task_id/observations")"
observations_file="$(mktemp "${TMPDIR:-/tmp}/vermay-live-k8s-observations.XXXXXX")"
trap 'rm -f "$observations_file"' EXIT
printf '%s' "$observations_response" >"$observations_file"

python - "$LIVE_K8S_EXPECTED_TOOLS" "$observations_file" <<'PY'
import json
import sys

expected_tools = {value.strip() for value in sys.argv[1].split(",") if value.strip()}
with open(sys.argv[2], encoding="utf-8") as stream:
    payload = json.load(stream)
observations = payload.get("observations")
if not isinstance(observations, list) or not observations:
    raise SystemExit("live Task completed without normalized tool observations")

errors = [
    item for item in observations
    if isinstance(item, dict) and item.get("error_category") in {"tool_argument_error", "budget_exhausted"}
]
if errors:
    raise SystemExit(f"live Task retained correction or budget errors: {errors}")

execution = payload.get("execution")
if isinstance(execution, dict) and execution.get("stop_reason") == "budget_exhausted":
    raise SystemExit(f"live Task exhausted its execution budget: {execution}")

successful = [item for item in observations if isinstance(item, dict) and item.get("ok") is True]
observed_tools = {str(item.get("tool_name") or "") for item in successful}
missing = sorted(expected_tools - observed_tools)
if missing:
    raise SystemExit(f"expected successful tool observations are missing: {missing}; observed={sorted(observed_tools)}")

seen = set()
duplicates = []
for item in successful:
    fingerprint = (
        str(item.get("tool_name") or ""),
        json.dumps(item.get("structured_data"), ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )
    if fingerprint in seen:
        duplicates.append(item.get("tool_name"))
    seen.add(fingerprint)
if duplicates:
    raise SystemExit(f"repeated identical successful tool observations detected: {duplicates}")

without_evidence = [
    item.get("tool_name") for item in successful
    if not item.get("summary") and item.get("structured_data") is None
]
if without_evidence:
    raise SystemExit(f"successful tool observations lack durable evidence: {without_evidence}")

print(
    f"validated {len(observations)} observation(s); "
    f"successful tools={','.join(sorted(observed_tools))}"
)
PY

echo "Live Kubernetes workflow passed: task=$task_id"
