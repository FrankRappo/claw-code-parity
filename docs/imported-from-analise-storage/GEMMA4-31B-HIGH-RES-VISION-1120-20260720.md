# Gemma 4 31B — high-resolution Vision 1120 (2026-07-20)

## Результат

На production endpoint Gemma 4 31B включён адаптивный high-detail Vision:

```text
--image-min-tokens 280
--image-max-tokens 1120
--batch-size 1280
--ubatch-size 1280
--mtmd-batch-max-tokens 1280
--cache-ram 0
```

Используется диапазон `280–1120`, а не принудительный `1120–1120`: процессор
может не расходовать максимальный budget на простом изображении, но получает до
1120 visual tokens для больших скриншотов, документов и мелкого текста.

Это настройка существующей Gemma 4 и существующего `mmproj`, а не новая модель,
не новые веса и не AMD/Vulkan-патч. Повторное скачивание или квантование GGUF не
требуется.

## Источники исследования

- публикация Google Gemma про sharp OCR и ручной `max_soft_tokens=1120`:
  <https://x.com/googlegemma/status/2077449157254738227>;
- официальная документация Google по Gemma Vision и budgets
  `70/140/280/560/1120`:
  <https://ai.google.dev/gemma/docs/capabilities/vision/image>;
- официальный model card:
  <https://huggingface.co/google/gemma-4-31B-it>;
- proposal Google сменить default `280 → 1120`:
  <https://huggingface.co/google/gemma-4-31B-it/discussions/131>;
- текущий официальный `config.json`:
  <https://huggingface.co/google/gemma-4-31B-it/blob/main/config.json>;
- параметры pinned `llama-server` `b10012`:
  <https://github.com/ggml-org/llama.cpp/blob/c71854292f7c367cc3b35939f88121d81945472f/tools/server/README.md>;
- известный crash-report для fixed 1120 на другой backend/hardware комбинации:
  <https://github.com/ggml-org/llama.cpp/issues/21550>.

На момент проверки Google proposal `#131` ещё не был слит, а основной config
сохранял default `280`. Поэтому high-resolution mode включён явно через
поддерживаемые pinned-сборкой llama.cpp аргументы.

## Почему batch limits равны 1280

Gemma 4 использует non-causal attention для image tokens. Полный image-token
chunk должен помещаться в logical batch, physical micro-batch и лимит
multimodal encoder. Предыдущие `512/512` не могут вместить budget 1120.

Выбран запас `1280`, поэтому launcher проверяет инварианты:

```text
VISION_BATCH_SIZE >= VISION_MAX_TOKENS
VISION_UBATCH_SIZE >= VISION_MAX_TOKENS
VISION_MTMD_BATCH_MAX_TOKENS >= VISION_MAX_TOKENS
```

Поддерживаемые budget values также валидируются до запуска:
`70`, `140`, `280`, `560`, `1120`.

## Реализация

Tracked source of truth:

```text
/work/claster/analise_storage/llm_ops/systemd/run-gemma4-31b-2gpu-vision.sh
/work/claster/analise_storage/llm_ops/systemd/llm-gemma4-primary.env.example
/work/claster/analise_storage/llm_ops/systemd/tests/test_run_gemma4_launcher.py
```

Production launcher:

```text
/mnt/ssd/llm-distributed/run-gemma4-31b-2gpu-vision.sh
SHA-256: b2a90a78bdf3ec7d846c747358776f3f2a2daf7b9e967694b7f8195e3a15dd1d
```

Установленные defaults:

```text
VISION_MIN_TOKENS=280
VISION_MAX_TOKENS=1120
VISION_BATCH_SIZE=1280
VISION_UBATCH_SIZE=1280
VISION_MTMD_BATCH_MAX_TOKENS=1280
```

`--cache-ram 0` оставлен отдельным неизменяемым production-решением. Новый
режим не включает host-memory prompt cache.

## Контролируемый A/B

Оба режима проверены на одной модели, одном `mmproj`, runtime `b10012`, двух GPU
через `Vulkan0,RPC0`, одном server slot и одном PNG. Запросы проходили через
deployed Telegram-bot payload и jump tunnel.

Fixture:

```text
format: PNG, 1600x1568, 16-bit RGBA
size: 243865 bytes
SHA-256: be5162250465de0fa46f9a4ba40da3cda08dc9ff03d08c24ea3ef81cf7902638
```

OCR-результат:

| Показатель | 280 | 280–1120 |
|---|---:|---:|
| Dense prompt tokens | 414 | 1247 |
| Dense total latency | 20.33 s | 30.54 s |
| Крупные поля `SOKOL-4827`, `19 375 РУБ.` | верно | верно |
| Мелкая дата | ошибочно `07-29` | верно `07-20` |
| Остальные ожидаемые поля | верно | верно |

Production-повтор после постоянного включения дал `1247` prompt tokens,
`30.78 s` total latency и снова правильно прочитал `07-20`.

