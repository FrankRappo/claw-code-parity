# План: безопасный анализ `storage` через VM120 + локальную Gemma API + claw-code-parity

Дата: 2026-05-28

## Цель

Запустить кастомное приложение `claw-code-parity` для автономного поиска нужных
файлов на сервере `storage`, используя нашу локальную Gemma API, но сделать это
безопасно:

- не запускать `claw` с `danger-full-access` на Proxmox host `srv1`;
- не давать агенту write-доступ к `storage`;
- результаты сохранять на SSD `srv1` через диск VM120;
- рабочую LLM на `srv1:8080` не ломать;
- `srv2` не трогать.

## Текущие факты

```text
Gemma API: srv1, 127.0.0.1:8080, single-GPU, health OK
claw repo: уже скачан на srv1 SSD: /mnt/ssd/claw-code-parity
local /work/claster external copy: удалена
VM120: running, IP <vm120-lan>
VM120 disk: srv1 /mnt/ssd, внутри VM примерно /home/ubuntu доступно ~191G
storage: <storage-lan>
VM120 -> storage ping: OK
VM120 -> storage SSH 22: open
VM120 -> storage NFS 2049: closed
VM120 -> storage SMB 445: closed
srv2: не использовать для этой задачи
```

## Ключевое решение

`claw-code-parity` запускать **внутри VM120**, а не на `srv1` host.

Причина: режим без подтверждений / `--dangerously-skip-permissions` опасен на
Proxmox host. В VM120 ущерб ограничен самой VM и заранее выданными mount/path.

Целевая схема:

```text
srv1 host
  ├─ Gemma llama-server: 127.0.0.1:8080
  ├─ VM120 disk on /mnt/ssd
  └─ claw repo source copy: /mnt/ssd/claw-code-parity

VM120
  ├─ runs claw-code-parity
  ├─ sees Gemma API via tunnel/proxy to srv1:8080
  ├─ mounts storage read-only over SSH/SSHFS or rsync-style read-only scan
  └─ writes results to /home/ubuntu/analise_storage/results

storage
  └─ read-only source of files for analysis
```

## Safety rules

1. Не запускать `claw --dangerously-skip-permissions` на `srv1` host.
2. Не монтировать `storage` read-write.
3. Не удалять, не переименовывать, не форматировать и не чистить данные на
   `storage`.
4. Не копировать весь `storage` вслепую на SSD — сначала сделать inventory и
   candidate list, потом копировать только выбранные/нужные файлы.
5. Не менять текущий `srv1` llama-server, пока не подготовлен безопасный API
   доступ для VM120.
6. Не использовать `srv2`.
7. Не записывать секреты/API keys в план, README или логи.

## План работ

### Этап 0 — зафиксировать исходное состояние

Проверить и сохранить в лог внутри этой папки:

```text
/work/claster/analise_storage/logs/preflight-YYYYMMDD-HHMMSS.log
```

Что проверить:

- `srv1` Gemma API health;
- VM120 status/IP;
- свободное место внутри VM120;
- доступность `storage` по ping/SSH из VM120;
- что `claw` сейчас не запущен;
- что `storage` не смонтирован read-write.

Acceptance:

```text
srv1 health OK
VM120 running
storage reachable from VM120
no claw process running
no write mount to storage
```

### Этап 1 — подготовить рабочую структуру в VM120

Внутри VM120 создать:

```text
/home/ubuntu/analise_storage/
  claw-code-parity/        # приложение
  storage-readonly/        # read-only mount/source view
  results/                 # найденные/скопированные файлы
  manifests/               # списки файлов, размеры, hashes
  logs/                    # логи запусков
  prompts/                 # системные/рабочие prompts для агента
```

Код `claw-code-parity` лучше держать внутри VM120, а не запускать напрямую из
host path. Источник на `srv1 /mnt/ssd/claw-code-parity` можно использовать как
копию для переноса в VM120.

Acceptance:

```text
VM120 contains /home/ubuntu/analise_storage/claw-code-parity
results/manifests/logs/prompts directories exist
all writes go only under /home/ubuntu/analise_storage
```

### Этап 2 — дать VM120 доступ к Gemma API

Сейчас Gemma API слушает только `srv1 127.0.0.1:8080`, поэтому VM120 напрямую его
не видит.

Безопасные варианты:

