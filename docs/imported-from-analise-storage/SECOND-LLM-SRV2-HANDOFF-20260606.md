# Second LLM / srv2 handoff — 2026-06-06

This document is the separate handoff for the **second LLM path** using `srv2` GPU and a new controller VM on `srv1`.

Do **not** paste secrets into this document. Passwords/keys remain in local private files referenced by path only.

## Goal

Provide a separate terminal-controllable Claw/LLM path for the second GPU (`srv2`) without disturbing the existing production/background LLM process on `srv1`.

Target user intent captured this session:

- keep `srv2` physically powered on;
- do not shut down `srv2`;
- make `srv2` host-GPU mode similar to `srv1` and avoid the old VFIO/D3cold mixed state;
- create a second VM on `srv1` for Claw/control;
- set the second VM resource limits from remaining host capacity;
- configure the second LLM target token/context limit around `260000` tokens where supported.

## High-level topology

```text
local/controller shell
  -> jump host <previous-bot-vps>
  -> srv1 <srv1-public>:234 / <srv1-lan>
     - current production/single-GPU LLM: localhost:8080 (do not disturb)
     - second LLM llama-server: <srv1-lan>:8081
     - VM121 claw-second-llm: <vm121-lan>
  -> srv2 <srv1-public>:235 / <srv2-lan> / 10.30.0.2
     - GPU2 host mode
     - llama.cpp RPC worker: 10.30.0.2:50053
```

The second LLM currently uses:

```text
VM121 Claw wrapper -> http://<srv1-lan>:8081/v1 -> srv1 llama-server -> RPC -> srv2 GPU2
```

The existing background analysis uses:

```text
VM120 -> http://127.0.0.1:18080/v1 -> SSH tunnel -> srv1 localhost:8080 -> srv1 GPU only
```

## Access references

Private credentials/keys are intentionally not copied here.

Relevant private paths on the controller/local shell:

```text
/work/claster/llm-gpu-storage-cluster-20260527/private/access.md
/work/claster/llm-gpu-storage-cluster-20260527/private/vm-120-ed25519
```

New VM121 SSH private key on `srv1`:

```text
/root/.ssh/vm-121-claw-second-llm-ed25519
```

VM121 SSH command from `srv1`:

```bash
ssh -i /root/.ssh/vm-121-claw-second-llm-ed25519 ubuntu@<vm121-lan>
```

Convenience SSH aliases installed in `/root/.ssh/config` on `srv1`:

```bash
ssh vm121-claw
ssh second-llm-claw
ssh claw2
```

VM121 SSH command from the controller via jump + srv1 ProxyCommand requires the srv1 password from the private access file; do not inline it in logs.

## VM121 — second Claw controller VM

Created on `srv1` via Proxmox/QEMU.

```text
VMID: 121
name: claw-second-llm
node: srv1
IP: <vm121-lan>/24
GW: 192.168.87.1
CPU: host, 4 cores
RAM: 16384 MB
Disk: 80G on ssd storage
OS image source: /var/lib/vz/template/iso/ubuntu24.img
ciuser: ubuntu
onboot: 0
status observed: running
cloud-init observed: done
```

Observed VM121 resource state after first boot:

```text
host: claw-second-llm
kernel: 6.8.0-106-generic
root disk: /dev/sda1 ext4, ~77G available
RAM: ~15Gi available
```

VM121 was intentionally sized from remaining `srv1` capacity, not maximum host capacity, because VM120 and the existing LLM remain active.

Storage clarification for `srv1` host OS vs SSD storage:

```text
srv1 host root filesystem: /dev/mapper/pve-root mounted on /, ~94G, Proxmox/system OS
VM/proxmox SSD storage:  /dev/sda mounted on /mnt/ssd, ~880G
VM121 disk file:         /mnt/ssd/images/121/vm-121-disk-0.qcow2
```

So VM121 is on the separate `/mnt/ssd` data/storage disk, not on the `srv1` host root/system filesystem. The SSD does contain VM images, templates, model/runtime files, and Claw/LLM working directories; it does not contain the `srv1` host OS root (`/`).

## Existing srv1 production LLM — do not disturb

Current existing backend on `srv1`:

```text
pid: 3461651
health: http://127.0.0.1:8080/health -> {"status":"ok"}
mode: srv1 GPU only, no srv2 RPC
args include:
  --device Vulkan0
  --ctx-size 8192
  --parallel 1
  --batch-size 512
  --ubatch-size 128
  --cache-type-k q8_0
  --cache-type-v q8_0
  --host 127.0.0.1
  --port 8080
```

This endpoint is used by the active VM120 background repair run through VM120 local port `18080`.

## srv2 GPU host-mode state

`srv2` was physically powered back on and must remain on.

Current desired binding:

