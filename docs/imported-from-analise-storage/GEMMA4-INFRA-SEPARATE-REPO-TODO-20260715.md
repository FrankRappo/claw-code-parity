# TODO: выделить Gemma 4 infrastructure в отдельный GitHub-репозиторий

**Статус:** запланировано, не выполнено
**Дата фиксации:** 2026-07-15

## Текущее состояние

Отдельного GitHub-репозитория для настройки модели пока нет. Версионируемые
Gemma 4 файлы находятся в приватном репозитории:

```text
https://github.com/FrankRappo/analise_storage
```

Основной commit развёртывания:

```text
c4ee64d8d9a34fa3f73b23011a4c2d35a920f5f1
```

Подтверждение реального Telegram screenshot path:

```text
5d8db43
```

Текущие Git-safe файлы:

```text
llm_ops/systemd/
llm_ops/telegram_bot/
llm_ops/benchmarks/
GEMMA4-31B-TWO-GPU-VISION-SETUP-20260715.md
TELEGRAM-GEMMA4-VISION-BOT-20260715.md
GEMMA4-31B-AMD-VULKAN-UPGRADE-PLAN-20260715.md
```

Следующие локальные каталоги сами Git-репозиториями не являются:

```text
/work/claster/llm-gpu-storage-cluster-20260527
/work/claster/gemma431b_upgrade
```

## Будущая задача

Создать отдельный **private** GitHub-репозиторий. Рабочее имя, пока не создано и
не зарезервировано:

```text
FrankRappo/gemma4-31b-infra
```

Название можно изменить при создании. До фактического создания единственным
Git source of truth остаётся `FrankRappo/analise_storage`.

## Что перенести

Рекомендуемая структура:

```text
gemma4-31b-infra/
  README.md
  docs/
    two-gpu-vision-setup.md
    telegram-vision-bot.md
    amd-vulkan-upgrade-plan.md
    security-and-rollback.md
  systemd/
    llm-gemma4-primary.service
    llm-gpu2-rpc-worker.service
    llm-gemma4-primary.env.example
  scripts/
    run-gemma4-31b-2gpu-vision.sh
    run-gemma4-rpc-b10012.sh
  telegram_bot/
    telegram_gemma_bot.py
    tests/
  benchmarks/
    benchmark_openai.py
```

Переносить только код, тесты, обезличенные примеры конфигурации, pinned hashes и
проверенные runbooks.

## Что запрещено переносить

Никогда не добавлять в новый репозиторий:

```text
TELEGRAM_BOT_TOKEN
BOT_ALLOWED_USER_IDS / BOT_ALLOWED_USERNAMES с реальными значениями
SYSTEM_PROMPT из private env, если он содержит частные данные
private/access.md
SSH passwords, keys, tokens, Git auth helpers
/etc/default/llm-gemma4-primary с локальными секретами
/opt/tg-gemma-bot/tg-gemma-bot.env
*.gguf, *.safetensors, model weights, mmproj binaries
raw datasets, indexes, logs, journal dumps, backups
IP/credential inventories, которые не нужны публичному runbook
```

Репозиторий должен оставаться private даже после очистки.

## Порядок выделения

1. Создать новый private repository с защитой основной ветки.
2. Собрать curated export из текущего commit `analise_storage`, а не копировать
   весь `/work/claster`.
3. Выполнить secret scan до первого commit.
4. Переписать абсолютные operational paths как переменные и примеры там, где это
   не ломает runbook.
5. Сохранить pinned llama.cpp/model/mmproj SHA-256 и решения о MTP.
6. Запустить 15+ Telegram tests, Python compile, shell syntax и systemd verify.
7. Проверить, что новый repo не содержит моделей, env, credentials и logs.
8. Сделать Lore commit с исходным commit `c4ee64d` в `Related:`.
9. Push в новый private GitHub repository.
10. Обновить ссылки в `analise_storage`,
    `/work/claster/llm-gpu-storage-cluster-20260527/README.md` и
    `/work/claster/gemma431b_upgrade/README.md`.
11. После проверки выбрать один source of truth: новый репозиторий. В
    `analise_storage` оставить только ссылку/handoff либо явно синхронизируемую
    копию; не поддерживать две молча расходящиеся реализации.

## Acceptance criteria

Выделение считается завершённым только если:

- новый GitHub repository существует и private;
- clone с чистого каталога воспроизводит тесты и сборочные инструкции;
- secret scan чистый;
- модели и runtime artifacts отсутствуют в Git history;
- server/RPC build identity rule и rollback описаны;
- Telegram screenshot tests и two-GPU runbook сохранены;
- все старые документы указывают на новый source of truth;
- deployed production не перезапускался и не изменялся только ради переноса Git.

До выполнения этих критериев не удалять Gemma-файлы из `analise_storage`.
