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
  stored Claw session ID on the next message.

Images and PDFs are attached to the active workspace. Images receive a Gemma
Vision preprocessing pass. When the request asks to read text, numbers, errors,
tables, or document fields, the bridge runs local Tesseract before invoking the
agent. PDFs use their text layer first and fall back to OCRmyPDF when needed.
Users simply forward the file into the same chat.

## Components

- `claw_project_bridge.py`: authenticated localhost HTTP service on the Claw VM;
- `telegram_claw_bot.py`: combined Gemma/Claw Telegram bot for the Telegram host;
- `systemd/`: service and reverse-tunnel templates;
- `tests/`: dependency-free unit tests.

The bridge uses `workspace-write`, an explicit tool allowlist, argument arrays
instead of a shell, per-chat serialization, a global concurrency limit, process
groups for interruption, atomic state writes, attachment signature checks, and
a separate project transcript.

## Claw patch

The CLI accepts a new non-interactive continuation form:

```bash
claw --output-format json --resume SESSION prompt "continue the task"
```

JSON output includes `session_id` and `session_path`. This makes completed
project context recoverable after a process or server restart. The active model
context is automatically compacted using
`CLAUDE_CODE_AUTO_COMPACT_INPUT_TOKENS`; set the bridge's
`CLAW_AUTO_COMPACT_INPUT_TOKENS` to roughly 65–70% of the verified Gemma context
per slot.

The bridge example caps each individual model completion at 1024 tokens. This
does not cap the project session and does not prevent multi-step tool loops; it
prevents a simple Telegram turn from spending minutes draining an unused 4096-
token completion. Raise it explicitly for workloads that need longer single
responses.

## Test

```bash
cargo test -p rusty-claude-cli
python3 -m unittest discover -s integrations/telegram/tests -v
```

## Tunnel key restriction

Use a dedicated unprivileged account on the Telegram host. Restrict its public
key to remote forwarding and the one listen address/port supported by your
OpenSSH version. Keep the bridge itself bound to `127.0.0.1` and require the
same random bearer token on both sides.