```text
03:00.0 VGA/compute -> amdgpu
03:00.1 HDMI audio  -> snd_hda_intel
```

Current desired power/runtime guard:

```text
power/control=on
d3cold_allowed=0
```

applied on GPU/root/switch path including:

```text
0000:00:01.0
0000:01:00.0
0000:02:00.0
0000:03:00.0
0000:03:00.1
0000:00:1c.5
0000:05:00.0
```

Do not restore mixed `amdgpu + vfio-pci` mode for the same physical GPU while using host/RPC LLM.

### srv2 remediation already applied

Backups from host-mode remediation:

```text
/root/srv2-gpu-hostmode-fix-20260605T052605Z
/root/srv2-match-srv1-hostmode-20260606T112205Z
```

Disabled stale VFIO/blacklist configs:

```text
/etc/modprobe.d/blacklist-amd-gpu-host.conf.disabled-hostmode-20260605T052605Z
/etc/modprobe.d/vfio-pci-gpu.conf.disabled-hostmode-20260605T052605Z
/etc/modules-load.d/vfio.conf.disabled-hostmode-20260605T052605Z
```

Current amdgpu host-mode config:

```text
/etc/modprobe.d/amdgpu-host-llm-stability.conf
options amdgpu gpu_recovery=1
```

GRUB on `srv2` was changed to match `srv1` default host mode:

```text
GRUB_CMDLINE_LINUX_DEFAULT="quiet"
```

`update-grub` was run. Current boot still includes old `intel_iommu=on iommu=pt` until the next reboot; no reboot was done.

## srv2 RPC worker for second LLM

Service:

```text
/etc/systemd/system/llm-gpu2-rpc-worker.service
```

Definition:

```ini
[Unit]
Description=Second LLM GPU2 llama.cpp RPC worker on srv2
After=network-online.target llm-stability-setup.service
Wants=network-online.target

[Service]
Type=simple
Environment=VK_LOADER_DRIVERS_SELECT=radeon_icd.x86_64.json
ExecStart=/opt/llm-rpc/runtime/llamacpp-rpc-vulkan-runtime-portable/run-rpc-server.sh -H 10.30.0.2 -p 50053
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Observed state at creation:

```text
active: active/running
pid: 16487
listen: 10.30.0.2:50053
```

The service is currently **disabled** for autostart but was started manually for this session.

Useful commands on `srv2`:

```bash
systemctl status llm-gpu2-rpc-worker.service --no-pager -l
journalctl -u llm-gpu2-rpc-worker.service -n 100 --no-pager
ss -ltnp | grep ':50053'
pgrep -a rpc-server
```

Stop only the second RPC worker:

```bash
systemctl stop llm-gpu2-rpc-worker.service
```

## srv1 second llama-server endpoint

Service:

```text
/etc/systemd/system/llm-gpu2-server.service
```

Start script:

```text
/mnt/ssd/llm-distributed/start-gemma31-second-llm-gpu2.sh
```

Current intended args:

```bash
/mnt/ssd/llm-distributed/src/llama.cpp/build-rpc-vulkan-portable/bin/llama-server \
  --model /mnt/ssd/llm-distributed/models/gemma-4-31b-abliterated-Q4_K_M.gguf \
  --rpc 10.30.0.2:50053 \
  --device RPC0 \
  --gpu-layers auto \
  --ctx-size 260000 \
  --fit on \
  --parallel 1 \
  --batch-size 512 \
  --ubatch-size 128 \
  --cache-type-k q4_0 \
  --cache-type-v q4_0 \
  --jinja \
  --host <srv1-lan> \
  --port 8081
