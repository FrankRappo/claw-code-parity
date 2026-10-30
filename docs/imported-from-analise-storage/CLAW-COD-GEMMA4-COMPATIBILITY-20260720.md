# Claw Cod + Gemma 4 31B: проверка совместимости

**Дата проверки:** 2026-07-20  
**Статус:** PASS для текстового coding-agent и последовательных tool calls  
**Ограничение:** прямой image transport отсутствует; внешний OCR через Bash работает

## Краткий ответ

Установленный на VM120 кастомный Claw Cod работает с текущей production Gemma 4
31B через OpenAI-compatible API. Проверены не только обычный ответ, но и реальные
инструменты: `bash`, `write_file`, `read_file`, возврат tool result в модель и
следующий agent turn.

Это означает, что Claw можно использовать для текстового анализа репозиториев,
редактирования файлов и команд внутри VM120. Кроме того, на VM120 уже установлен
внешний OCR-контур: Tesseract/OCRmyPDF и custom helpers доступны агенту через
`bash`. High-resolution Vision 1120 на сервере не делает Claw прямым visual
client автоматически: в текущем Rust provider нет сериализации
`image_url`/image content blocks. Поэтому существуют два разных пути:

- установленный OCR извлекает текст локально и возвращает его модели как tool
  result — этот путь проверен и работает;
- direct Vision передал бы исходные pixels в Gemma image encoder — этот путь в
  Claw provider пока отсутствует.

## Текущая топология

```text
Claw Cod (VM120, user clawrun)
  -> GOOGLE_BASE_URL=http://127.0.0.1:18080/v1
  -> gemma-api-tunnel.service
  -> srv1 127.0.0.1:8080
  -> llama-server b10012: Vulkan0 + srv2 RPC0
  -> Gemma 4 31B Abliterated Q4_K_M
```

Live profile backend:

```text
ctx-size=32768
parallel=1
batch-size=1280
ubatch-size=1280
image-min-tokens=280
image-max-tokens=1120
mtmd-batch-max-tokens=1280
cache-ram=0
```

На момент финальной проверки:

```text
VM120 gemma-api-tunnel.service: active
VM120 storage-readonly-sshfs.service: active
VM120 API /health: {"status":"ok"}
srv1 llm-gemma4-primary.service: active, NRestarts=0
jump tg-gemma-bot.service: active
jump llm-srv1-forward.service: active
Claw processes after smoke: none
```

`tg-gemma-bot` и внешний forward работают на jump host; их `inactive` на `srv1`
нормален, потому что это не место установки этих units.

## Установленный Claw

```text
VM:                  VM120 / llm-vpn-ubuntu24 / <vm120-lan>
OS user:             clawrun (без sudo)
repository:          /home/clawrun/analise_storage/claw-code-parity
source commit:       5fbee8e82b9714882698e0be65f1ff31b19e4643
binary:              rust/target/release/claw
version:             Claw Code 0.1.0, build date 2026-03-31
binary SHA-256:      4c8943c6bb3b806bb44cd30d5d0d2b9c11e12afd05d8a144ab71bb3ad7623681
wrapper:             /home/clawrun/analise_storage/bin/run-claw-gemma
wrapper SHA-256:     0d1d5ccbc4e9fbe0acb79aec268344d082d0eda596f483c7aa9f8bd0057380ed
model alias:         gemma4 -> gemma-4-31b-it
```

Repository не полностью clean: локальная модификация
`rust/crates/api/src/providers/mod.rs` ограничивает максимальный Gemma output
значением `CLAW_GEMMA_MAX_OUTPUT_TOKENS`, default `4096`, вместо `32000`. Это
разумный agent guard, а не замена модели. Бинарь сообщает `Git SHA unknown`,
поэтому воспроизводимость нужно привязывать одновременно к source commit,
локальному diff и SHA-256 бинаря.

Wrapper задаёт endpoint, выбирает `--model gemma4` и включает
`--dangerously-skip-permissions`. Значение API key намеренно не записано в этот
документ.

## Почему API совместим

