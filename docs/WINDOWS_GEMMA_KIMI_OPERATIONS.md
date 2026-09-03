# Windows Gemma and Kimi Operations

This document records the Windows/WSL model-runtime changes validated on
2026-09-03. It covers Google AI Studio Gemma, the Kimi web gateway, explicit
Claw agent mode, Windows PowerShell, and Ubuntu WSL tool routing.

## Runtime layout

| Component | Location | Responsibility |
| --- | --- | --- |
| Claw source and Windows binary | `C:\claw cod\claw-code-parity — копия (2)` | Session loop, tools, permissions, slash commands, provider clients |
| Kimi gateway | `C:\claw cod\kimi-claw-gateway` | Authentication, pacing, persistent upstream chats, retry, Connect/OpenAI wire translation |
| General launcher | `C:\claw cod\kimi-windows-agent\windows\start-code.ps1` | Starts Google Gemma through `claw-code` |
| Kimi launcher | `claw-kimi.cmd` | Starts Claw against the local Kimi gateway |
| VNC/CV framework | `\\wsl.localhost\Ubuntu-24.04\work\vnc_work_v2-vm121-review` | Read-only inspection, screenshots, CV targeting, optional explicitly authorized GUI control |

## Gemma: diagnosis and changes

### Context capacity is not API throughput

`gemma-4-31b-it` exposes a large model context, but the tested Google AI
Studio free project returned an effective limit of **16,000 input tokens per
minute**. Free access is quota-limited, not unlimited.

Observed locally:

- a minimal direct PowerShell request returned HTTP 200;
- a clean Claw request used about 3,967-4,479 input tokens;
- several Claw/tool-loop requests inside one rolling minute can therefore hit
  429 even when the model context window is mostly empty;
- Google returned `google.rpc.RetryInfo` with approximately 30 seconds of
  required cooldown.

### Server-directed retry

`OpenAiCompatClient` now reads both:

- the standard HTTP `Retry-After` header;
- Google's JSON `error.details[].retryDelay` protobuf-duration field.

The client waits for the greater of its exponential backoff and the server's
cooldown. Previously it exhausted four attempts in the same rate-limit window.

### Earlier automatic compaction

The Windows Gemma launcher sets:

```powershell
$env:CLAUDE_CODE_AUTO_COMPACT_INPUT_TOKENS = "8000"
```

This does not raise Google's quota. It limits avoidable conversation growth so
each later request consumes less of the available throughput.

### Gemma verification

- direct Google request: HTTP 200;
- real `claw-code` smoke test: `GEMMA_OK`;
- API tests cover header and Google-body retry delays;
- Windows release binary rebuilt successfully.

## Kimi: architecture

### Gateway boundary

The gateway is a transport adapter, not a planning or decision layer. It owns:

- DPAPI-protected Kimi session credentials;
- access-token refresh;
- request pacing and upstream concurrency;
- Kimi Connect framing and OpenAI-compatible SSE responses;
- persistent chat bookkeeping and rotation;
- syntactic tool-call validation and name normalization;
- retry of empty streams and malformed tool-call wire formats.

The gateway does **not**:

- classify turns as action/discussion/clarification;
- decide whether another tool is needed;
- select a tool for Kimi;
- contain Upwork, Freelancer, VNC, or project-specific task policy.

The former semantic decision pass and deterministic gateway tool selection were
removed. For ordinary `tool_choice=auto` requests, Kimi alone decides whether
to answer or call a tool.

### Empty-stream recovery

Kimi web can return HTTP 200 with a valid Connect stream but no assistant text
or tool call. When that happens, the gateway rotates to a fresh upstream chat
and replays the complete prompt instead of retrying corrections forever in the
same broken chat.

### Protocol repair

The Windows gateway permits five bounded format-repair attempts. This only asks
Kimi to use an exact registered tool name after malformed output; it does not
choose a tool. Common wire aliases such as `list_dir` are normalized to the
registered `glob_search` function.

### K2.6 and K3 limitation

The free Windows route uses `k2d6-chat`. In `auto` mode it can prefer discussion
or print a command as text instead of calling a tool. The tested `k3-agent`
route is designed for agent behavior but returned a membership paywall for the
current account. Explicit Claw agent mode therefore provides deterministic
execution without putting semantic policy into the gateway.

