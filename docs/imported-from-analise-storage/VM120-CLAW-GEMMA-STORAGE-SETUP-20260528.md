# VM120: claw-code-parity + Gemma API + read-only storage

Дата настройки: 2026-05-28
Последняя live-проверка: 2026-07-20
Цель: запускать `claw-code-parity` внутри VM120 от отдельного пользователя, подключать локальную Gemma на srv1 через API, читать `storage` только read-only и сохранять найденное/отчёты только в рабочую папку VM.

> Секции от 2026-05-28 ниже сохраняют исторические checkpoints. Текущий
> authoritative статус backend и Claw приведён в секции от 2026-07-20 в конце
> документа.

## Итоговый статус

Готово и проверено:

- `claw-code-parity` установлен и собран внутри VM120.
- Gemma API доступен в VM120 локально как `http://127.0.0.1:18080/v1`.
- `storage` read-only SSHFS настроен для VM120: `/home/clawrun/analise_storage/storage-readonly`; на live-проверке 2026-07-20 unit активен и mount имеет `ro,default_permissions,allow_other`.
- Рабочий пользователь: `clawrun`, без sudo.
- Запись разрешена в рабочую область VM: `/home/clawrun/analise_storage/results`.
- Для копирования найденных файлов используется безопасная команда `storage-safe-copy`, которая читает из read-only mount и пишет копии в `results/found-files`.
- Smoke-test `claw --dangerously-skip-permissions` через Gemma API выполнен: Claw вызвал bash tool и создал отчёт `results/claw-danger-smoke-report.md`.

## Последний safety checkpoint — 2026-05-28 21:50 MSK

Документ по защите новых дисков:

```text
/work/claster/analise_storage/DISK-READONLY-PROTECTION-20260528.md
```

Проверено после паузы агента:

```text
VM120 claw/orchestrator processes: none
storage-readonly-sshfs.service: inactive
/home/clawrun/analise_storage/storage-readonly: not mounted, root:root 0555
clawrun cannot write to the unmounted storage-readonly mountpoint
storage authorized_keys forced internal-sftp -R: present
```

Новые источники данных protected read-only:

```text
srv2    /dev/sda2 -> /mnt/gtd-hitachi-ro     # Hitachi NTFS, GTD/customs declarations, block ro=1
storage /dev/sdg1 -> /mnt/excise-docs-ro     # external NTFS, excise docs, block ro=1
```

Important: VM120 sees `storage:/mnt/excise-docs-ro` only when storage SSHFS is mounted. VM120 does **not** see `srv2:/mnt/gtd-hitachi-ro` until a separate read-only access path from VM120 to srv2 is created.

## Важные ограничения безопасности

1. **Не запускать Claw на Proxmox host/srv1.** Запуск только внутри VM120.
2. **Не монтировать storage/read-only source disks read-write.** Любой mount `storage`, `srv2:/mnt/gtd-hitachi-ro`, `storage:/mnt/excise-docs-ro` должен оставаться `ro`.
3. **Не давать пользователю `clawrun` sudo.** Это часть защиты от удаления/перезаписи storage.
4. Dangerous/no-confirm режим допустим только внутри VM120 под `clawrun`, потому что защита сделана на уровне ОС:
   - `storage-readonly` смонтирован с `ro` или, если paused, mountpoint закрыт `root:root 0555`;
   - SSH-ключ на storage принудительно ограничен `internal-sftp -R`;
   - `clawrun` не имеет sudo;
   - результаты пишутся только в `/home/clawrun/analise_storage/results`.

## Компоненты

### VM120

- Hostname: `llm-vpn-ubuntu24`
- IP: `<vm120-lan>`
- Рабочий пользователь: `clawrun`
- Рабочая папка: `/home/clawrun/analise_storage`
- Symlink для удобства: `/home/ubuntu/analise_storage -> /home/clawrun/analise_storage`

### Репозиторий Claw

- Путь в VM120: `/home/clawrun/analise_storage/claw-code-parity`
- Источник: `https://github.com/FrankRappo/claw-code-parity`
- Проверенный commit: `5fbee8e82b9714882698e0be65f1ff31b19e4643`
- Бинарь: `/home/clawrun/analise_storage/claw-code-parity/rust/target/release/claw`

Сборка потребовала Rust через `rustup`, потому что apt cargo 1.75 не понимает `Cargo.lock` v4. В VM установлен rustup toolchain `rustc 1.95.0` для пользователя `clawrun`.

### Gemma API tunnel

Systemd service в VM120:

```bash
systemctl status gemma-api-tunnel.service
```

Назначение:

```text
VM120 127.0.0.1:18080 -> srv1 127.0.0.1:8080
```

Claw использует OpenAI-compatible API через Google provider-переменные:

```bash
export GOOGLE_API_KEY=local-gemma
export GOOGLE_BASE_URL=http://127.0.0.1:18080/v1
--model gemma4
```

Проверка API из VM120:

```bash
curl -fsS http://127.0.0.1:18080/health
```

Ожидаемо:

```json
{"status":"ok"}
```

### Read-only storage mount

Systemd service в VM120:

```bash
systemctl status storage-readonly-sshfs.service
```