Alias `gemma4` в
`rust/crates/api/src/providers/mod.rs` выбирает `gemma-4-31b-it` и Google
provider с переопределяемым `GOOGLE_BASE_URL`. Реализация
`rust/crates/api/src/providers/openai_compat.rs` собирает streamed OpenAI
`tool_calls`, передаёт их runtime, затем сериализует tool result в продолжение
диалога. Это соответствует `/v1/chat/completions` текущего llama-server.

Поиск по Rust source не нашёл поддержки `image_url`, `input_image` или image
content block. Поэтому direct Vision нельзя выводить только из того, что server
и модель мультимодальные. Это не отменяет внешний OCR: Claw может вызвать его
своим штатным `bash` tool и получить распознанный текст.

## End-to-end проверки

Все тесты запускались wrapper-ом от `clawrun` через реальный tunnel и текущий
двух-GPU production endpoint. Временный test file после проверки удалён тем же
непривилегированным владельцем.

### 1. Один tool call

Задача требовала вызвать Bash и выполнить безопасный `printf`.

```text
exit code:           0
elapsed:             36 s
response JSON:       valid
stderr:              empty
model:               gemma-4-31b-it
iterations:          2
tool:                bash
tool result error:   false
final answer:        CLAW_TOOL_OK
```

### 2. Последовательная agent-цепочка

Задача требовала создать временный файл с точным `CLAW_WRITE_OK`, прочитать его
и вернуть то же значение.

```text
exit code:           0
elapsed:             51 s
response JSON:       valid
stderr:              empty
model:               gemma-4-31b-it
iterations:          4
tools:               bash -> write_file -> read_file
all tool errors:     false
file exists:         true
file exact:          true
final answer:        CLAW_WRITE_OK
```

Ошибка `PermissionError` в первоначальном cleanup harness не относилась к Claw:
post-check пытался удалить принадлежащий `clawrun` файл от пользователя
`ubuntu`. Файл затем удалён корректно через `sudo -u clawrun`; agent и все его
tools до cleanup уже завершились успешно.

### 3. OCR через Claw Bash tool

На VM120 подтверждены:

```text
Tesseract:           5.3.4
OCRmyPDF:            15.2.0+dfsg1
language packs:      chi_sim,deu,eng,fra,kaz,osd,rus,tur,ukr
PDF helpers:         pdftoppm, pdftotext
```

Custom OCR helpers:

```text
tasks/tools/declarant-crosscheck-ocr-evidence-v1.py
tasks/tools/declarant-fast-ocr-retry-v1.py
tasks/tools/declarant-finalize-fast-ocr-v1.py
tasks/tools/ocr-enrich-gtd-pilot100-v1.py
tasks/tools/ocr-enrich-gtd-pilot100-v2.py
```

End-to-end fixture содержал `OCR TOOL OK 4827`. Claw самостоятельно вызвал через
`bash` команду Tesseract, получил результат и вернул его модели:

```text
exit code:           0
elapsed:             35 s
response JSON:       valid
stderr:              empty
model:               gemma-4-31b-it
iterations:          2
tool:                bash
tool result error:   false
final answer:        OCR TOOL OK 4827
```

Таким образом, OCR — не native Rust tool с отдельным именем в registry Claw, но
это установленный и реально доступный agent tool через `bash`. В рабочем смысле
Claw умеет пользоваться OCR уже сейчас.

## Практические ограничения

1. **Контекст — 32K, не исторические 262K.** Этого достаточно для текущего
   system prompt (~2.2 KiB) и обычных coding turns, но длинные логи и tool output
   быстро накапливаются. Для долгой задачи нужны короткие этапы, compaction или
   новый сеанс. Maximum probe `512K total / p2` подтвердил два slots по 256K,
   но оставил лишь `75.5 MiB` VRAM на srv1 и был отклонён с rollback. Следующие
   кандидаты — два slots по 192K/160K/128K. Полный протокол:
   `GEMMA4-CLAW-CONTEXT-PARALLEL-PROBE-20260720.md`.
