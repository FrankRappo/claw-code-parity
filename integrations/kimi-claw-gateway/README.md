# Kimi Claw Gateway

A localhost-only, OpenAI-compatible gateway that lets Claw Code use a logged-in
Kimi web account. It supports both streaming and non-streaming
`/v1/chat/completions`, refreshes the short-lived Kimi access token, and sends
Kimi traffic directly or through an optional persistent SOCKS5 egress tunnel.

> This is an unofficial integration built against Kimi's web API. Web endpoints
> can change without notice. Use it only with an account and infrastructure you
> control, and follow the applicable service terms.

## Verified architecture

```text
Claw Code
  -> http://127.0.0.1:18081/v1/chat/completions
  -> Kimi gateway (Windows process or kimi-api.service)
  -> direct HTTPS, or socks5://127.0.0.1:1081
  -> optional restricted SSH tunnel / matching VPS egress
  -> www.kimi.com / auth.kimi.com
```

The API binds to loopback by default. The VPS SSH account should be an
unprivileged forwarding-only account; do not put a root password or browser
profile in this repository.

## Features

- OpenAI-compatible `POST /v1/chat/completions`
- OpenAI SSE streaming with a final `[DONE]`
- `GET /v1/models` and `GET /health`
- automatic token refresh through Kimi's current auth service
- atomic `0600` session updates
- Microsoft Edge session import without storing a Google password
- Windows DPAPI protection and user-only ACLs for the live session
- bearer authentication between Claw and the loopback gateway
- persistent Kimi chats keyed per Claw process, with context-aware rotation
- FIFO request pacing (10 requests/minute means at least 6 seconds between starts)
- all active Claw tool names registered as native Kimi device tools, with compact schemas
- strict agent mode that separates informational turns from action turns
- SOCKS5 proxy support through `httpx[socks]`
- systemd hardening and restart policies
- Claw Code runner and Windows/WSL launcher examples
- Kimi marker to OpenAI `tool_calls` translation for Claw Bash/Read/Write/Edit loops
- bounded retry/backoff for transport errors, HTTP 408/429, and upstream 5xx
- configurable upstream concurrency semaphore (default: 2)
- `/ready` upstream readiness probe and `/metrics` reliability counters

## What was adopted from `kimi-reverse-api`

