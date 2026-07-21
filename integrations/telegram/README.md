# Telegram projects for Claw Code

This integration adds persisted Claw projects to an existing Gemma Telegram
deployment without adding a separate OCR menu.

## User model

- **New project** creates a new workspace and a fresh Claw session.
- Messages continue the active project's saved session.
- **Stop** interrupts only the running operation; completed context remains.
- **Progress** (`/progress` or **📊 Ход работы**) reports the current model/tool
  phase, elapsed time, last phase change, matching child Agents, and whether the
  turn may be stalled. Natural questions such as «что ты сейчас делаешь?»,
  «на чём остановился?», «опиши эту задачу» and «не завис ли?» use a separate
  read-only Gemma observer. It receives a bounded summary of the durable plan, latest
  checkpoint, recent events, and last tool, then answers naturally without
  interrupting or steering the active turn. If both model slots are busy, the
  bot returns the technical progress immediately instead of queueing observer
  work behind the active task.
- An ordinary text message during a running turn is a live steering update: the
  current process is interrupted, its durable state is checkpointed, and the
  same project resumes with the correction. `/next TEXT` queues work without
  interrupting; `/queue`, `/pause`, and `/continue` expose durable control.
- **Close** stops the active project without deleting it.
- **Projects** lists saved projects; `/project ID` opens one again.
- A VM or bridge restart does not lose completed turns. The bridge restores the
  exact stored Claw session file on the next message.

Images and PDFs are attached to the active workspace. Images receive a Gemma
Vision preprocessing pass. When the request asks to read text, numbers, errors,
tables, or document fields, the bridge runs local Tesseract before invoking the
agent. PDFs use their text layer first and fall back to OCRmyPDF when needed.
Users simply forward the file into the same chat.

## Components

- `claw_project_bridge.py`: authenticated localhost HTTP service on the Claw VM;
- `telegram_claw_bot.py`: combined Gemma/Claw Telegram bot for the Telegram host;
- `systemd/`: bridge, model-tunnel, reverse-tunnel, and dedicated-sandbox
  templates;
- `tests/`: dependency-free unit tests.

The dedicated-VM profile defaults to `danger-full-access`, omits
`--allowedTools`, sets `CLAW_UNRESTRICTED=1`, and passes the complete service
environment to Claw. It keeps per-chat serialization, two physical top-level
model slots, process groups for `/stop`, atomic state writes, attachment
signature checks, and a separate project transcript.

The Telegram deployment is persistent but not a live terminal TTY. Every
Telegram message starts or resumes one non-interactive Claw turn. In the
dedicated VM, command approval is automatic because the whole disposable VM is
the sandbox; `/permissions` explains the active policy and `/stop` interrupts a
running process group. A future approval-button mode would require a separate
asynchronous stdin/permission protocol and is not mixed with the current
`danger-full-access` deployment.

Control commands use a separate Telegram worker pool, so `/progress`, `/status`,
`/queue`, `/pause`, `/continue`, and `/stop` remain responsive while a long turn
occupies the normal request pool. Live steering is process-boundary safe rather
than stdin injection: the correction is written atomically, the old process
group is interrupted, and a resumed process applies the correction.
Long model-prefill and generation phases refresh a 15-second operation
heartbeat, so the stalled indicator reflects a lost runtime heartbeat rather
than merely a long but healthy Gemma request.

Every operator turn owns a durable, versioned plan in `.claw/plan.json` and may
finish only when all items are complete and a verification item contains
concrete evidence. Verification markers are accepted in either the plan item or
its evidence. The completion gate retries premature final answers; forced Gemma
tool calls use llama-server's string `tool_choice` contract instead of an
unsupported named-tool object. If a completed verified turn persists its final
answer but ends with an empty terminal stream, the bridge returns that newly
persisted answer instead of replaying the entire task. Other failed processes
are retried with durable idempotency keys before declaring a concrete blocker
(four unchanged plan continuations or three failed process attempts). These are
explicit no-progress escalation rules, not task-duration timeouts.
`.claw/project-memory.json` stores corrections with
provenance/TTL, `.claw/events.jsonl` is an append-only audit journal, and
`.claw/checkpoints/` holds non-destructive Git/untracked recovery points.
`/continue` preserves this existing plan during service-restart recovery rather
than replacing it with a synthetic recovery plan. `/stop` also tombstones the
bridge's active operation when the Telegram process no longer has its local ID,
so an interrupted recovered turn cannot enter the retry loop.

The parent and child expose the complete built-in tool registry, including
`WebFetch`, `WebSearch`, `RemoteTrigger`, `MCP`, `ToolSearch`, shell, filesystem,
notebook, task, team, worker, and LSP tools. Public and private network access is
available, explicit HTTP URLs are preserved, and configured credentials are
inherited. Permission rules, blocking hooks, runtime sandbox layers, and the
dedicated unit's process restrictions are bypassed only when
`CLAW_UNRESTRICTED=1` is present.

