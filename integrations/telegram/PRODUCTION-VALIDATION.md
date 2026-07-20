# Production validation — 2026-07-20

This record is intentionally sanitized: it contains no tokens, usernames,
public addresses, private paths, model weights, or Telegram chat identifiers.

## Deployed contract

- The bridge binds to localhost and requires a random bearer token.
- Telegram reaches it through a restricted reverse SSH forward.
- At most two Claw turns execute concurrently; messages in one chat remain
  serialized.
- A Telegram project owns one workspace and one exact Claw session file.
- `New project` creates a new session; `Close` detaches without deleting it;
  `Projects` can reopen it; `Stop` interrupts only the current process group.
- Images and PDFs stay in the active workspace. OCR is automatic when the
  request needs exact text; there is no separate OCR mode.
- Each Gemma call is capped at 1024 output tokens. The logical project is not
  capped at 1024: tool loops and later turns continue normally.
- Automatic compaction begins at 110000 input tokens for the measured 163840-
  token Gemma slot.

## End-to-end results

| Scenario | Evidence |
|---|---|
| Direct CLI resume | A marker was recalled with the same session ID after a separate `--resume ... prompt ...` process. |
| Bridge restart recovery | A completed marker was recalled after restarting the bridge; session ID and exact session file were unchanged. |
| Stop | A running agent process group was interrupted; the bridge returned to `running=false`, while completed history remained. |
| Automatic screenshot OCR | A generated image containing `OCR TOOL OK 4827` was forwarded without selecting an OCR menu; the agent returned `4827`. |
| Parallel projects | Two first turns completed concurrently in 24.113 s with different session IDs and different session files. |
| Parallel resume | Both projects recalled their own distinct markers concurrently in 16.658 s; session IDs and files remained unchanged. |
| Service health | Bridge active, model tunnel active, bridge restart count 0 after final code deployment. |

The OCR agent turn completed in 69.182 s on the deployed hardware. That test
validates behavior, not a per-request latency target; the 1024-token completion
cap and turn timeout bound pathological generation.

## Context behavior

The model server has two 163840-token slots. Claw compacts before a request
would exceed 110000 input tokens, leaving 53840 tokens of headroom for system
instructions, tool results, Vision tokens, continued turns, and output.

This yields long-lived logical projects across restarts and repeated
compactions, not an infinite lossless transcript inside one model call. Older
history is summarized. Durable facts that must remain exact should be written
to files in the project workspace. Completed session data survives process and
VM restarts; an interrupted in-flight turn may need to be submitted again.

See the matching server measurements in
[`FrankRappo/gemma4-amd-vulkan-ops`](https://github.com/FrankRappo/gemma4-amd-vulkan-ops/blob/main/docs/context-parallel-capacity.md).

## Regression commands

```bash
cargo test -p claw-runtime
cargo test -p rusty-claude-cli
python3 -m unittest discover -s integrations/telegram/tests -v
systemd-analyze verify integrations/telegram/systemd/claw-telegram-bridge.service
```