#### Вариант A — SSH tunnel из VM120 на srv1 localhost

В VM120 поднять local tunnel:

```text
VM120 127.0.0.1:18080 -> srv1 127.0.0.1:8080
```

Плюсы:

- не надо открывать llama-server на LAN;
- легко остановить;
- меньше риск случайного внешнего доступа.

Минусы:

- нужен стабильный SSH доступ VM120 -> srv1.

#### Вариант B — bind/reverse proxy только на VM-facing interface

Поднять на `srv1` локальный reverse proxy, доступный только с VM120/LAN.

Плюсы:

- удобно для постоянной работы.

Минусы:

- больше surface area;
- надо аккуратно ограничить доступ firewall/listen address.

Рекомендуемый первый вариант: **A, SSH tunnel**.

Acceptance:

```text
inside VM120: curl http://127.0.0.1:18080/health -> {"status":"ok"}
no public exposure of srv1 llama-server
```

### Этап 3 — проверить совместимость `claw-code-parity` с локальной Gemma API

Нужно выяснить, какой API формат реально ожидает `claw`:

- Anthropic-compatible `/v1/messages`;
- OpenAI-compatible `/v1/chat/completions`;
- Google/OpenAI-compatible provider;
- собственная registry model alias логика.

Наш `llama-server` сейчас даёт OpenAI-compatible API. Если `claw` умеет
OpenAI-compatible через `OPENAI_API_KEY`/`OPENAI_BASE_URL`, настроить так:

```text
OPENAI_BASE_URL=http://127.0.0.1:18080/v1
OPENAI_API_KEY=dummy-local-key
MODEL=<локальный model id/alias, который claw примет>
```

Если `claw` жёстко ожидает Anthropic API, понадобится маленький local adapter:

```text
Anthropic /v1/messages -> OpenAI /v1/chat/completions -> srv1 llama-server
```

Acceptance:

```text
claw one-shot prompt from VM120 returns a short answer via local Gemma
no file tools enabled yet
no dangerous permission mode yet
```

### Этап 4 — подключить `storage` read-only

Так как NFS/SMB закрыты, первичный путь — SSH/SSHFS/SFTP.

Рекомендуемые варианты:

#### Вариант A — SSHFS read-only mount

В VM120:

```text
/home/ubuntu/analise_storage/storage-readonly
```

монтируется через SSHFS с read-only опциями. Дополнительно использовать Linux
permissions, чтобы пользователь `ubuntu` не мог писать в mount.

#### Вариант B — без mount: inventory через ssh/find/rsync dry-run

Если SSHFS нежелателен, выполнять read-only команды на `storage` через SSH и
сохранять manifests в VM120:

```text
find ... -printf ... > manifests/storage-file-list.tsv
sha256sum selected files only
rsync --dry-run for candidate validation
```

Рекомендуемый первый вариант: **B для inventory**, затем **A или selective scp**
для копирования выбранных файлов.

Acceptance:

```text
storage inventory created
no writes on storage
results path is on VM120 disk / srv1 SSD
```

### Этап 5 — inventory всего storage без LLM-агента

Перед тем как давать агенту `danger-full-access`, сделать детерминированный
read-only inventory обычными инструментами:

```text
manifests/storage-files.tsv      # path, size, mtime, type
manifests/storage-dirs.tsv       # dir sizes if affordable
manifests/storage-extensions.tsv # count/size by extension
manifests/storage-large-files.tsv
manifests/storage-recent-files.tsv
```

Исключения/осторожность:

- не читать специальные pseudo FS (`/proc`, `/sys`, `/dev`, `/run`), если они
  видны;
- не считать hashes для всех больших файлов сразу;
- не копировать всё.

Acceptance:

```text
inventory files exist in manifests/
inventory has counts and total size
no data copied yet except manifests/logs
```

### Этап 6 — ограничить задачу агента и подготовить prompt

Агенту дать не свободную формулировку "перерой всё", а строгую задачу:

```text
Ты работаешь внутри VM120.
Разрешённые write paths:
- /home/ubuntu/analise_storage/results
- /home/ubuntu/analise_storage/manifests
- /home/ubuntu/analise_storage/logs

Storage source is read-only.
Do not modify, delete, rename, chmod, chown, move, or write to storage.
First inspect manifests. Then propose/search candidate files.
Copy only files that match criteria into results/, preserving relative paths.
Write a report with what was copied and why.
```

