# Telegram projects for Claw Code

This integration adds persisted Claw projects to an existing Gemma Telegram
deployment without adding a separate OCR menu.

## User model

- **New project** creates a new workspace and a fresh Claw session.
- Messages continue the active project's saved session.
- **Stop** interrupts only the running operation; completed context remains.
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

The source configuration defaults to `workspace-write`. It also uses an
explicit tool allowlist, argument arrays instead of a shell, per-chat
serialization, a global concurrency limit, process groups for interruption,
atomic state writes, attachment signature checks, and a separate project
transcript. The production dedicated-VM deployment intentionally overrides the
permission mode to `danger-full-access` and permits `sudo`; the whole disposable
VM, rather than the process sandbox, is the security boundary.

The Telegram deployment is persistent but not a live terminal TTY. Every
Telegram message starts or resumes one non-interactive Claw turn. In the
dedicated VM, command approval is automatic because the whole disposable VM is
the sandbox; `/permissions` explains the active policy and `/stop` interrupts a
running process group. A future approval-button mode would require a separate
asynchronous stdin/permission protocol and is not mixed with the current
`danger-full-access` deployment.

The top-level Claw session may use the `Agent` tool to launch one background
sub-agent on the same Gemma deployment. The bridge injects `gemma4` as the
sub-agent default and limits active sub-agents to one per top-level process.
Sub-agents do not receive `Agent`, so recursion stops after one level. This
matches the two available Gemma slots: parent plus one child. Other Telegram
projects may still queue while both slots are occupied.

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
model call can never exceed its physical slot. Compaction summarizes older
completed turns, so exact verbatim details far back in the project should be
written to project files when they must be preserved losslessly. An interrupted
in-flight turn is not promised to persist; all previously completed turns do.

The bridge caps each individual model completion at 4096 tokens. This does not
cap the project session and does not prevent multi-step tool loops. Telegram
responses are divided on paragraph/newline/word boundaries into ordered chunks
of at most 3900 characters, with no character loss, for both ordinary Gemma and
Claw. The ordinary Gemma default is also 4096 output tokens; `/tokens` may raise
one chat to 8192. These are generation safety limits, not Telegram truncation:
all text actually returned by the model is sent.

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
`CLAW_PERMISSION_MODE=danger-full-access`. Do not use that override on a shared
or credential-bearing host. Install the model tunnel from
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
