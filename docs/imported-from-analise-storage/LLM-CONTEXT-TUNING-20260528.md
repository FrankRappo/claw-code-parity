# LLM context tuning — Gemma 4 Q4_K_M on srv1

Date: 2026-05-28

## Summary

The storage-search orchestrator was stopped before tuning. No storage search is currently running.

We increased llama.cpp runtime context step by step while keeping:

```text
model/quantization: gemma-4-31b-abliterated-Q4_K_M.gguf (unchanged)
device: Vulkan0
parallel: 1
batch-size: 512
ubatch-size: 128
KV cache: q8_0/q8_0
bind: 127.0.0.1:8080
```

## Results

| Requested ctx | Health | Slot effective n_ctx | Test result |
| --- | --- | --- | --- |
| 8192 | OK | 8192 | raw API ~7021-token prompt OK; Claw tool smoke OK |
| 16384 | OK | 16384 | raw API ~12021-token prompt OK |
| 32768 | OK | 32768 | raw API ~20021-token prompt OK, ~302 sec |
| 65536 | OK | 65536 | started OK; long ~30k prompt progressed past ~25k tokens before SSH client disconnect; server stayed healthy |
| 131072 | OK | 131072 | small API smoke OK |
| 262144 | OK | 262144 | small API smoke OK; this matches model train context from llama.cpp logs |
| 300000 | OK | **262144** | command starts with `--ctx-size 300000`, but `/slots` reports effective `n_ctx=262144` |

## Current running state

Current process after sweep:

```text
llama-server ... --ctx-size 300000 --parallel 1 ... --host 127.0.0.1 --port 8080
```

Important: although command line says `300000`, the real slot reports:

```text
n_ctx=262144
```

So the effective maximum exposed by llama.cpp/model is `262144`, not 300000.

## Practical conclusion

- User-visible target “~300000 tokens” was tested.
- The runtime starts with `--ctx-size 300000`, but caps/normalizes the active slot to `262144`.
- `262144` is also the model train context shown earlier in llama.cpp warnings (`n_ctx_train = 262144`).
- For real storage agents, keep `--parallel 1`; otherwise context/slot memory multiplies.

## Stability notes

- The server remained healthy at every tested startup level.
- Full 262144-token load was **not** performed; it would likely take a very long time on one 16GB GPU and is not necessary for the first storage-document search wave.
- Long-context prompts are slow: 20k prompt on `ctx=32768` took about 302 seconds.
- For the orchestrator, larger context solves the previous `4096` request limit, but short task prompts and manifests are still recommended.

## Verified Claw high-context smoke

From VM120 through the Gemma tunnel:

```text
bin/run-claw-gemma --output-format json prompt "Use your bash tool to run: bin/check-storage-safety. Then answer DONE."
```

Result:

```text
tool call OK
storage safety OK
short text prompt DONE
```

Log:

```text
/work/claster/analise_storage/logs/vm120-claw-highctx-smoke-20260528-183845.log
```

## Related logs

```text
/work/claster/analise_storage/logs/srv1-llama-ctx8192-test-20260528-181351.log
/work/claster/analise_storage/logs/srv1-llama-ctx16384-test-20260528-181843.log
/work/claster/analise_storage/logs/srv1-llama-ctx32768-test-20260528-182222.log
/work/claster/analise_storage/logs/srv1-ctx65536-post-disconnect-check-20260528-183522.log
/work/claster/analise_storage/logs/srv1-llama-highctx-sweep-20260528-183707.log
/work/claster/analise_storage/logs/vm120-claw-highctx-smoke-20260528-183845.log
```

---

## Current explicit production profile after user confirmation — 2026-05-28 18:50 local

Per user request, the server was changed from requested `--ctx-size 300000` to the explicit maximum effective context profile:

```text
--ctx-size 262144
--parallel 1
--batch-size 512
--ubatch-size 128
--cache-type-k q8_0
--cache-type-v q8_0
--jinja
--host 127.0.0.1
--port 8080
```

Verified state:

```text
health: OK
process: llama-server ... --ctx-size 262144 --parallel 1 ...
slot 0: n_ctx=262144, is_processing=False
VRAM free at startup check: ~1127 MiB
```

Log proving the switch:

```text
/work/claster/analise_storage/logs/srv1-set-ctx262144-and-launch-prep-20260528-184944.log
```


## Previous runtime override — 2-GPU conservative after PSU, 2026-05-28 22:05 MSK

At that checkpoint, the serving process was switched to conservative 2-GPU mode rather than the earlier single-GPU high-context process:

```text
mode: srv1 Vulkan0 + srv2 VM100 RPC0
endpoint: srv1 127.0.0.1:8080
ctx-size: 4096
parallel: 1
quantization: unchanged Q4_K_M
health: OK
```

Reason: first post-PSU run deliberately uses conservative `ctx-size 4096` to validate that `srv2` no longer hard-reboots under real Gemma warmup. This conservative state is now superseded by the current 2-GPU max-context profile below.

Evidence:

```text
/work/claster/llm-gpu-storage-cluster-20260527/two-gpu-after-psu-success-20260528.md
/work/claster/analise_storage/logs/two-gpu-after-psu-final-verify-20260528-200410.log
```

---

## Current production profile — 2-GPU max context, parallel=2 — 2026-05-28 22:29 MSK

The earlier 2-GPU conservative override (`ctx-size=4096`) has now been upgraded after validation. Current active production state:

```text
mode: srv1 Vulkan0 + srv2 VM100 RPC0
endpoint: srv1 127.0.0.1:8080
jump/bot endpoint: 127.0.0.1:18080 -> srv1:8080
model/quantization: gemma-4-31b-abliterated-Q4_K_M.gguf / Q4_K_M unchanged
pid: 2279971
ctx-size: 262144 total
parallel: 2
slot_count: 2
slot n_ctx: 131072 each
health: OK
smoke: max context ok
```

Operational meaning:

- The bot can handle two simultaneous model requests.
- Each of those two slots has `n_ctx=131072`.
- The total configured context is the model/llama.cpp maximum we validated: `262144`.
- For a single long maintenance/orchestrator request requiring the full `262144`, restart in a controlled window with `PARALLEL=1`.

Validated production wrapper on `srv1`:

```text
/mnt/ssd/llm-distributed/start-gemma31-2gpu-vm100-maxctx-parallel2.sh
```

Evidence:

```text
/work/claster/llm-gpu-storage-cluster-20260527/two-gpu-maxctx-parallel2-20260528.md
/work/claster/analise_storage/logs/two-gpu-maxctx-parallel2-restart-20260528-202500.log
```