Нужно заранее определить критерии "нужные файлы". Если критерии неизвестны,
первый запуск агента должен делать классификацию/обзор, а не копирование.

Acceptance:

```text
prompt saved under prompts/
write boundaries explicit
copy criteria explicit or first-run mode is inventory/classification only
```

### Этап 7 — первый безопасный запуск `claw` без dangerous mode

Запустить `claw` в VM120 на маленькой задаче:

```text
status/check model
read manifests summary
write one small report to results/test-report.md
```

Без `--dangerously-skip-permissions`.

Acceptance:

```text
claw can call local Gemma
claw can read manifests
claw can write into results
claw cannot/does not write to storage
```

### Этап 8 — запуск с dangerous permissions только внутри VM120

Только после успешного Этапа 7:

```text
claw --dangerously-skip-permissions
```

Но рабочая директория должна быть строго:

```text
/home/ubuntu/analise_storage
```

И storage должен быть read-only или вообще доступен только через manifests +
selective copy command wrappers.

Acceptance:

```text
claw runs only inside VM120
cwd is /home/ubuntu/analise_storage
storage mount is read-only
all outputs are under results/logs/manifests
```

### Этап 9 — копирование найденных файлов на SSD

Так как VM120 диск расположен на `srv1 /mnt/ssd`, сохранение в VM120 уже
физически сохраняет результаты на SSD.

Целевой путь внутри VM:

```text
/home/ubuntu/analise_storage/results/found-files/
```

Отчёт:

```text
/home/ubuntu/analise_storage/results/report.md
/home/ubuntu/analise_storage/results/copied-files.tsv
```

Формат `copied-files.tsv`:

```text
source_path	dest_path	size	mtime	sha256_optional	reason
```

Acceptance:

```text
found files copied under results/found-files/
report.md explains selection
copied-files.tsv lists every copied file
storage remains unchanged
```

### Этап 10 — финальная проверка

Проверить:

- `srv1` Gemma API health still OK;
- VM120 disk free space;
- no writes happened on storage;
- no runaway `claw` processes;
- results/report.md exists;
- copied-files.tsv exists;
- sample copied files readable.

Acceptance:

```text
health OK
storage unchanged
results complete
logs saved
```

## Stop conditions

Остановиться и не продолжать, если:

- Gemma API на `srv1` стала unstable;
- VM120 не видит `storage`;
- `storage` доступен только read-write и нельзя гарантировать read-only режим;
- `claw` требует небезопасного запуска на host;
- приложение пытается писать вне `/home/ubuntu/analise_storage`;
- начинаются массовые копирования без критериев отбора;
- свободное место на VM120/SSD падает ниже заранее заданного лимита.

## Что не делать

```text
НЕ запускать claw на srv1 host с danger-full-access
НЕ прокидывать GPU в VM120 для этой задачи
НЕ трогать srv2
НЕ монтировать storage read-write
НЕ копировать весь storage без отбора
НЕ хранить секреты в markdown/logs
```

## Открытые вопросы перед исполнением

1. Какие именно файлы считаются "нужными"?
   - документы?
   - проекты/исходники?
   - фото/видео?
   - конфиги?
   - файлы по ключевым словам?
2. Можно ли использовать SSHFS, или лучше только SSH inventory + selective copy?
3. Какой лимит места выделить под результаты на SSD?
4. Нужно ли сохранять только найденные файлы или также полный индекс `storage`?
5. Нужно ли потом открыть результаты наружу через VM/VPN/архив?

## Рекомендуемый следующий шаг

Сначала выполнить Этапы 0–3:

```text
preflight -> рабочая структура VM120 -> Gemma tunnel -> claw smoke test
```

И только после этого подключать read-only inventory `storage`.

---

## Фактическая реализация 2026-05-28

Подробный runbook и проверенные параметры после настройки записаны в:

`/work/claster/analise_storage/VM120-CLAW-GEMMA-STORAGE-SETUP-20260528.md`

Ключевой итог: `claw-code-parity` собран в VM120, Gemma API подключен через `127.0.0.1:18080`, `storage` смонтирован read-only через SSHFS + forced `internal-sftp -R`, dangerous/no-confirm smoke-test Claw прошёл через helper `bin/storage-smoke-report`.