The local Gemma deployment prompt is injected as a real OpenAI-compatible
`role=system` message for ordinary Telegram chat, Claw, Vision preprocessing,
and the built-in child Agent. It is file-backed and hot-reloaded on each model
request. The mechanism, historical patch lineage, rebuild steps, and deployment
checks are documented in [`SYSTEM_PROMPT.md`](../../SYSTEM_PROMPT.md).

The top-level Claw session may use the `Agent` tool to launch one background
sub-agent on the same Gemma deployment. The bridge injects `gemma4` as the
sub-agent default, gives every child role the complete built-in tool registry,
and keeps exactly one cross-process active-child lock. A child can see `Agent`,
but a recursive or second-process launch is rejected while that lock is
occupied. This matches the two available Gemma slots: parent plus one child.

## Claw patch

The CLI accepts a new non-interactive continuation form:

```bash
claw --output-format json --resume SESSION prompt "continue the task"
```

JSON output includes `session_id` and `session_path`. This makes completed
project context recoverable after a process or server restart. The active model
context is automatically compacted before an oversized request using
`CLAUDE_CODE_AUTO_COMPACT_INPUT_TOKENS`; set the bridge's
`CLAW_AUTO_COMPACT_INPUT_TOKENS` to roughly 65–70% of the verified Gemma context
per slot.

The measured Gemma deployment exposes two 163840-token slots and uses a 110000-
token threshold. A project can continue through repeated compactions, but one
model call can never exceed its physical slot. Before removing any message,
compaction writes an immutable full JSONL archive and an atomic versioned
checkpoint containing the plan, project memory, provenance, archive chain, and
resume summary. The latest checkpoint is automatically injected on every new
Claw process. Active-task and control state are also persisted; `/continue` can
recover an in-flight task after a bridge/process restart without guessing its
prompt.

The bridge uses a 32000-token completion ceiling, matching the current Claw Code
default Opus budget. It is not a target length: normal responses stop naturally.
The 110000 automatic-compaction point is unchanged. Telegram
responses are divided on paragraph/newline/word boundaries into ordered chunks
of at most 3900 characters, with no character loss, for both ordinary Gemma and
Claw. The ordinary Gemma chat remains separately configured at 4096 by default
and up to 8192 through `/tokens`.

The complete removal/retention rationale is recorded in
[`UNRESTRICTED-DEPLOYMENT.md`](UNRESTRICTED-DEPLOYMENT.md).

The Gemma server profile, capacity measurements, rejected larger contexts, and
ordinary Gemma Telegram bot are maintained in
[`FrankRappo/gemma4-amd-vulkan-ops`](https://github.com/FrankRappo/gemma4-amd-vulkan-ops).

## Install safely

Create writable paths before starting the hardened unit. The unit also marks
them optional in `ReadWritePaths`, preventing a first-install namespace failure
if provisioning and service startup race.

```bash
sudo install -d -m 0700 -o clawrun -g clawrun \
  /home/clawrun/analise_storage/state \
  /home/clawrun/analise_storage/telegram-projects \
  /home/clawrun/.claw
sudo install -m 0600 -o root -g root \
  claw-telegram-bridge.env /etc/claw-telegram-bridge.env
sudo systemctl daemon-reload
sudo systemctl enable --now claw-telegram-bridge.service
```

On a dedicated disposable VM only, install
`systemd/claw-telegram-bridge-sandbox.conf` as a systemd drop-in and set
`CLAW_PERMISSION_MODE=danger-full-access` and `CLAW_UNRESTRICTED=1`. Optional
operator credentials belong in `/etc/claw-agent-credentials.env`, mode `0600`,
and are inherited by Claw. Do not use this profile on a shared or untrusted
host. Install the model tunnel from
`systemd/gemma-api-tunnel.service` and its external environment file before the
bridge; install the reverse tunnel last, after local bridge health succeeds.

Validate the unit and service before exposing the reverse tunnel:

```bash
systemd-analyze verify integrations/telegram/systemd/claw-telegram-bridge.service
systemctl show claw-telegram-bridge.service -p ActiveState -p NRestarts
curl --fail --silent http://127.0.0.1:19090/health
```

## Test

```bash
cargo test -p rusty-claude-cli
python3 -m unittest discover -s integrations/telegram/tests -v
```

Production evidence for resume, restart recovery, stop, automatic OCR, two
parallel projects, dedicated-VM migration, package installation, firewall
isolation, and VM reboot recovery is recorded in
[`PRODUCTION-VALIDATION.md`](./PRODUCTION-VALIDATION.md).

The Claw runtime, bridge, sessions, and workspaces have been moved off the
shared VM and onto one dedicated sandbox VM. Per-project containers and
repository-scoped GitHub deploy keys remain documented future upgrades, not
current requirements. See
[`DEDICATED-SANDBOX-VM.md`](./DEDICATED-SANDBOX-VM.md).

## Tunnel key restriction

Use a dedicated unprivileged account on the Telegram host. Restrict its public
key to remote forwarding and the one listen address/port supported by your
OpenSSH version. Keep the bridge itself bound to `127.0.0.1` and require the
same random bearer token on both sides.