Mount path:

```text
/home/clawrun/analise_storage/storage-readonly
```

Проверка:

```bash
findmnt -no TARGET,SOURCE,FSTYPE,OPTIONS /home/clawrun/analise_storage/storage-readonly
sudo -u clawrun test ! -w /home/clawrun/analise_storage/storage-readonly && echo OK_readonly
```

Фактически проверено:

```text
fuse.sshfs ... ro,nosuid,nodev,...,default_permissions,allow_other
storage_write_probe_blocked=1
Read-only file system
```

Дополнительная защита на storage: ключ VM120 добавлен в `authorized_keys` storage с forced command:

```text
restrict,command="internal-sftp -R"
```

То есть даже при ошибке mount-опций ключ предназначен только для read-only SFTP. На последнем checkpoint storage SSHFS остановлен, а пустая mount-точка закрыта от записи.

## Рабочие директории VM120

```text
/home/clawrun/analise_storage/
  claw-code-parity/        # приложение
  storage-readonly/        # read-only mount storage
  results/                 # всё, что Claw может сохранять
  results/found-files/     # скопированные найденные файлы
  manifests/               # будущие индексы/списки
  logs/                    # локальные логи VM
  prompts/                 # правила/промпты
  bin/                     # helper scripts
  tmp/                     # временные файлы
```

## Helper-команды

### Wrapper запуска Claw с Gemma API

```bash
/home/clawrun/analise_storage/bin/run-claw-gemma --output-format json prompt 'Use your bash tool to run: pwd. Then answer DONE.'
```

Wrapper делает:

```bash
export HOME=/home/clawrun
export GOOGLE_API_KEY=local-gemma
export GOOGLE_BASE_URL=http://127.0.0.1:18080/v1
cd /home/clawrun/analise_storage
claw --model gemma4 --dangerously-skip-permissions ...
```

### Smoke-test Claw + storage guard

```bash
/home/clawrun/analise_storage/bin/run-claw-storage-smoke
```

Он просит Claw через bash tool выполнить:

```bash
bin/storage-smoke-report
```

Результат:

```text
/home/clawrun/analise_storage/results/claw-danger-smoke-report.md
/home/clawrun/analise_storage/results/claw-danger-helper-smoke-output.json
```

### Безопасное копирование найденного файла

```bash
storage-safe-copy etc/hostname
```

Правила `storage-safe-copy`:

- источник всегда относительно `/home/clawrun/analise_storage/storage-readonly`;
- абсолютные пути и выход за пределы mount запрещены;
- копия пишется в `/home/clawrun/analise_storage/results/found-files/...`;
- существующий файл не перезатирается;
- копии делаются read-only (`chmod a-w`);
- журнал копий: `/home/clawrun/analise_storage/results/copied-files.tsv`.

## Проверенные команды и результаты

### 1. Claw simple API smoke

```bash
GOOGLE_API_KEY=local-gemma \
GOOGLE_BASE_URL=http://127.0.0.1:18080/v1 \
/home/clawrun/analise_storage/claw-code-parity/rust/target/release/claw \
  --model gemma4 --output-format text prompt 'Ответь строго одним словом: OK'
```

Результат: `OK`.

Лог:

```text
/work/claster/analise_storage/logs/vm120-claw-gemma-smoke-20260528-140231.log
```

### 2. Dangerous/no-confirm tool smoke

```bash
/home/clawrun/analise_storage/bin/run-claw-storage-smoke
```

Результат: Claw вызвал `bash` tool и получил `REPORT_READY .../results/claw-danger-smoke-report.md`.

Лог:

```text
/work/claster/analise_storage/logs/vm120-claw-danger-helper-smoke-20260528-141348.log
```

### 3. Storage write protection

Проверено:

```text
mount: fuse.sshfs ro
storage_write_probe_blocked=1
error: Read-only file system
```

Лог:

```text
/work/claster/analise_storage/logs/vm120-storage-copy-guard-smoke-20260528-140412.log
```

## Как запускать следующий реальный поиск

Запускать из VM120 только под пользователем `clawrun`:

```bash
sudo -H -u clawrun /home/clawrun/analise_storage/bin/run-claw-gemma \
  --output-format json \
  prompt 'Use your bash tool to inspect storage-readonly read-only. Copy only truly relevant found files with storage-safe-copy. Write reports only under results/. First read prompts/storage-agent-rules.md. Task: <описать что искать>.'
```

Практическая рекомендация: для долгого анализа storage лучше давать Claw короткие, точные команды или просить запускать подготовленные helper scripts через bash tool. На этой модели сложные многошаговые длинные промпты иногда дают `assistant stream produced no content`, а короткие tool-задачи (`pwd`, `ls`, `touch`, `bin/storage-smoke-report`) проверенно работают.

## Диагностика

Статусы сервисов внутри VM120:

```bash
systemctl status gemma-api-tunnel.service --no-pager
systemctl status storage-readonly-sshfs.service --no-pager
```

Проверка mount:

```bash
findmnt -no TARGET,SOURCE,FSTYPE,OPTIONS /home/clawrun/analise_storage/storage-readonly || echo not_mounted
sudo -u clawrun test ! -w /home/clawrun/analise_storage/storage-readonly && echo OK_not_writable
```

