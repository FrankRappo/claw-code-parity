# Gemma 4 / Claw Cod: context + parallel capacity probe

**Дата:** 2026-07-20
**Статус:** maximum capacity проверена; production rollback выполнен
**Текущий production:** `ctx-size=32768`, `parallel=1`, health OK

## Цель

Увеличить лимит токенов одной Claw-сессии и одновременно добавить параллельную
обработку запросов, не отключая:

- Gemma 4 31B Abliterated Q4_K_M;
- двух-GPU `Vulkan0 + RPC0`;
- Vision `280–1120`;
- Q4 KV cache;
- решение владельца `--cache-ram 0`.

Критерий production допуска: короткий single-request decode не хуже baseline
более чем на 5%, измеримый aggregate throughput двух запросов, отсутствие
OOM/GPU reset/restart и достаточный VRAM reserve для Vision 1120.

## Нативный предел модели

Официальный model card Gemma 4 указывает для dense 31B контекст `256K`:

<https://ai.google.dev/gemma/docs/core/model_card_4>

Официальный config задаёт text `max_position_embeddings=262144`:

<https://huggingface.co/google/gemma-4-31B-it/blob/main/config.json>

В текущем llama-server общий `--ctx-size` является общей ёмкостью KV cache и
делится между `--parallel` slots. Поэтому два гарантированных слота по 256K
требуют:

```text
CTX_SIZE=524288
PARALLEL=2
per-slot context=262144
```

Официальная документация server:

<https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md>

## Baseline: 32K / один slot

```text
CTX_SIZE=32768
PARALLEL=1
per-slot context=32768
```

Контрольный deterministic completion benchmark:

| Метрика | Результат |
|---|---:|
| Single decode | 11.736 ток/с |
| Два одновременно отправленных запроса | фактически выполнены последовательно |
| Dual wall time, 2 × 128 tokens | 22.586 s |
| Dual aggregate | 11.334 ток/с |
| `truncated` | false |

Вывод: при `parallel=1` второй Claw/Telegram запрос ждёт первый; aggregate
throughput практически равен single throughput.

Сырой лог:

```text
logs/context-parallel-ab-20260720/baseline-32k-p1.txt
```

## Maximum probe: 512K total / два slots

Перед изменением создан backup:

```text
/etc/default/llm-gemma4-primary.bak-before-ctx512k-p2-20260720T190903Z
```

Проверенный candidate:

```text
CTX_SIZE=524288
PARALLEL=2
VISION_MAX_TOKENS=1120
batch-size=1280
ubatch-size=1280
mtmd-batch-max-tokens=1280
cache-ram=0
```

Результат запуска:

```text
health:                  OK
ready after:             57 s
total slots:             2
context per slot:        262144
llama.cpp:               b10012-c71854292
NRestarts:               0
srv1 VRAM total:         17095983104 bytes
srv1 VRAM used:          17016803328 bytes
srv1 VRAM free:          79179776 bytes = 75.5 MiB
```

Это доказывает, что два нативных 256K slots технически создаются. Но оставшиеся
`75.5 MiB` на основной GPU — неприемлемый production reserve: нет места для
вариаций compute/vision buffers, и dense Vision 1120 не был бы безопасен. Поэтому
performance и dual-load benchmark на этом candidate намеренно не запускались.

### Почему 75.5 MiB недостаточно

`75.5 MiB` — около `0.46%` от 16 GiB VRAM. Startup доказывает только, что в
память поместились model shards, максимальный Q4 KV cache и стартовые buffers.
Во время реального запроса дополнительно или с другим peak нужны:

- Vulkan compute, graph, command и staging buffers;
- временные tensors для prompt batch/ubatch `1280`;
- offloaded image encoder/mmproj и intermediate tensors Vision 1120;
- driver bookkeeping и выравнивание allocations;
- резерв против fragmentation и небольших изменений после обновления runtime.

Обычная системная RAM и swap не являются заменой VRAM reserve. Backend может
попытаться использовать более медленный host-visible/GTT path, но рассчитывать
на прозрачный и быстрый spill нельзя; чаще критическая allocation завершается
ошибкой.

Возможные последствия почти нулевого запаса:

1. отдельный text/vision запрос получает out-of-device-memory/HTTP error;
2. `llama-server` abort/crash, после чего systemd перезапускает модель и endpoint
   недоступен примерно на минуту;
3. `VK_ERROR_DEVICE_LOST` или reset GPU, потеря локального context/KV;
4. разрыв RPC chain и необходимость перезапуска server, а иногда RPC worker;
5. резкий рост latency/jitter при memory pressure или fallback transfers;
6. нестабильность, зависящая от размера изображения и длины запроса: health после
   startup остаётся зелёным, но первый тяжёлый запрос падает.

Универсального безопасного числа для всех workloads нет. Для этой production
конфигурации кандидат допускается к Vision/load A/B только с измеряемым запасом
не менее примерно `1 GiB`, предпочтительно `1.5 GiB+`, на каждой GPU после
startup; окончательный порог подтверждается peak telemetry на Vision 1120 и двух
одновременных запросах. Поэтому 192K на slot остаётся верхним следующим
кандидатом, а не заранее одобренным production значением.

Сырой лог:

```text
logs/context-parallel-ab-20260720/start-512k-p2.txt
```

## Rollback

Maximum candidate отклонён и backup восстановлен. Проверено после возврата:

```text
CTX_SIZE=32768
PARALLEL=1
total slots=1
context per slot=32768
health={"status":"ok"}
NRestarts=0
Claw experiments: stopped
```

Сырой лог:

```text
logs/context-parallel-ab-20260720/rollback-after-max-probe.txt
```

## Что проверять следующим

Эксперименты поставлены на паузу по команде владельца. Следующий безопасный A/B
должен идти сверху вниз, не смешивая настройки:

| Candidate | Общий ctx | Slots | Лимит одного slot | Статус |
|---|---:|---:|---:|---|
| A | 393216 | 2 | 196608 (192K) | первый кандидат после возобновления |
| B | 327680 | 2 | 163840 (160K) | fallback |
| C | 262144 | 2 | 131072 (128K) | консервативный fallback |
| D | 196608 | 2 | 98304 (96K) | только если 128K не сохраняет reserve |

Для каждого candidate обязательны:

1. startup health, effective command line, `total_slots` и context per slot;
2. VRAM обеих GPU до запроса, после short text и после Vision 1120;
3. single 128/256-token decode против baseline;
4. два concurrent запроса и aggregate TPS;
5. prompt больше 32K для доказательства увеличенного лимита;
6. Claw tool-chain и OCR smoke;
7. Telegram image smoke;
8. `NRestarts=0`, отсутствие OOM/device-lost;
9. rollback при VRAM reserve ниже принятого порога или performance regression
   более 5%.

## Решение

- 32K действительно мало для долгой Claw-сессии.
- Два slots по 256K технически стартуют, но небезопасны на текущем VRAM budget.
- Production пока оставлен на проверенном `32K/p1`, а не на рискованном maximum.
- Наиболее вероятная рабочая цель — два slots по `128K–192K`; точное максимальное
  значение определяется только продолжением A/B с Vision и concurrent load.
- RAM prompt cache остаётся выключенным; увеличение достигается GPU KV cache, а
  не `--cache-ram`.