```

Observed at `2026-06-06T15:17:48+03:00`:

```text
service: active
pid: 4103003
listen: <srv1-lan>:8081
health: HTTP 503 while model is loading/warming
```

Important log evidence:

```text
RPC0: 10.30.0.2:50053 (15941 MiB free)
n_ctx_seq (260096) < n_ctx_train (262144)
warming up the model with an empty run
```

This means the requested ~260k context was accepted by startup parsing and fitted to the model capacity, but health may remain `503` until loading/warmup completes. If it later crashes or OOMs, reduce ctx in the start script and restart only `llm-gpu2-server.service`.

Useful commands on `srv1`:

```bash
systemctl status llm-gpu2-server.service --no-pager -l
journalctl -u llm-gpu2-server.service -n 200 --no-pager
curl -fsS http://<srv1-lan>:8081/health
ss -ltnp | grep ':8081'
pgrep -a llama-server
```

Stop only the second LLM server:

```bash
systemctl stop llm-gpu2-server.service
```

Do not stop pid `3461651` / port `8080`; that is the existing `srv1` production/background backend.

## VM121 Claw target configuration

Installed on VM121 at `2026-06-06T15:28+03:00` by copying the known-good Claw binary from VM120.

Installed files:

```text
/home/ubuntu/second-llm/bin/claw
/home/ubuntu/second-llm/bin/run-claw-gemma2
/home/ubuntu/second-llm/README.md
/home/ubuntu/second-llm/second-llm.env
/home/ubuntu/.local/bin/run-claw-gemma2 -> /home/ubuntu/second-llm/bin/run-claw-gemma2
/usr/local/bin/run-claw-gemma2 -> /home/ubuntu/second-llm/bin/run-claw-gemma2
```

Binary/version check:

```text
Claw Code
Version: 0.1.0
Build date: 2026-03-31
```

Claw target for VM121:

```text
GOOGLE_API_KEY=local-gemma
GOOGLE_BASE_URL=http://<srv1-lan>:8081/v1
model name: gemma4
max/target token or context limit: 260000
```

Wrapper on VM121:

```bash
#!/usr/bin/env bash
set -euo pipefail
BASE=/home/ubuntu/second-llm
export HOME=/home/ubuntu
export GOOGLE_API_KEY="${GOOGLE_API_KEY:-local-gemma}"
export GOOGLE_BASE_URL="${GOOGLE_BASE_URL:-http://<srv1-lan>:8081/v1}"
export CLAW_MAX_TOKENS="${CLAW_MAX_TOKENS:-260000}"
export CLAW_CONTEXT_TOKENS="${CLAW_CONTEXT_TOKENS:-260000}"
export CLAW_ALLOWED_TOOLS="${CLAW_ALLOWED_TOOLS:-Bash,Read,Write,Edit,MultiEdit,Grep,Glob,LS}"
cd "$BASE"
exec "$BASE/bin/claw" --model gemma4 --dangerously-skip-permissions "$@"
```

Known source copied from VM120:

```text
/home/clawrun/analise_storage/claw-code-parity/rust/target/release/claw
```

Operational note: a short Claw prompt was attempted through VM121 and reached the second llama-server, but it was intentionally bounded by a 120s timeout and did not finish because prompt processing at the 260k-context server is slow (`512` prompt tokens took about `101s`, progress `0.14`). No Claw process was left running after the timeout and the endpoint stayed healthy.

## Direct terminal workflow

From `srv1`, connect to the second Claw VM:

```bash
ssh vm121-claw
# equivalent explicit form:
ssh -i /root/.ssh/vm-121-claw-second-llm-ed25519 ubuntu@<vm121-lan>
```

Then use the installed VM121 wrapper, for example:

```bash
run-claw-gemma2 --help
run-claw-gemma2 --output-format json prompt 'Say READY and report the model endpoint health.'
```

Because the second endpoint is configured for the requested near-260k context, Claw prompt processing can be slow. For quick health checks prefer:

```bash
curl -fsS http://<srv1-lan>:8081/health
```

From controller/local shell, connect through jump -> srv1 -> VM121. Use passwords/keys from the private access files; do not paste secrets in shared logs.

## Monitoring checklist

### Second LLM endpoint

```bash
# srv1
curl -fsS http://<srv1-lan>:8081/health
journalctl -u llm-gpu2-server.service -n 200 --no-pager

# srv2
systemctl status llm-gpu2-rpc-worker.service --no-pager -l
journalctl -u llm-gpu2-rpc-worker.service -n 100 --no-pager
```

### srv2 GPU health

```bash
# srv2
for f in /sys/class/drm/card*/device/hwmon/hwmon*/temp*_input \
         /sys/class/drm/card*/device/hwmon/hwmon*/power*_average \
         /sys/class/drm/card*/device/gpu_busy_percent; do
  [ -f "$f" ] && echo "$f=$(cat "$f")"
done