## Explicit Claw agent mode

Agent mode is controlled inside the interactive Claw process:

```text
/agent status
/agent on
/agent off
```

- Default: **off**. Kimi freely chooses discussion or tools.
- `on`: for each new user turn, Claw requires one real tool result before a
  final text answer is accepted. After that result, the same turn returns to
  `auto`, allowing Kimi to finish normally.
- `off`: all requests use `tool_choice=auto`.

This state is local to the current interactive process. It is intentionally not
stored in the gateway and does not reduce the 49-tool registry.

Live verification:

```text
/agent on
Use PowerShell to run: Test-Path 'C:\claw cod'.
```

Kimi invoked the real `PowerShell` tool, received `True`, and returned
`AGENT_MODE_OK=True`. `/agent off` was then verified in the same REPL.

## Windows and WSL tools

The Kimi launcher sets:

```cmd
set "CLAW_BASH_BACKEND=wsl"
set "CLAW_WSL_DISTRO=Ubuntu-24.04"
```

Routing is explicit:

- `PowerShell` runs on Windows;
- `Bash` runs through `wsl.exe --distribution Ubuntu-24.04`;
- a Windows working directory such as `C:\claw cod` is mapped to
  `/mnt/c/claw cod` for Bash;
- Windows-native file tools continue to run inside Claw.

`read_file` also accepts directories and returns a sorted read-only directory
listing. This allows a model-selected `read_file` call against a WSL UNC
directory to produce useful evidence rather than `Access denied`.

PowerShell validation of the VNC framework path:

```powershell
$path = '\\wsl.localhost\Ubuntu-24.04\work\vnc_work_v2-vm121-review'
Test-Path -LiteralPath $path
Get-ChildItem -LiteralPath $path
```

The path returned `True` and exposed 113 entries.

## Kimi validation evidence

The controlled read-only framework task completed successfully:

- all 49 Claw tools reached Kimi;
- Kimi selected five `read_file` calls itself;
- it read the framework directory, `README.md`, `VM121-CLAW-START.md`,
  `rc.sh`, and `lib/vmatch.py`;
- no marketplace click, submission, or file mutation occurred;
- Claw auto-compacted the conversation and completed in about 94 seconds.

Test evidence:

- Kimi gateway: 87 unit/integration tests passed (one platform-specific skip);
- slash commands: 28 tests passed;
- targeted Claw agent-mode test passed;
- targeted directory-listing and server-directed-retry tests passed;
- Windows release build completed successfully.

## Operator workflow

Start a new Kimi session:

```powershell
cd "C:\claw cod"
& ".\claw-code-parity — копия (2)\claw-kimi.cmd"
```

Discuss without forced tools:

```text
/agent off
```

Request verified local execution:

```text
/agent on
```

Resume an existing session interactively:

```text
/resume session-1788445447090-4504-0
```

Command-line `--resume` without a prompt only restores/reports and exits. A
single non-interactive continuation can instead use:

```powershell
& ".\claw-code-parity — копия (2)\claw-kimi.cmd" `
  --resume session-1788445447090-4504-0 prompt "продолжай"
```

## Known limitations

- Kimi web can be slow; the Windows gateway intentionally permits one upstream
  operation at a time and paces starts at six-second intervals.
- A Kimi web chat may report `Chat session in progress`; the broken persistent
  mapping is cleared and the next request creates a fresh chat.
- `k2d6-chat` does not reliably call tools in `auto`; use `/agent on` only for
  turns where a real local action is required.
- `/agent` is process-local and returns to `off` on a new Claw process.
- Google Gemma free-tier throughput remains externally enforced; client changes
  can wait/recover but cannot remove provider quotas.

## Local rollback

The pre-retry Windows binary was preserved locally as:

```text
C:\claw cod\claw-code-parity — копия (2)\rust\target\release\claw.exe.pre-gemma-retry-20260903.bak
```

Do not commit API keys, Kimi session files, DPAPI blobs, cookies, browser
profiles, or marketplace credentials.