Проверка новых read-only source disks с соответствующих hosts:

```bash
# srv2
findmnt -no TARGET,SOURCE,FSTYPE,OPTIONS /mnt/gtd-hitachi-ro
blockdev --getro /dev/sda /dev/sda1 /dev/sda2

# storage
findmnt -no TARGET,SOURCE,FSTYPE,OPTIONS /mnt/excise-docs-ro
blockdev --getro /dev/sdg /dev/sdg1
```

Проверка Claw:

```bash
sudo -H -u clawrun /home/clawrun/analise_storage/bin/run-claw-gemma --version
sudo -H -u clawrun /home/clawrun/analise_storage/bin/run-claw-storage-smoke
```

Проверка, что случайные Claw-процессы не висят:

```bash
pgrep -af 'claw|rusty-claude-cli' || true
```

---

## LLM context profile note — 2026-05-28

Historical single-GPU high-context profile was:

```text
--ctx-size 262144
--parallel 1
--batch-size 512
--ubatch-size 128
--cache-type-k q8_0
--cache-type-v q8_0
```

Proof log:

```text
/work/claster/analise_storage/logs/srv1-set-ctx262144-and-launch-prep-20260528-184944.log
```

Current backend after PSU/2-GPU cutover is documented below and uses conservative `ctx-size 4096` until 2-GPU high-context is separately re-tested.


## Current API backend update — 2026-05-28 22:05 MSK

The VM120 API tunnel still points to `srv1:8080`, but the backend behind that port changed:

```text
Before: srv1 single-GPU Vulkan0, ctx-size 262144
Now:    srv1 Vulkan0 + srv2 VM100 RPC0, ctx-size 4096, parallel 1
Health from VM120: {"status":"ok"}
```

This keeps the same VM120 endpoint:

```text
http://127.0.0.1:18080/v1
```

Do not assume current context is `262144` while the conservative 2-GPU post-PSU profile is running.

## Current Claw/Gemma backend — 2026-07-20

Исторический профиль `ctx-size 4096` выше больше не является текущим. VM120
по-прежнему использует тот же endpoint, но production backend теперь:

```text
VM120 127.0.0.1:18080 -> srv1 127.0.0.1:8080
llama.cpp b10012, Vulkan0 + srv2 RPC0
Gemma 4 31B Abliterated Q4_K_M
ctx-size 32768, parallel 1, Q4 KV
Vision range 280–1120, batch/ubatch/mtmd limit 1280
host-memory prompt cache disabled: --cache-ram 0
```

Live status:

```text
gemma-api-tunnel.service: active
storage-readonly-sshfs.service: active
storage mount: fuse.sshfs ro
VM120 /health: {"status":"ok"}
clawrun sudo: no
```

Установленный бинарь:

```text
Claw Code 0.1.0, build date 2026-03-31
source commit: 5fbee8e82b9714882698e0be65f1ff31b19e4643
binary SHA-256: 4c8943c6bb3b806bb44cd30d5d0d2b9c11e12afd05d8a144ab71bb3ad7623681
wrapper SHA-256: 0d1d5ccbc4e9fbe0acb79aec268344d082d0eda596f483c7aa9f8bd0057380ed
```

2026-07-20 повторно пройдены три end-to-end smoke:

1. `bash` tool, два agent iterations, финальный `CLAW_TOOL_OK`;
2. `bash -> write_file -> read_file`, четыре iterations, exact file content и
   финальный `CLAW_WRITE_OK`;
3. `bash -> tesseract`, два iterations, распознан exact fixture
   `OCR TOOL OK 4827`.

Все запросы завершились с exit code 0, valid JSON, пустым stderr и без tool
errors. После теста временный файл удалён, висящих Claw processes нет.

**Вывод:** Claw уже можно использовать как текстовый coding-agent с этой Gemma.
Ограничения: текущий контекст 32K, один server slot, RAM prompt cache выключен,
а Rust provider не передаёт `image_url`/image blocks. При этом на VM120 уже есть
и проверенно вызывается через Claw Bash tool внешний OCR-контур: Tesseract
`5.3.4`, OCRmyPDF `15.2.0`, языки `rus+eng` и custom helpers в `tasks/tools`.
Значит, OCR screenshots работает через извлечение текста; только direct Vision
1120 с передачей pixels требует provider patch.

Полный протокол совместимости, security boundaries, known gaps и план hardening:

```text
/work/claster/analise_storage/CLAW-COD-GEMMA4-COMPATIBILITY-20260720.md
```

Capacity probe двух параллельных Claw-сессий:

```text
/work/claster/analise_storage/GEMMA4-CLAW-CONTEXT-PARALLEL-PROBE-20260720.md
```

Проверено, что `CTX_SIZE=524288`, `PARALLEL=2` создаёт два slots по 256K, но
оставляет только `75.5 MiB` VRAM на srv1. Этот профиль отклонён; production
успешно возвращён к `32K/p1`. Следующие кандидаты после возобновления A/B —
192K, 160K и 128K на slot.
