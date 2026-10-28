# Gemma 4 31B — two AMD GPU + vision configuration (updated 2026-07-20)

## Result

Gemma 4 31B Abliterated Q4_K_M now runs as one llama.cpp endpoint using two AMD
GPUs on two physical hosts:

```text
clients / Telegram tunnel
        |
        v
srv1 127.0.0.1:8080, llama-server b10012
  - Vulkan0: srv1 AMD GPU
  - RPC0: srv2 AMD GPU over 10.30.0.2:50053
  - layer tensor split: 1,1
  - Gemma 4 mmproj Q8 for PNG/JPEG screenshots
```

The original outage was not a model/GPU crash. The old srv1 server had been
started manually with `nohup`; after the 2026-07-07 host reboot it had no enabled
primary systemd unit and therefore did not return.

## Pinned artifacts

Runtime:

```text
llama.cpp release: b10012
commit: c71854292f7c367cc3b35939f88121d81945472f
official Vulkan archive SHA-256:
4b1dc1507aa4bb3d2d8b1ca69ea67b7ef7b4050bba00d8cc19cee9ce1716ec6c
```

Models:

```text
target:
/mnt/ssd/llm-distributed/models/gemma-4-31b-abliterated-Q4_K_M.gguf
SHA-256: 380f2f50f441225e669ab1541220985387bf3933573cd2f4f7e667f3790e73a3

vision projector:
/mnt/ssd/llm-distributed/models/vision/mmproj-gemma-4-31B-it-Q8_0.gguf
SHA-256: 1e8de54a30a5d08fa400c8d956a5ef7f8ad5ba51a39b860d1ccb463d7c330c37
```

Old runtimes and the srv2 local model service were preserved for rollback.
Server and RPC worker must always use identical llama.cpp builds.

## Active services and files

`srv1`:

```text
/etc/systemd/system/llm-gemma4-primary.service
/etc/default/llm-gemma4-primary
/mnt/ssd/llm-distributed/run-gemma4-31b-2gpu-vision.sh
/mnt/ssd/llm-distributed/runtime/llama.cpp-b10012-vulkan-x64/llama-b10012/
```

`srv2`:

```text
/etc/systemd/system/llm-gpu2-rpc-worker.service
/opt/llm-rpc/run-gemma4-rpc-b10012.sh
/opt/llm-rpc/runtime/llama.cpp-b10012/llama-b10012/
```

Tracked source of truth:

```text
/work/claster/analise_storage/llm_ops/systemd/
```

Current service state:

```text
srv1 llm-gemma4-primary.service: active/enabled, NRestarts=0
srv2 llm-gpu2-rpc-worker.service: active/enabled, NRestarts=0
srv2 llm-gpu2-local-server.service: inactive/disabled (rollback service)
```

## Effective llama-server profile

```text
--model gemma-4-31b-abliterated-Q4_K_M.gguf
--mmproj mmproj-gemma-4-31B-it-Q8_0.gguf
--mmproj-offload
--rpc 10.30.0.2:50053
--device Vulkan0,RPC0
--split-mode layer
--tensor-split 1,1
--gpu-layers auto
--ctx-size 32768
--parallel 1
--batch-size 1280
--ubatch-size 1280
--image-min-tokens 280
--image-max-tokens 1120
--mtmd-batch-max-tokens 1280
--cache-type-k q4_0
--cache-type-v q4_0
--cache-ram 0
--fit off
--reasoning on
--reasoning-budget -1
--jinja
--host 127.0.0.1
--port 8080
```

The original `--ubatch-size 512` fixed the first 280-token image path after a
128 micro-batch assertion. On 2026-07-20 the profile was deliberately increased
to an adaptive `280–1120` image-token range for sharper OCR. All three batch
limits are `1280`, so the largest non-causal Gemma 4 image-token chunk fits. The
launcher rejects a configuration where any batch limit is below the maximum
image budget.

Host-memory prompt caching remains disabled with `--cache-ram 0`. The measured
additional working RAM for the 1120 OCR path was approximately 502 MiB; it is
image/batch workspace, not a prompt cache.

The service launcher waits for `10.30.0.2:50053` before starting the model. RPC
is bound only to the private point-to-point link because llama.cpp RPC is
unauthenticated and experimental.

## Deployment order

1. Download and SHA-256 verify the official b10012 Vulkan archive.
2. Extract independent pinned runtime directories on both hosts; do not replace
   the old build.
3. Install the same `ggml-rpc-server` build on srv2.
4. Back up the old srv2 RPC unit.
5. Stop/disable `llm-gpu2-local-server.service` and start/enable
   `llm-gpu2-rpc-worker.service` on `10.30.0.2:50053`.
6. Verify the RPC port and free GPU memory.
7. Install/enable `llm-gemma4-primary.service` on srv1.
8. Start first at context 8K, verify text and vision, then restart at context
   32K.
9. Enable the bot only after direct image API smoke passes.

## Verification evidence

Current checks:

```text
srv1 /health: {"status":"ok"}
srv1 runtime: version 10012 (c71854292)
srv1 GPU VRAM used: ~10.87 GB
srv2 GPU VRAM used: ~10.54 GB
both systemd units: active/enabled, NRestarts=0
```

Deterministic 10-case text benchmark at 32K:

```text
median decode: 11.5749 tok/s
mean decode:   11.5660 tok/s
historical old endpoint: approximately 5 tok/s
```

Direct PNG screenshot smoke:

```text
expected: SOKOL-4827 / 19 375 РУБ.
received: SOKOL-4827 / 19 375 РУБ.
vision decode: approximately 11.6 tok/s
```

High-resolution dense OCR A/B on 2026-07-20:

```text
280:      414 prompt tokens, 20.33 s, small date misread as 07-29
280-1120: 1247 prompt tokens, 30.54 s, small date correctly read as 07-20
RAM delta during OCR: +501.9 MiB
minimum MemAvailable at 1120: 35.77 GiB
swap growth / OOM / GPU error / restart: none
```

Research sources, fixture hash, exact measurements and the vision-only rollback
are recorded in
[`GEMMA4-31B-HIGH-RES-VISION-1120-20260720.md`](GEMMA4-31B-HIGH-RES-VISION-1120-20260720.md).

Post-deployment owner acceptance on 2026-07-15 also passed with a real inbound
Telegram screenshot, covering the Telegram `getFile` path in addition to the
generated direct/deployed-code smoke.

The Telegram deployed-code smoke through the jump tunnel is recorded in:

```text
/work/claster/analise_storage/TELEGRAM-GEMMA4-VISION-BOT-20260715.md
/work/claster/llm-gpu-storage-cluster-20260527/tg-gemma-bot-local/TELEGRAM-GEMMA4-VISION-BOT-20260715.md
```

## MTP status

MTP is intentionally disabled in production:

```text
ENABLE_MTP=0
```

Reasons:

1. the downloaded third-party draft GGUF used obsolete architecture metadata
   `gemma4_mtp`, while b10012 expects `gemma4_assistant`;
2. upstream still has a Gemma 4 MTP shared-KV/cross-backend limitation for a
   target split across Vulkan0 and RPC0;
3. MTP does not accelerate multimodal requests themselves.

This does not affect normal text, two-GPU layer split, or screenshot support.
Future MTP work and AMD Vulkan patch candidates are documented in:

```text
/work/claster/gemma431b_upgrade/README.md
/work/claster/analise_storage/GEMMA4-31B-AMD-VULKAN-UPGRADE-PLAN-20260715.md
```

## Operational checks

`srv1`:

```bash
systemctl is-active llm-gemma4-primary.service
systemctl is-enabled llm-gemma4-primary.service
curl -fsS http://127.0.0.1:8080/health
journalctl -u llm-gemma4-primary.service -n 80 --no-pager
```

`srv2`:

```bash
systemctl is-active llm-gpu2-rpc-worker.service
systemctl is-enabled llm-gpu2-rpc-worker.service
ss -ltn | grep '10.30.0.2:50053'
journalctl -u llm-gpu2-rpc-worker.service -n 80 --no-pager
```

Verify GPU allocation on both hosts through the appropriate DRM
`mem_info_vram_used` sysfs file. Do not assume that a healthy endpoint proves
both GPUs are allocated.

## Rollback

To roll back only high-resolution Vision while preserving the two-GPU service,
set `VISION_MIN_TOKENS=VISION_MAX_TOKENS=280` and all three vision batch limits
to `512` in `/etc/default/llm-gemma4-primary`, then restart only
`llm-gemma4-primary.service` and verify `/health` plus the effective command
line.

Full two-host rollback:

1. Stop/disable `llm-gemma4-primary.service` on srv1.
2. Stop/disable `llm-gpu2-rpc-worker.service` on srv2.
3. Start/enable the preserved `llm-gpu2-local-server.service` on srv2.
4. There is no persistent srv1 fallback unit. Start the existing 4K fallback
   launcher as a supervised transient systemd service:

   ```bash
   systemd-run --unit=llm-gemma4-single-fallback --collect \
     --property=WorkingDirectory=/mnt/ssd/llm-distributed \
     --property=Restart=on-failure \
     /mnt/ssd/llm-distributed/start-gemma31-single-gpu-rollback-latest.sh
   ```

   The newer 8K equivalent is
   `/mnt/ssd/llm-distributed/restart-llama-8080.sh`; use only one fallback
   launcher at a time. A transient unit does not provide boot persistence.
5. Recheck srv1 `127.0.0.1:8080`, srv2 `<srv2-lan>:8081`, and both GPU
   memory allocations.

Remove the transient fallback before returning to the combined service:

```bash
systemctl stop llm-gemma4-single-fallback.service || true
systemctl reset-failed llm-gemma4-single-fallback.service || true
```

Never start the srv2 local model and RPC worker together: they compete for the
same GPU and the units are configured to conflict.

## Remaining hardware risk

The direct srv1/srv2 RTL8125 link is 2.5 Gbit/s and currently stable, but srv2
records persistent PCIe AER Correctable events for that NIC. No GPU reset or
uncorrectable PCIe event was observed. Run a 6–8 hour inference burn-in and abort
on link-down, malformed RPC response, GPU reset/device-lost, OOM, or
uncorrectable errors before declaring long-term production stability.

## Future repository split

The current Git-safe Gemma serving code and documentation remain versioned in
the private `FrankRappo/analise_storage` repository. A dedicated GitHub
repository has **not** been created. The curated extraction scope, security
exclusions, source-of-truth transition, and acceptance gates are recorded in
[`GEMMA4-INFRA-SEPARATE-REPO-TODO-20260715.md`](GEMMA4-INFRA-SEPARATE-REPO-TODO-20260715.md).

Do not copy the whole `/work/claster` tree or any runtime credentials when that
future migration is performed.
