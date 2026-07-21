# Production validation — 2026-07-20 through 2026-07-21

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
- Each Claw Gemma call uses a 32000-token ceiling, matching Claw Code's current
  default Opus budget. Normal answers still stop naturally.
- Ordinary Gemma defaults to 4096 output tokens and supports up to 8192 per
  chat through `/tokens`.
- Both modes preserve every returned character by sending ordered Telegram
  chunks of at most 3900 characters.
- Claw commands are automatically approved only inside the disposable VM.
  `/permissions` reports this policy; `/stop` remains available at any time.
- A top-level Claw turn may launch one Gemma-backed background `Agent`. Every
  child role receives the complete built-in tool registry. A shared OS advisory
  `flock` permits only one child across all top-level Claw processes, releases
  automatically after a crashed owner, and prevents recursive fan-out.
- Automatic compaction begins at 110000 input tokens for the measured 163840-
  token Gemma slot.
- Compaction archives the complete pre-compaction JSONL immutably, atomically
  updates a versioned recovery checkpoint, and rehydrates plan/project memory.
- Autonomous plans cannot finalize with pending items or without concrete
  verification evidence. Live steering, durable `/next`, `/queue`, `/pause`,
  `/continue`, active-task recovery, retry events, and project checkpoints are
  part of the deployed contract and require post-deployment E2E below.
- Claw now runs on one dedicated, disposable, GPU-less sandbox VM. The Telegram
  bot and Gemma inference remain on their existing hosts.
- The VM has 8 vCPU, 16 GiB RAM, and a 250 GB SSD. It is configured to boot with
  the hypervisor; its model tunnel, bridge, and reverse forward are enabled in
  the guest.
- The VM has public and private egress, noninteractive package-install ability,
  the complete Claw tool registry, full service-environment inheritance, and no
  runtime permission/hook/sandbox blocker in unrestricted mode.

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
| Lost CLI stdout recovery | If a resumed turn is persisted but its final JSON stdout is lost, the bridge returns the newly persisted assistant text instead of reporting a false failure. It never replays an unchanged older answer. |
| Dedicated VM tool/install test | Claw fetched a public HTTPS endpoint, installed a harmless package through noninteractive `sudo`, executed it, and returned the expected marker. |
| Migration integrity | Both real Telegram project session files matched the source SHA-256 hashes byte for byte; the original active project/session metadata was unchanged. |
| VM reboot recovery | The model tunnel, bridge, and reverse forward automatically returned active with zero restarts; the Telegram host health check and bot recovered. |
| Post-reboot resume | A marker was recalled after reboot and after the final RAM resize using the same session ID. |
| Post-migration parallelism | Two projects completed simultaneously in 18.297 s wall time versus 36.400 s summed turn time, with distinct session IDs. |
| Post-migration automatic OCR | A PNG attachment passed through the reverse-forwarded bridge, local OCR extracted the test number, and the agent returned that exact number. |
| Network access | Public HTTPS, explicit HTTP, and routed private-network access are required in unrestricted mode. |
| Long Telegram output | Unit fixtures reconstructed a multi-chunk response character-for-character, verified every chunk stayed below 3900 characters, and kept the keyboard only on the final message. The same sender is used by Gemma and Claw. |
| Sub-agent policy | The `Agent` tool uses the deployment's `gemma4` default, exposes the complete built-in tool set to every child role, and retains one cross-process active-child lock. |
| Deployed long-output smoke | The installed bot reconstructed a five-message fixture without character loss; the largest chunk was exactly 3900 characters, only the first replied to the source message, and only the last carried the keyboard. |
| Deployed sub-agent E2E | A parent Claw turn launched one `Explore` Agent without specifying a model, waited for it, read its marker, and returned the expected parent marker in 80.907 s. The persisted child manifest reported `model=gemma4`, `status=completed`, and no error. |
| Durable-control API | The deployed `/v1/status` reported `autonomous_plan=true`, `durable_memory=true`, `live_steering=true`, and the 110000-token compaction threshold. `/next`, `/queue`, `/pause`, `/continue`, and `/stop` all returned HTTP 200 against an isolated production project. |
| Live steering and recovery | An active production E2E turn was interrupted twice through `/v1/steer`, including recovery from a model-selected unbounded `tail -f`. Both calls returned `accepted=true` and `interrupted=true`; the bridge resumed the same durable session and superseded the older control message. |
| Autonomous completion gate | The steered turn created `e2e-proof.txt` with the corrected exact value, read it back, and finished only after every durable plan item was `completed` with non-empty evidence. The persisted task ended `completed`, no queue item remained pending, and three rollback checkpoints existed. |
| Deployed Telegram controls | The installed bot loaded successfully with Pause and Continue buttons and the `/progress` and `/queue` command paths. The bot, bridge, model tunnel, and reverse forward remained active with zero service restarts after deployment. |

