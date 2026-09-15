#!/usr/bin/env bash
set -euo pipefail

# Start Claw Code on VM121 through the local OpenAI-compatible Kimi gateway.
# Kimi traffic leaves VM121 through the systemd-managed SOCKS tunnel so it
# uses the same public IP as the registered Windows Chrome session.
# Examples:
#   /work/claster/vm121/vm121-claw-kimi.sh
#   /work/claster/vm121/vm121-claw-kimi.sh --help
#   /work/claster/vm121/vm121-claw-kimi.sh --output-format json prompt 'Say READY'

ACCESS_FILE="${ACCESS_FILE:-/work/claster/llm-gpu-storage-cluster-20260527/private/access.md}"
JUMP_KEY="${JUMP_KEY:-/root/.ssh/ke_tunnel}"
JUMP_HOST="${JUMP_HOST:-178.253.55.128}"
SRV1_HOST="${SRV1_HOST:-134.0.107.8}"
SRV1_PORT="${SRV1_PORT:-234}"
VM121_HOST="${VM121_HOST:-192.168.87.33}"
VM121_USER="${VM121_USER:-ubuntu}"
VM121_KEY_ON_SRV1="${VM121_KEY_ON_SRV1:-/root/.ssh/vm-121-claw-second-llm-ed25519}"
VM121_WORKDIR="${VM121_WORKDIR:-/home/ubuntu/second-llm}"
KIMI_BASE_URL="${KIMI_BASE_URL:-http://127.0.0.1:18081/v1}"
CLAW_KIMI_MAX_OUTPUT_TOKENS="${CLAW_KIMI_MAX_OUTPUT_TOKENS:-32000}"
CLAW_AUTO_COMPACT_INPUT_TOKENS="${CLAW_AUTO_COMPACT_INPUT_TOKENS:-64000}"

need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing command: $1" >&2; exit 127; }; }
need ssh
need sshpass
need python3

if [[ ! -r "$ACCESS_FILE" ]]; then
  echo "Cannot read access file: $ACCESS_FILE" >&2
  exit 1
fi
if [[ ! -r "$JUMP_KEY" ]]; then
  echo "Cannot read jump key: $JUMP_KEY" >&2
  exit 1
fi

TMPDIR="$(mktemp -d)"
cleanup() { rm -rf "$TMPDIR"; }
trap cleanup EXIT
PASSFILE="$TMPDIR/srv1.pass"
python3 - <<'PY' "$ACCESS_FILE" "$PASSFILE"
from pathlib import Path
import sys
access = Path(sys.argv[1])
out = Path(sys.argv[2])
pw = None
for line in access.read_text(errors='replace').splitlines():
    if '| GPU node 1 ' in line:
        cells = [c.strip().strip('`') for c in line.strip().strip('|').split('|')]
        if len(cells) >= 6:
            pw = cells[5]
            break
if not pw:
    raise SystemExit('srv1 credential not found in access file')
out.write_text(pw, encoding='utf-8')
out.chmod(0o600)
PY

JUMP_CMD="ssh -i $JUMP_KEY -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/tmp/omx_jump_known_hosts -W %h:%p root@$JUMP_HOST"
BASE_SSH=(
  sshpass -f "$PASSFILE" ssh
  -o PreferredAuthentications=password,keyboard-interactive
  -o PubkeyAuthentication=no
  -o NumberOfPasswordPrompts=1
  -o StrictHostKeyChecking=accept-new
  -o UserKnownHostsFile=/tmp/omx_target_known_hosts
  -o ConnectTimeout=10
  -o ServerAliveInterval=10
  -o ServerAliveCountMax=3
  -o ProxyCommand="$JUMP_CMD"
  -p "$SRV1_PORT"
  root@"$SRV1_HOST"
)

remote_cmd="cd $(printf '%q' "$VM121_WORKDIR") && exec env"
remote_cmd+=" GOOGLE_BASE_URL=$(printf '%q' "$KIMI_BASE_URL")"
remote_cmd+=" CLAW_MAX_TOKENS=$(printf '%q' "$CLAW_KIMI_MAX_OUTPUT_TOKENS")"
remote_cmd+=" CLAUDE_CODE_AUTO_COMPACT_INPUT_TOKENS=$(printf '%q' "$CLAW_AUTO_COMPACT_INPUT_TOKENS")"
remote_cmd+=" run-claw-kimi"
for arg in "$@"; do
  remote_cmd+=" $(printf '%q' "$arg")"
done
srv1_cmd="ssh -tt -i $(printf '%q' "$VM121_KEY_ON_SRV1") -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new $(printf '%q' "$VM121_USER@$VM121_HOST") $(printf '%q' "$remote_cmd")"

exec "${BASE_SSH[@]}" -tt "$srv1_cmd"