Модель продолжает транслитерировать контрольную последовательность с похожими
кириллическими/латинскими символами (`А/А`, `Р/P`, `Х/X`). Это не считается
закрытым OCR-кейсом и должно остаться в расширенном corpus.

## RAM и стабильность

Одинаковая OCR-нагрузка измерялась с секундным sampling cgroup и
`/proc/meminfo`:

| Показатель | 280 | 280–1120 | Разница |
|---|---:|---:|---:|
| `llm-gemma4-primary` cgroup во время OCR | 375.6 MiB | 877.5 MiB | +501.9 MiB |
| Минимальный `MemAvailable` | 36.30 GiB | 35.77 GiB | -0.53 GiB |
| Пик unit при старте | ~1.25 GiB | ~1.25 GiB | практически без изменения |
| `SwapFree` | 6808136 KiB | 6808136 KiB | 0 |

У production host 62.68 GiB RAM. При принятом консервативном резерве
`MemAvailable >= 20 GiB` худший наблюдавшийся остаток `35.77 GiB` оставляет
`15.77 GiB` сверх резерва.

Дополнительные примерно 502 MiB — рабочие image/batch buffers, а не RAM cache.
Часть памяти может оставаться у живого процесса после первого изображения и
освобождается при перезапуске сервиса. Рост swap, OOM, GPU device-lost, abort и
автоматические рестарты в A/B не наблюдались.

## Production deployment evidence

Постоянный профиль включён 2026-07-20. После запуска и dense OCR:

```text
llm-gemma4-primary.service: active/running, NRestarts=0
srv1 /health: {"status":"ok"}
tg-gemma-bot.service: active/running
llm-srv1-forward.service: active/running
jump tunnel /health: {"status":"ok"}
RAM cache: disabled (--cache-ram 0)
```

Backup launcher непосредственно перед production switch:

```text
/mnt/ssd/llm-distributed/run-gemma4-31b-2gpu-vision.sh.bak-before-vision1120-prod-20260720T141040Z
```

## Operations

Safe checks on `srv1`:

```bash
systemctl is-active llm-gemma4-primary.service
curl -fsS http://127.0.0.1:8080/health
systemctl show llm-gemma4-primary.service \
  -p MainPID -p MemoryCurrent -p MemoryPeak -p NRestarts
journalctl -u llm-gemma4-primary.service -n 100 --no-pager
```

Проверять effective flags нужно по `/proc/$MainPID/cmdline`; обязательны
`image-min=280`, `image-max=1120`, все три batch limits `1280` и
`cache-ram=0`.

Для максимального OCR-качества пользователь должен отправлять screenshot как
PNG/JPEG **документ**. Telegram photo может быть пережат до попадания в модель.

## Rollback только Vision profile

Предпочтительный rollback без замены runtime или модели:

```text
VISION_MIN_TOKENS=280
VISION_MAX_TOKENS=280
VISION_BATCH_SIZE=512
VISION_UBATCH_SIZE=512
VISION_MTMD_BATCH_MAX_TOKENS=512
```

После изменения `/etc/default/llm-gemma4-primary` перезапустить только:

```bash
systemctl restart llm-gemma4-primary.service
curl -fsS http://127.0.0.1:8080/health
```

Аварийный файловый rollback — восстановить указанный backup launcher, проверить
его SHA-256, перезапустить unit и подтвердить effective command line.

## Оставшиеся ограничения

- A/B доказал эффект на одном детерминированном dense OCR fixture, но не заменяет
  широкий корпус чеков, таблиц, рукописного и low-contrast текста.
- Нужен длительный mixed text/image burn-in; текущая проверка доказывает короткую
  стабильность и отсутствие немедленного regression/crash.
- High-resolution Vision улучшает качество, но не ускоряет inference: на dense
  fixture latency выросла примерно на 50%.
- RAM, VRAM, OCR exact fields и Telegram latency нужно продолжать отслеживать
  после обновлений llama.cpp, Mesa, модели или `mmproj`.
- Maximum concurrency probe `512K total / parallel=2` создаёт два slots по 256K,
  но оставляет только `75.5 MiB` VRAM на srv1. Он отклонён и откатан до 32K/p1;
  использовать его с Vision 1120 небезопасно. Протокол:
  `GEMMA4-CLAW-CONTEXT-PARALLEL-PROBE-20260720.md`.
- Установленный Claw Cod на VM120 проверенно работает с текстом и tools. Внешний
  OCR уже установлен: Tesseract/OCRmyPDF и custom helpers доступны и проверены
  через Claw Bash tool. Но текущий OpenAI-compatible provider не сериализует
  `image_url`/image blocks, поэтому именно direct Vision 1120 с передачей pixels
  требует отдельного provider patch. Протокол:
  `CLAW-COD-GEMMA4-COMPATIBILITY-20260720.md`.