2. **`parallel=1`.** Один Claw запрос работает нормально, но одновременные Claw
   и Telegram запросы обслуживаются последовательно. `parallel=2` повышает
   aggregate throughput, но при total 32K оставит примерно 16K на slot; для
   длинного агента это отдельный A/B, не готовый default.
3. **RAM prompt cache выключен.** `--cache-ram 0` не занимает RAM для повторных
   prompts, но повторяющийся Claw system prompt и накопленная история снова
   проходят prefill. Это выбранный владельцем компромисс; cache не включать.
4. **Chat template требует обновления.** Текущий server предупреждает:
   `outdated gemma4 chat template, applying compatibility workarounds`. Простые
   и последовательные tools прошли, но перед длительным unattended mode нужен
   официальный template и regression suite для multi-tool, malformed args,
   tool errors, continuation и reasoning turns.
5. **Есть OCR, но нет direct Vision transport.** Скриншоты можно распознавать
   установленными Tesseract/OCRmyPDF helpers через `bash`; это проверено. Однако
   исходные pixels по текущему agent path не попадают в high-resolution Gemma
   image encoder. Для понимания схем, UI и не-OCR деталей нужен OpenAI
   `image_url` provider patch либо отдельный multimodal bridge.
6. **Usage telemetry неполная.** В Claw JSON token/cache counters остались
   нулевыми, хотя llama-server выполнил запросы. Это проблема нормализации usage,
   а не inference; для capacity planning использовать server metrics/logs.
7. **Dangerous mode ограничен ОС, а не моделью.** Запускать только в VM120 от
   `clawrun`, без sudo. Read-only storage должен оставаться `ro`; никогда не
   переносить этот wrapper на Proxmox host.

## Безопасный запуск

Внутри VM120:

```bash
sudo -H -u clawrun \
  /home/clawrun/analise_storage/bin/run-claw-gemma \
  --output-format json \
  prompt 'Коротко опиши задачу, разрешённые пути и ожидаемый результат.'
```

Перед длинной сессией:

```bash
systemctl is-active gemma-api-tunnel.service
curl -fsS http://127.0.0.1:18080/health
findmnt -no TARGET,SOURCE,FSTYPE,OPTIONS \
  /home/clawrun/analise_storage/storage-readonly
sudo -u clawrun test ! -w \
  /home/clawrun/analise_storage/storage-readonly && echo OK_readonly
```

После сессии:

```bash
pgrep -af 'target/release/claw' || true
```

## Что улучшить перед полностью автономными длинными задачами

1. Подключить официальный Gemma 4 chat template отдельным versioned-файлом и
   повторить tool regression suite.
2. Добавить тесты: несколько tool calls в одном turn, tool error/retry,
   некорректный JSON args, длинный tool output, context pressure и cancellation.
3. Исправить OpenAI-compatible usage mapping, чтобы Claw видел prompt/completion
   tokens и мог ограничивать историю по реальным данным.
4. Оформить существующие OCR helpers единым безопасным wrapper-ом с
   file-size/type/path/time limits; отдельно реализовать image content
   serialization, если модели нужно видеть pixels, а не только OCR text.
5. Проверить `ngram-mod` только на повторяющемся code corpus: это примерно 16 MiB
   runtime state, не RAM prompt cache, но default оставить выключенным.
6. Не включать `parallel=2`, пока не измерены agent context и одновременная
   Telegram нагрузка; maximum 256K-per-slot профиль уже исключён из-за VRAM.

## Итоговое решение

**Использовать Claw Cod с этой Gemma можно уже сейчас** для текстовых coding и
file/tool задач внутри VM120. Текущие доказательства подтверждают базовую и
многошаговую agent-функцию. Для unattended длительных сессий остаются два P0:
официальный chat template с расширенной regression suite и контроль 32K context.
OCR-задачи уже выполняются существующим Tesseract/OCRmyPDF контуром через Bash.
Для задач, где модели нужны сами pixels, а не распознанный текст, остаётся нужен
отдельный direct Vision transport; production Vision 1120 сам по себе это
ограничение Claw provider не снимает.
