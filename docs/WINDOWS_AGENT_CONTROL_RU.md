# Управление запущенным ClawCod без нового чата (Windows)

Этот контроллер оставляет один процесс и одну сессию ClawCod открытыми и позволяет оркестратору передавать поправки во время работы агента.

Механизм повторяет безопасную схему OMX:

1. Полный текст сообщения **сначала** атомарно сохраняется в `.claw/control/<name>/messages/`; в окно передаётся абсолютный путь, поэтому агент может менять рабочую папку.
2. Запрос помещается в долговечную очередь `.claw/control/<name>/queue/`.
3. Фоновый диспетчер проверяет состояние того же окна ClawCod.
4. Для срочной поправки он атомарно создаёт `interrupt.signal`. Родной `HookAbortSignal` ClawCod останавливает ход, диспетчер дожидается сохранения файла той же сессии и только затем вводит короткую однострочную ссылку на сообщение.
5. После ввода запрос переносится в `delivered/`. Агент создаёт файл в `acks/`, когда прочитал полное сообщение.

Так поправка не теряется, если модель в момент отправки выполняет ход, и не создаётся новая сессия/новый чат.

## Автовосстановление (watchdog)

Для длительной автономной задачи добавьте `-Watchdog` при запуске:

```powershell
powershell -ExecutionPolicy Bypass -File .\windows\start-claw-agent.ps1 `
  -Name kimi `
  -LauncherPath .\claw-kimi.cmd `
  -WorkspacePath 'C:\claw cod' `
  -Resume latest `
  -AgentMode on `
  -Watchdog
```

Watchdog не запускает новый процесс при обычной compaction: она выполняется
внутри текущей Claw-сессии. Он срабатывает только при неожиданном завершении
процесса, запускает Claw с `-Resume latest` и отправляет короткое указание
продолжить незавершённую задачу. `stop-claw-agent.ps1` сначала ставит маркер
штатной остановки, поэтому намеренный stop не вызывает перезапуск.

К уже работающему агенту watchdog подключается отдельно:

```powershell
powershell -ExecutionPolicy Bypass -File .\windows\start-claw-agent-watchdog.ps1 `
  -Name kimi -WorkspacePath 'C:\claw cod'
```

По умолчанию допускается пять восстановлений с паузой три секунды. Параметры:
`-WatchdogRestartDelaySeconds` и `-WatchdogMaxRestarts` (`0` — без ограничения).
Интеграционная проверка:

```powershell
.\windows\agent-control\tests\test-agent-watchdog.ps1
```

## Запуск Kimi в управляемом окне

Из корня репозитория:

```powershell
powershell -ExecutionPolicy Bypass -File .\windows\start-claw-agent.ps1 `
  -Name kimi `
  -LauncherPath .\claw-kimi.cmd `
  -WorkspacePath . `
  -Resume latest `
  -AgentMode on
```

`-Resume latest` **до запуска** находит последнюю непустую локальную сессию, запускает обычный интерактивный REPL, а затем вводит `/resume <точный-session-id>` в это же окно. Это важно: CLI-вариант `claw --resume latest` лишь печатает сведения и завершается, а поиск `latest` уже после запуска выбрал бы только что созданную пустую сессию. `-AgentMode on` отправляется после точного восстановления. Launcher уже использует `--dangerously-skip-permissions`, поэтому инструменты доступны без дополнительных диалогов разрешений.

ClawCod сохраняет точное имя модели в метаданных JSONL-сессии. Контроллер читает его **до запуска launcher**, поэтому после штатной остановки, сбоя или достижения лимита восстанавливаются одновременно локальная сессия и тот же ключ persistent Kimi-чата. Явный `-Model` имеет приоритет и нужен один раз для старой сессии, созданной до появления этого поля:

```powershell
powershell -ExecutionPolicy Bypass -File .\windows\start-claw-agent.ps1 `
  -Name kimi -LauncherPath .\claw-kimi.cmd -WorkspacePath . `
  -Resume session-123 -Model 'kimi-k2d6-my-stable-lane' -AgentMode on
```

После первого сохранения этой сессии `-Model` можно не указывать. Не меняйте model/session marker при обычном перезапуске: gateway использует SHA-256 имени модели как стабильный ключ upstream-чата.

Новая задача может быть передана сразу при запуске:

```powershell
powershell -ExecutionPolicy Bypass -File .\windows\start-claw-agent.ps1 `
  -Name kimi -LauncherPath .\claw-kimi.cmd -WorkspacePath . `
  -Resume latest -AgentMode on `
  -InitialMessage 'Продолжи текущую задачу с места остановки; сначала проверь фактическое состояние и не повторяй уже выполненное.'
```

## Поправка прямо во время работы

Срочная поправка (режим по умолчанию):

```powershell
powershell -ExecutionPolicy Bypass -File .\windows\send-claw-agent.ps1 `
  -Name kimi -WorkspacePath . `
  -Message 'Не открывай новый Edge. Используй уже открытый профиль Default и занимайся только текущей ошибкой.'
```

Если агент выполняет ход, диспетчер подаст внутренний сигнал отмены только один раз, дождётся остановки хода и сохранения текущей сессии, затем введёт поправку. Если агент уже ждёт ввод, сигнал не создаётся.

Несрочное сообщение, которое нужно выполнить после текущего хода:

```powershell
powershell -ExecutionPolicy Bypass -File .\windows\send-claw-agent.ps1 `
  -Name kimi -WorkspacePath . -Mode next `
  -Message 'После теста обнови документацию и перечисли точные команды запуска.'
```

Скрипт сам хранит многострочный текст в файле. В окно всегда вводится одна физическая строка. Не перенаправляйте заранее подготовленный файл в stdin и не добавляйте `/exit` к стартовой задаче — именно это делало прежний запуск неуправляемым.

Служебная команда ClawCod без inbox-файла:

```powershell
powershell -ExecutionPolicy Bypass -File .\windows\send-claw-agent.ps1 `
  -Name kimi -WorkspacePath . -Raw -Mode next -Message '/status'
```

## Статус и экран

```powershell
powershell -ExecutionPolicy Bypass -File .\windows\get-claw-agent-status.ps1 -Name kimi -WorkspacePath .
```

В JSON видны PID окна и диспетчера, состояние `turn`, число queued/submitted/failed/acknowledged и последние строки экрана. `submitted_to_console` означает подтверждённый ввод в окно, но не завершение задачи. Для управляемого хода состояние остаётся `active` до изменения файла именно той восстановленной Claw-сессии; поэтому следующая поправка не вводится преждевременно, даже если VT-renderer не отдаёт экранный текст. Файл `acks/<id>.txt` подтверждает, что агент прочитал полный текст сообщения.

## Остановка

Остановить только текущий ход, сохранив сессию и окно:

```powershell
powershell -ExecutionPolicy Bypass -File .\windows\interrupt-claw-agent.ps1 -Name kimi -WorkspacePath .
```

Сохранить сессию и штатно закрыть ClawCod:

```powershell
powershell -ExecutionPolicy Bypass -File .\windows\stop-claw-agent.ps1 -Name kimi -WorkspacePath .
```

Принудительное завершение используется только если штатный `/exit` не сработал:

```powershell
powershell -ExecutionPolicy Bypass -File .\windows\stop-claw-agent.ps1 -Name kimi -WorkspacePath . -Force
```

## Проверка механизма без расхода API

```powershell
powershell -ExecutionPolicy Bypass -File .\windows\agent-control\tests\test-agent-control.ps1
```

Тест запускает фальшивый line-oriented Claw в отдельной Windows-консоли и проверяет Unicode-ввод, восстановление сохранённой модели, `/agent on`, атомарный inbox, однострочный trigger и штатный `/exit`. API Kimi/GLM при этом не вызывается.

## Ограничения

- Контроллер предназначен для классической Windows-консоли/ConHost. Он не использует хрупкий поиск окна мышью или имитацию кликов.
- На системах, где VT-renderer ClawCod не отдаёт текст приглашения через console buffer, диспетчер использует проверяемую позицию курсора: пустое приглашение `> ` находится в колонке 2. Текст команды всё равно вводится через штатный `WriteConsoleInputW`.
- Срочное исправление вызывает тот же `HookAbortSignal`, что и штатный `Esc`, но без ненадёжной синтетической клавиши. ClawCod также завершает дочерние процессы текущего хода перед приёмом поправки.
- Перезапуск с `-Resume latest` продолжает локальную сессию ClawCod, но не должен использоваться одновременно из двух окон.
- Proxifier для контроллера не требуется. Не включайте профиль, указывающий на неработающий SOCKS `127.0.0.1:8081`.