This project uses the useful architectural idea from
[`aryaniiil/kimi-reverse-api`](https://github.com/aryaniiil/kimi-reverse-api):
obtain an already authenticated browser session once, persist it locally, and
talk directly to Kimi's backend with `httpx` rather than automate the page for
every message.

The old implementation itself is not copied. It targets the legacy
`kimi.moonshot.cn` `/api/chat/.../completion/stream` flow, stores credentials in
plain text, and automates Google login. This gateway instead uses the current
`www.kimi.com` Connect v2 transport, Microsoft Edge session capture, refresh
tokens, DPAPI at rest, OpenAI-compatible streaming, and the Claw tool bridge.

Подробная русскоязычная методика reverse engineering, отладки gateway и переноса
архитектуры на другой web-провайдер (включая GLM) находится в
[`docs/REVERSE_GATEWAY_PLAYBOOK_RU.md`](docs/REVERSE_GATEWAY_PLAYBOOK_RU.md).

## Windows quick start (Claw parity checkout)

The verified target layout is:

```text
C:\claw cod\
├── kimi-claw-gateway\
└── claw-code-parity — копия (2)\
```

Gemma remains unchanged. Kimi is a second, process-local provider started only
through `claw-kimi.cmd`.

Install the gateway:

```powershell
cd 'C:\claw cod\kimi-claw-gateway'
powershell -ExecutionPolicy Bypass -File .\windows\install.ps1
```

Import a Kimi login from Edge. The safest default opens a dedicated Edge profile
used only for Kimi; sign in once in the opened window:

```powershell
powershell -ExecutionPolicy Bypass -File .\windows\import-edge-session.ps1
```

To import the already logged-in normal Edge `Default` profile, first close all
Edge windows so Chromium cannot corrupt the profile, then run:

```powershell
powershell -ExecutionPolicy Bypass -File .\windows\import-edge-session.ps1 `
  -ProfileMode Existing -ProfileDirectory Default
```

The importer opens a loopback-only, temporary DevTools port, captures only
Kimi/Moonshot cookies plus Kimi auth/device metadata, writes
`%LOCALAPPDATA%\KimiClawGateway\session.dpapi`, and closes the debugging browser.
It never reads or stores the Google password. The DPAPI file can be decrypted
only by the same Windows account.

Copy the launcher into the Claw checkout and start a Kimi session:

```powershell
Copy-Item .\windows\claw-kimi.cmd `
  'C:\claw cod\claw-code-parity — копия (2)\claw-kimi.cmd' -Force
cd 'C:\claw cod\claw-code-parity — копия (2)'
.\claw-kimi.cmd
```

The launcher derives a stable `kimi-k2d6-workspace-<hash>` marker from the
current working directory. Restarting Claw and resuming a local session from the
same workspace therefore continues the same Kimi chat instead of opening a chat
for every process. A caller can still set `KIMI_CLAW_SESSION` explicitly when a
workspace needs multiple independent lanes. The stable Windows upstream profile
is `k2d6-chat` with `SCENARIO_CHAT`.

## Persistent chat and context rotation

With `KIMI_PERSISTENT_CHAT=1`, the first Claw request creates a real Kimi chat.
Later requests reuse its `chat_id`. Although Claw sends the complete message
array each turn, the gateway stores only SHA-256 message fingerprints and sends
Kimi only the new tail: the next user message or tool result. This makes the Kimi
side resemble a normal sequential chat instead of repeated transcript dumps.

A different workspace/session marker, `/clear`, an unrelated replacement
history, or a context estimate above `KIMI_MAX_PERSISTENT_CONTEXT_TOKENS`
creates a new Kimi chat. Normal Claw automatic compaction does not rotate the
upstream chat: the gateway recognizes Claw's synthetic summary and the exact
preserved tail, then sends only the new user/tool delta to the existing
`chat_id`. On a genuine context rotation the current compacted Claw history is
sent to the new chat, so the working conversation continues. Rotation resets
that chat's context window; it does not reset an account-level daily or usage
quota.

Detailed Russian design, failure modes, limits, and verification procedure:
[`docs/PERSISTENT_CHAT_RU.md`](docs/PERSISTENT_CHAT_RU.md).

## Request pacing

The gateway queues requests instead of rejecting the second request. Starts are
evenly spaced according to `KIMI_REQUESTS_PER_MINUTE`:

```text
10 requests/minute -> minimum 6.0 seconds between request starts
6 requests/minute  -> minimum 10.0 seconds between request starts
```

The Windows launcher also sets `KIMI_MAX_CONCURRENT_UPSTREAM=1`. Retries are
bounded separately and use exponential backoff.

## Kimi K3 compatibility status

K3 is implemented as an optional web profile but is not the stable Windows
default yet. A live `GetAvailableModels` response on 2026-09-02 exposed:

```text
model:             k3-agent
scenario:          SCENARIO_OK_COMPUTER
kimiPlusId:        ok-computer
reasoning:         LOW / HIGH (default) / MAX
standard context:  CONTEXT_LENGTH_L
extra-long:        CONTEXT_LENGTH_XL (Allegro plan only, up to 1M tokens)
```

Unlike K2.6, the first K3 request must use an empty provisional `chatId`; Kimi
creates the chat inside the Connect stream and returns its id in a `chat` event.
The gateway supports this lifecycle and reads the stream through the final
Connect frame rather than stopping at the earlier application-level `done`.

The current account accepted the corrected K3 payload with HTTP 200, but the
final Connect frame returned:

```text
resource_exhausted
REASON_SERVER_OVERLOADED_FOR_FREE_USER
Too many people are chatting with Kimi right now. Subscribe to enter a priority queue!
```

Because K3 did not reach model generation, its full Claw
`DEVICE_TOOL -> local execution -> tool result` loop could not be validated
end-to-end. The Windows launcher therefore remains on the already verified
K2.6 agent profile. The fresh Edge session tokens are account-level and work for
both profiles; switching models does not require recapturing or copying tokens.
The gateway does not silently substitute K2.6 when explicitly configured for
K3.

## Claw tool bridge

Claw sends its complete system prompt and OpenAI tool registry to the gateway.
The gateway preserves the full Claw system prompt and sends it through Kimi's
native Connect `options.systemPrompt` field. In stateless mode conversation
history remains in the user message; in persistent mode only the unsynchronized
message tail is sent. In both cases history does not compete with system
instructions at the same priority.

Kimi's current protobuf exposes 13 built-in `ToolType` categories, but its
ordinary chat request has no OpenAI-style arbitrary function schema.
`TOOL_TYPE_DEVICE_TOOL` accepts a name and makes the web model emit native syntax
such as `<function_calls>...</function_calls>` or `<bash>...</bash>`. The gateway
registers every tool from the actual Claw request under its native name and adds
the compact schemas for the full registry to the system prompt. Kimi chooses the
tool; the gateway validates the selected name against the current registry,
normalizes harmless generated prefixes/name layouts such as `bridge_read_file`,
`claw_read_file`, and `FileRead`, and converts the result to an OpenAI
`tool_call`. Unknown or ambiguous names are rejected. Text after the closing tag
is discarded so a fabricated result cannot be accepted.

Kimi tool markers such as `functions.Bash` are parsed into real OpenAI
`tool_calls`, including streaming deltas and `finish_reason=tool_calls`. Tool
results returned by Claw are rendered into the next Kimi request so multi-turn
tool loops can continue. Invalid JSON, invalid tool names, and simulated textual
tool calls are not treated as successful execution.

Explicit tool choices from the client remain hard protocol requirements. For
normal `tool_choice=auto` turns, Kimi alone decides whether to answer or call a
tool. The gateway performs no semantic task classification, completion decision,
or tool selection; it only validates and translates the returned wire format.
The older marker parser remains as a compatibility fallback.

When a tool is required, one non-empty text-only response is corrected in the
same upstream chat. Two consecutive text-only responses indicate a stuck chat:
the gateway creates a fresh upstream chat, replays the current Claw conversation,
and continues the bounded repair loop. This also prevents a strict `/agent on`
turn from ending as `assistant stream produced no content` merely because one
upstream chat stopped following the native tool protocol.

If every bounded retry still returns only text for the generic
`tool_choice=required` contract, the gateway returns one real, harmless
workspace-location probe (`PowerShell: Get-Location`, or `Bash: pwd`) when that
exact shell and its one-command schema are present in the client's live tool
registry. Claw executes the probe and the normal tool-result loop continues.
The gateway never synthesizes a client-forced specific tool and never selects a
write or destructive tool as recovery. The event is counted as
`required_any_tool_fallbacks_total` by `/metrics`.

Do not treat a tool list written by the model as authoritative: repeated live
queries produced different self-reported names. The verified Windows runtime
request exposes 49 tools; only that actual per-request registry is used for
validation and routing.

Streaming requests send the initial OpenAI SSE role chunk immediately and then
run Kimi tool preflight inside the stream. This prevents Claw from retrying a
slow model turn and opening duplicate Kimi chats. Corrective retries reuse the
same upstream chat. If all attempts fail and the safe generic shell fallback is
not applicable (for example, a specifically forced tool was refused), the stream
contains a typed `upstream_error` event and terminates with `[DONE]`.

The web transport does not report tokenizer usage, so the gateway emits a
conservative UTF-8 byte estimate in OpenAI `usage` fields for streaming and
non-streaming responses. This keeps Claw's context meter non-zero and allows its
configured automatic compaction threshold to activate before a long tool loop
reaches the free web route's `resource_exhausted` boundary.

For accuracy, the newest tool result is retained up to 16K characters when Kimi
forms its checkpoint answer, while older results are reduced to 800 characters.
This preserves one complete project source file without repeatedly resending all
previous files.

## Install

```bash
sudo useradd --system --create-home --home-dir /var/lib/kimi-claw-gateway kimi-gateway
sudo install -d -o kimi-gateway -g kimi-gateway -m 700 /opt/kimi-claw-gateway /var/lib/kimi-claw-gateway
sudo cp -a . /opt/kimi-claw-gateway/
sudo -u kimi-gateway python3 -m venv /opt/kimi-claw-gateway/.venv
sudo -u kimi-gateway /opt/kimi-claw-gateway/.venv/bin/pip install -r /opt/kimi-claw-gateway/requirements.txt
```

Place the browser-derived Kimi session at:

```text
/var/lib/kimi-claw-gateway/session.json
```

Use `config/session.example.json` only as a schema reference. The live file must
be owned by `kimi-gateway` and have mode `0600`. Never commit it.

Configure the dedicated SSH tunnel using
`deploy/kimi-egress-socks.env.example`, pin the VPS host key, then install the
units:

```bash
sudo cp deploy/kimi-egress-socks.service deploy/kimi-api.service /etc/systemd/system/
sudo cp deploy/kimi-egress-socks.env.example /etc/kimi-egress-socks.env
sudo chmod 600 /etc/kimi-egress-socks.env
sudo systemctl daemon-reload
sudo systemctl enable --now kimi-egress-socks.service kimi-api.service
```

## API smoke test

```bash
curl -fsS http://127.0.0.1:18081/health

curl -fsS http://127.0.0.1:18081/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "kimi-web",
    "messages": [{"role": "user", "content": "Reply with exactly READY"}],
    "stream": false
  }'
```

Streaming:

```bash
curl -N http://127.0.0.1:18081/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"kimi-web","messages":[{"role":"user","content":"Reply with exactly READY"}],"stream":true}'
```

## Claw Code

```bash
install -m 755 bin/run-claw-kimi /usr/local/bin/run-claw-kimi
run-claw-kimi --output-format text prompt 'Reply with exactly READY'
```

The generic runner uses the existing OpenAI-compatible provider path exposed
by `run-claw-gemma2`, while overriding its base URL to the Kimi gateway.

### Native Windows Claw parity build

For the Rust Claw build, use the dedicated `windows/claw-kimi.cmd` launcher.
It selects the existing OpenAI-compatible adapter through the `kimi-web` model
prefix and overrides only `GROQ_BASE_URL` for that process. It does not reuse
the Gemma launcher, `GOOGLE_BASE_URL`, or a Gemma-specific external prompt.

Install and start the native Windows gateway:

```powershell
powershell -ExecutionPolicy Bypass -File .\windows\install.ps1
powershell -ExecutionPolicy Bypass -File .\windows\import-edge-session.ps1
powershell -ExecutionPolicy Bypass -File .\windows\start-kimi-gateway.ps1
```

Copy `windows\claw-kimi.cmd` into the root of the Windows Claw parity checkout,
then run:

```powershell
.\claw-kimi.cmd --output-format text prompt "Reply with exactly READY"
```

The launcher reads the random loopback bearer key into process-local
`GROQ_API_KEY`, sets process-local `GROQ_BASE_URL`, and derives a stable Kimi
session marker from the current workspace. The Claw registry already routes
model names beginning with `kimi` through that OpenAI-compatible adapter.
Existing Gemma configuration and `GOOGLE_*` variables are not used or modified.

The Windows scripts use port `18081` by default. A machine-specific override can
be written as a single integer to
`%LOCALAPPDATA%\KimiClawGateway\port.txt`; the start, stop, and Claw launcher
scripts then use that port consistently. Non-default instances keep separate
PID and log files while sharing the protected login session and loopback API
key.

The Kimi launcher sets Claw's automatic-compaction threshold to 200,000
estimated input tokens. This is deliberately below the 262,144-token Kimi for
Coding context window and its own documented 50,000-token reserve. The parity
runtime compacts during a long tool loop, before the next model request, rather
than waiting only for the user turn to finish. The gateway uses the same
200,000-token safety threshold and recognizes the preserved tail so compaction
continues the same upstream web chat instead of creating another one.

### Required Windows Claw runtime fixes

The verified parity checkout includes generic execution fixes, not a
task-specific SOCKS helper:

- `bash` and `PowerShell` accept optional `stdin`;
- timed-out foreground commands terminate descendant processes;
- PowerShell captures output through temporary files, so a detached child does
  not hold an inherited pipe open and block the tool result;
- automatic compaction can run between tool iterations;
- a failed request returns to the interactive prompt instead of closing Claw;
- `Esc` cancels the active model/tool turn, terminates its child processes,
  preserves the session, and returns to the prompt for a correction. `Ctrl+C`
  is not used as the turn-cancellation key.

Rebuild the Windows binary after applying those changes:

```powershell
cd 'C:\claw cod\claw-code-parity — копия (2)\rust'
cargo build --release -p rusty-claude-cli
```

The end-to-end check on 2026-09-02 used only the original task prompt, a normal
resume, and Claw compaction. Kimi created a WSL-hosted dynamic SSH listener
exposed to Windows on `127.0.0.1:1080`, launched Chrome with
`--proxy-server=socks5://127.0.0.1:1080`, and opened `ipinfo.io`. An independent
SOCKS request returned a public IP different from the direct connection.

## Authentication lifecycle

Kimi access tokens are short-lived. The gateway decodes the JWT expiry and
refreshes before expiration through:

```text
https://auth.kimi.com/api/account.gateway.v1.AuthService/RefreshToken
```

The refresh token is long-lived but can expire or be revoked. When that happens,
rerun `windows\import-edge-session.ps1` against the dedicated or existing
logged-in Edge profile. Do not store an Edge profile, cookie database, token,
password, DPAPI plaintext, or SSH private key in Git.

Claw session transcripts can contain tool arguments and tool results, including
content read from credential files. Treat `.claw\sessions\*.jsonl` as sensitive
local state, keep it ignored by Git, and restrict affected files to the current
Windows account.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `KIMI_SESSION_FILE` | `/home/clawrun/.config/kimi-wrapper/session.json` | Private session JSON |
| `KIMI_PROXY` | empty (direct) | Optional egress proxy |
| `KIMI_WEB_BASE_URL` | `https://www.kimi.com` | Chat API origin |
| `KIMI_AUTH_BASE_URL` | `https://auth.kimi.com` | Token refresh origin |
| `KIMI_UPSTREAM_PROTOCOL` | `connect_v2` | Current framed Connect web transport |
| `KIMI_UPSTREAM_MODEL` | `k2d6-chat` | Stable Windows profile; optional K3 ID is `k3-agent` |
| `KIMI_SCENARIO` | `SCENARIO_CHAT` | Use `SCENARIO_OK_COMPUTER` with K3 |
| `KIMI_KIMIPLUS_ID` | empty | Use `ok-computer` with K3 |
| `KIMI_REASONING_EFFORT` | `REASONING_EFFORT_NONE` | K3 supports LOW/HIGH/MAX and defaults to HIGH |
| `KIMI_CONTEXT_LENGTH` | `CONTEXT_LENGTH_L` | Standard free context tier |
| `KIMI_ENABLE_PLUGIN` | `0` | Set to `1` with the current K3 web request contract |
| `KIMI_DEFAULT_SHELL_TIMEOUT_MS` | `30000` | Finite default for generated foreground shell calls that omit a timeout |
| `KIMI_BIND_HOST` | `127.0.0.1` | API bind address |
| `KIMI_PORT` | `18081` | API port |
| `KIMI_MAX_CONCURRENT_UPSTREAM` | `2` | Concurrent Kimi operations |
| `KIMI_REQUESTS_PER_MINUTE` | `10` | FIFO pacing rate; interval is `60 / value` seconds |
| `KIMI_FORMAT_REPAIR_ATTEMPTS` | `3` (Windows: `5`) | Retry malformed or unknown tool-call wire formats without choosing a tool for Kimi |
| `KIMI_PERSISTENT_CHAT` | `0` | Reuse one Kimi chat per unique Claw model/session marker |
| `KIMI_MAX_PERSISTENT_MESSAGES` | `1000` | Emergency count guard; context size is the primary rotation limit |
| `KIMI_MAX_PERSISTENT_CONTEXT_TOKENS` | `200000` | Safety threshold below Kimi's 262,144-token context window |
| `KIMI_GATEWAY_API_KEY` | empty | Optional bearer key for all `/v1/*` routes; Windows enables it automatically |
| `KIMI_UPSTREAM_MAX_ATTEMPTS` | `3` | Bounded transport/status attempts |
| `KIMI_UPSTREAM_RETRY_BASE_SECONDS` | `0.5` | Exponential backoff base |

Operational probes:

```text
GET /health   local session/config state
GET /ready    access refresh plus live kimi.com probe
GET /metrics  retries, empty completions, tool calls, request latency
```

## Tests

```bash
python -m unittest -v tests/test_kimi_api_server.py
python -m py_compile src/kimi_api_server.py
bash -n bin/run-claw-kimi scripts/vm121-claw-kimi.sh
systemd-analyze verify deploy/kimi-api.service deploy/kimi-egress-socks.service
```

The unit tests cover message conversion, structured text input, SSE filtering,
payload shape, JWT expiry parsing, and secure atomic session persistence.
