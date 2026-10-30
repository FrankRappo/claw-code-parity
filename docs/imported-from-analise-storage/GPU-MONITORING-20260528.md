# 2-GPU runtime monitoring — 2026-05-28

Monitoring was enabled for both GPUs used by the active 2-GPU Gemma server.

## Active monitor processes

### srv1 local GPU

```text
script: /mnt/ssd/llm-distributed/monitor-amdgpu-srv1.sh
pid file: /mnt/ssd/llm-distributed/logs/gpu-monitor-srv1.pid
log path file: /mnt/ssd/llm-distributed/logs/gpu-monitor-srv1.path
current log: /mnt/ssd/llm-distributed/logs/gpu-monitor-srv1-20260529-001127.tsv
interval: 5 seconds
```

### srv2 GPU inside VM100

The physical srv2 GPU is passed through to VM100, so monitoring must run inside VM100, not on the srv2 host.

```text
script: /usr/local/bin/monitor-amdgpu-vm100
pid file: /var/log/llm-gpu-monitor/gpu-monitor-vm100.pid
log path file: /var/log/llm-gpu-monitor/gpu-monitor-vm100.path
current log: /var/log/llm-gpu-monitor/gpu-monitor-vm100-20260528-211148.tsv
interval: 5 seconds
```

## Evidence from first observation window

### srv1

```text
samples: 16
busy_min: 0%
busy_avg: 74.5%
busy_max: 100%
busy_last: 100%
VRAM: 14.47 / 15.92 GiB
memory temp around: 76-78 C
power during compute: ~86-93 W, idle dips ~25-26 W
```

### srv2 / VM100

```text
samples: 16
busy_min: 28%
busy_avg: 42.6%
busy_max: 57%
busy_last: 46%
VRAM: 15.34 / 15.92 GiB
memory temp around: 66-68 C
power spikes observed up to ~103 W
```

## Conclusion

GPU2 / srv2 VM100 is participating in inference. It is not stuck idle: during the observation window it showed nonzero busy values on every sample and power spikes during compute.

Different busy percentages between srv1 and VM100 are expected because the server uses llama.cpp RPC with split execution; load appears in stages and includes synchronization/RPC effects.

## Live check commands

On srv1:

```bash
tail -f "$(cat /mnt/ssd/llm-distributed/logs/gpu-monitor-srv1.path)"
```

On VM100:

```bash
sudo tail -f "$(sudo cat /var/log/llm-gpu-monitor/gpu-monitor-vm100.path)"
```

Stop monitors if needed:

```bash
# srv1
pkill -f monitor-amdgpu-srv1.sh

# VM100
sudo pkill -f monitor-amdgpu-vm100
```

## Update — after 2.5G kernel NAT cutover — 2026-05-28

The active RPC path is now `<srv2-rpc-ip>:50052` via srv2 kernel NAT to VM100. During the verification benchmark both GPUs showed nonzero load:

```text
srv1:  busy ~40-42%, VRAM ~14.43/15.92 GiB
VM100: busy ~48-50%, VRAM ~15.34/15.92 GiB
```

Related runbook:

```text
/work/claster/llm-gpu-storage-cluster-20260527/two-gpu-25g-rpc-cutover-20260528.md
```