journalctl -k -b --no-pager | grep -Ei 'device lost|ring .*timeout|SMU|D3cold|Unknown header|GPU reset' | tail -n 120
```

### Failure criteria for GPU2

Treat GPU2 as unstable if any of the following appear during load:

```text
ring timeout
device lost from bus
SMU response 0xFFFFFFFF
Failed to export SMU metrics table
GPU reset / wedged
D3cold/D3hot unable to D0
Unknown header type 7f
SSH/PVE degradation
```

Given previous failures happened after about 4 hours, a meaningful stability test must run longer than 4 hours; recommended burn-in is 6-8 hours minimum, ideally overnight.

## Current caveats

1. `srv2` GPU is alive now, but not proven stable for long LLM/RPC load. Previous failures happened after about 4 hours, so the meaningful proof is a 6-8h+ burn-in, not this short setup check.
2. The second LLM uses RPC over `srv2`; if GPU2 wedges, stop only `llm-gpu2-server.service` and `llm-gpu2-rpc-worker.service` and preserve logs.
3. `ctx-size 260000` is near the model training context (`262144`) and is slow/heavy. It was accepted by the server log as `n_ctx_seq=260096`; health is now `ok`, but Claw prompts can take minutes just to process the prompt at this context.
4. `llm-gpu2-server.service` and `llm-gpu2-rpc-worker.service` are disabled for autostart unless explicitly enabled later.
5. The existing `srv1:8080` process and VM120 background analysis must not be interrupted.
6. Direct external SSH to `srv2` via NAT `<srv1-public>:235` may time out during SSH banner exchange; the verified alternate route is controller -> jump -> `srv1`, then from `srv1` to LAN `root@<srv2-lan>`.

## Latest known status snapshot

```text
snapshot_time: 2026-06-06T15:36:58+03:00 srv/VM clocks; 2026-06-06 local session
VM121: running, IP <vm121-lan>, 4 CPU, 16GB RAM, 80GB SSD
VM121 Claw: installed; /usr/local/bin/run-claw-gemma2 available; claw 0.1.0
srv2 RPC worker: active/running, pid 16487, 10.30.0.2:50053, NRestarts=0
srv1 second LLM: active/running, pid 4103003, <srv1-lan>:8081, health {"status":"ok"}, NRestarts=0
srv1 existing LLM: active/healthy, pid 3461651, 127.0.0.1:8080, health {"status":"ok"}
srv2 GPU binding: 03:00.0 amdgpu; 03:00.1 snd_hda_intel; power/control=on; d3cold_allowed=0
srv2 GPU metrics: edge ~38C, junction ~41C, memory ~60C, power ~22W, busy 0% after smoke test
srv2 kernel fault scan: no current boot device-lost/ring-timeout/SMU-FFFFFFFF lines observed; only normal boot PME/SMU init lines
VM120 background srv1-only repair: running, processed_this_run=1123, remaining=392, failures_this_run=3, endpoint 127.0.0.1:18080 health ok
```

Claw smoke note:

```text
Command attempted from VM121 with a 120s guard:
run-claw-gemma2 --output-format text prompt "Ответь ровно одним словом: READY"

Result: timed out by the guard while the server was still processing the prompt.
Relevant server timing: 512 prompt tokens in ~101s, progress 0.14.
Post-check: no claw process remained; second endpoint health stayed ok.
```


## Prompt / SYSTEM_PROMPT transfer

The documented srv1/Gemma client prompt has been transferred to VM121 and is active through the VM121 workspace `CLAUDE.md`:

```text
/home/ubuntu/second-llm/CLAUDE.md
/home/ubuntu/second-llm/prompts/system-prompt-gemma4-abliterated-current.txt
/home/ubuntu/second-llm/prompts/client-runtime-defaults.env
```

Local source/copy:

```text
/work/claster/vm121/CLAUDE.md
/work/claster/vm121/prompts/system-prompt-gemma4-abliterated-current.txt
/work/claster/vm121/VM121-PROMPT-TRANSFER-20260606.md
```

Verified on VM121 with:

```bash
/home/ubuntu/second-llm/bin/claw system-prompt --cwd /home/ubuntu/second-llm
```

The generated system prompt includes the Gemma 4 31B Abliterated persona lines from the srv1/bot documentation. Reference VM120 storage/orchestrator prompt files were also copied under `prompts/`, but they remain reference-only until VM121 has matching read-only mounts/tools.


## Speed profiling / retune result — 2026-06-06

Speed logs:

```text
/work/claster/vm121/speed-tests/speed-profile-20260606T1510/run.log
/work/claster/vm121/speed-tests/speed-retune-20260606T1722/raw-retune.log
/work/claster/vm121/speed-tests/speed-retune-20260606T1722/summary.md
```

Finding: direct raw API to `srv1:8081` is already slow without Claw:

```text
baseline: ~2.46-2.76 prompt tok/s
retuned:  ~2.17-2.28 prompt tok/s
```

A reversible retune was tested on `llm-gpu2-server.service` without reducing context and without touching `srv1:8080`. It did not improve speed. Backup of the original second-LLM launcher on srv1:

```text
/mnt/ssd/llm-distributed/start-gemma31-second-llm-gpu2.sh.bak-speed-retune-20260606T172019+0300
```

Conclusion: this is not a VM121/Claw-only issue and not a 2.5G link issue. The practical bottleneck is the current `RPC0-only` architecture plus `srv2` memory pressure (`srv2` has ~15GiB RAM while rpc-server uses ~14.6GiB RSS and swap is active). Real fixes are either local hosting on srv2 after adding RAM/model/runtime, or using the old split `Vulkan0,RPC0 --tensor-split 1,1` profile if sharing srv1 GPU is acceptable.