The OCR agent turn completed in 69.182 s on the deployed hardware. That test
validates behavior, not a per-request latency target. The unrestricted profile
has no bridge wall-clock cutoff; `/stop` remains the explicit cancellation path.
The 32000-token ceiling is only the default Claw completion budget, and ordinary
short answers stop naturally before it.

## Dedicated VM capacity result

The initially documented 32 GiB VM plan was rejected after measuring the
selected 62 GiB hypervisor: together with the existing 32 GiB shared VM it
would leave too little host headroom. A temporary 24 GiB validation profile
worked, but still allowed an unsafe simultaneous worst case. The final 16 GiB
limit leaves about 14 GiB, or 22%, outside the two VM limits. Immediately after
the final boot the guest had about 15 GiB available and 239 GB disk free. The
hypervisor SSD had 550 GB free. These figures are dated measurements, not a
substitute for ongoing monitoring.

The secondary GPU server was not selected: it has only a small CPU/RAM budget
and should remain focused on inference. The sandbox VM resides on the primary
server SSD; it has no GPU passthrough and therefore cannot consume model VRAM.

The old shared-VM bridge and reverse-forward units remain disabled for rollback
and are not in the live request path. The former special-case private-network
exception is obsolete because unrestricted production now permits routed
private egress generally.

## Operational follow-up: storage monitoring

Durable session archives, event journals, attachment history, and rollback
checkpoints are intentionally not deleted automatically. This preserves
lossless recovery, but disk consumption grows with long-running projects.

Add host monitoring and alerts for both free filesystem space and per-project
`.claw/` growth. Alert before the VM falls below 20% free space or 25 GiB,
whichever threshold is reached first. A future retention command may export and
verify old immutable generations before pruning them, but it must never remove
the active session, latest recovery checkpoint, current plan/project memory, or
the archive chain required by that checkpoint. Until such a verified retention
workflow exists, cleanup remains an explicit operator action rather than a
silent automatic policy.

## Incident: persisted turn with missing CLI stdout

On 2026-07-21 Telegram reported `Claw returned no JSON result` for one resumed
turn. The bot, bridge, model tunnel, and model endpoint remained healthy with
zero service restarts. Claw exited successfully and the exact session file
contained the new user turn, tool result, and a non-empty final assistant
message, proving that execution and persistence completed before the bridge
lost the CLI result. The low-level stdout-loss trigger was intermittent and did
not reproduce on an isolated resume probe.

The bridge now snapshots the exact session before each resumed turn. If CLI
stdout contains no result but that invocation added a new persisted assistant
record, the bridge returns that new record. A timestamp-only change or an
unchanged tail is rejected, so an older answer cannot be replayed as the result
of a new request. Regression tests cover both recovery and the no-replay guard;
the deployed live smoke test returned a marker and session metadata normally.

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
cargo test -p runtime
cargo test -p rusty-claude-cli
python3 -m unittest discover -s integrations/telegram/tests -v
systemd-analyze verify integrations/telegram/systemd/claw-telegram-bridge.service
systemd-analyze verify integrations/telegram/systemd/gemma-api-tunnel.service
```
