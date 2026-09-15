# Claw Cod + Kimi: локальный запуск вручную

Это короткая инструкция для повседневной работы на текущем Windows-компьютере. Полное описание находится в [`KIMI_CLAW_RUN_AND_TEST_RU.md`](KIMI_CLAW_RUN_AND_TEST_RU.md), управление автономным агентом — в [`docs/WINDOWS_AGENT_CONTROL_RU.md`](docs/WINDOWS_AGENT_CONTROL_RU.md).

## 1. Пути и подготовка PowerShell

```powershell
$repo = 'C:\claw cod\claw-code-parity — копия (2)'
$gateway = Join-Path $repo 'integrations\kimi-claw-gateway'
Set-Location $repo
Set-ExecutionPolicy -Scope Process Bypass -Force
```

Все секреты хранятся только в `%LOCALAPPDATA%\KimiClawGateway`. Не копируй `session.dpapi`, `api-key.txt`, cookies или токены в Git.

## 2. Первая установка

```powershell
& "$gateway\windows\install.ps1"
```

Если `%LOCALAPPDATA%\KimiClawGateway\session.dpapi` уже существует и сессия работает, повторный импорт не нужен.

Для нового входа или истёкшей сессии:

```powershell
& "$gateway\windows\import-edge-session.ps1" -ProfileMode Dedicated
```

Откроется отдельный профиль Edge для импорта. Войди в Kimi и дождись завершения скрипта. Обычный профиль Edge не закрывай и не используй `-ProfileMode Existing`, пока обычный Edge запущен.

## 3. Обычный ручной запуск

Запусти gateway:

```powershell
& "$gateway\windows\start-kimi-gateway.ps1" -Port 18082
Invoke-RestMethod 'http://127.0.0.1:18082/health'
```

Ожидается `status: ok`, `session_protection: dpapi`, `persistent_chat: true`.

Затем из каталога проекта запусти Claw:

```powershell
Set-Location $repo
.\claw-kimi.cmd
```

Основные команды внутри Claw:

```text
/status
/session list
/resume latest
/agent on
/agent off
/compact
/exit
```

- `/agent on` требует хотя бы один реальный tool call в каждом пользовательском ходе.
- `/agent off` снимает это требование, но не отключает доступные инструменты.
- При `/resume` восстанавливаются JSONL-сессия и сохранённое точное имя модели, поэтому persistent Kimi-чат не меняется случайно.

## 4. Автономный агент, которым управляет оркестратор

Запуск отдельного управляемого окна с продолжением последней сессии:

```powershell
Set-Location $repo
.\windows\start-claw-agent.ps1 `
  -Name kimi `
  -LauncherPath .\claw-kimi.cmd `
  -WorkspacePath 'C:\claw cod' `
  -Resume latest `
  -AgentMode on
```

Передать задачу или поправку в то же окно и ту же сессию:

```powershell
.\windows\send-claw-agent.ps1 `
  -Name kimi `
  -WorkspacePath 'C:\claw cod' `
  -Message 'Продолжай текущую задачу; локализуй конкретную проблему, исправь её, запусти тесты и задокументируй результат.'
```

Сообщение можно писать многострочным, но контроллер сохранит его в inbox и введёт в окно одну физическую строку. Для обычных задач не используй `-Raw`; этот режим предназначен для коротких служебных команд вроде `/status`.

Проверить состояние:

```powershell
.\windows\get-claw-agent-status.ps1 -Name kimi -WorkspacePath 'C:\claw cod'
```

Остановить только текущий ход, сохранив окно и сессию:

```powershell
.\windows\interrupt-claw-agent.ps1 -Name kimi -WorkspacePath 'C:\claw cod'
```

Штатно закрыть управляемое окно:

```powershell
.\windows\stop-claw-agent.ps1 -Name kimi -WorkspacePath 'C:\claw cod'
```

## 5. Смена Kimi API без пересборки

Создай локальный конфиг, который уже исключён из Git:

```powershell
Set-Location $repo
Copy-Item .\claw-kimi.local.cmd.example .\claw-kimi.local.cmd -Force
notepad .\claw-kimi.local.cmd
```

Для локального web-gateway оставь:

```bat
set "KIMI_PROVIDER_MODE=gateway"
```

Для официального Kimi Coding API запиши новый ключ в отдельный файл, не помещая его в командную строку или Git:

```powershell
$state = Join-Path $env:LOCALAPPDATA 'KimiClawGateway'
New-Item -ItemType Directory -Force -Path $state | Out-Null
$secret = Read-Host 'Вставь Kimi API key' -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
try {
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    [IO.File]::WriteAllText((Join-Path $state 'direct-api-key.txt'), $plain.Trim())
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    Remove-Variable plain, secret -ErrorAction SilentlyContinue
}
& "$gateway\windows\set-private-acl.ps1" -StateDir $state
```

Раскомментируй в `claw-kimi.local.cmd`:

```bat
set "KIMI_PROVIDER_MODE=direct"
set "KIMI_DIRECT_BASE_URL=https://api.kimi.com/coding/v1"
set "KIMI_DIRECT_MODEL=kimi-for-coding"
set "KIMI_DIRECT_API_KEY_FILE=%LOCALAPPDATA%\KimiClawGateway\direct-api-key.txt"
```

После этого снова запускай `.\claw-kimi.cmd`. Пересборка не нужна. Для возврата укажи `KIMI_PROVIDER_MODE=gateway`.

## 6. Проверки

Gateway:

```powershell
Invoke-RestMethod 'http://127.0.0.1:18082/health'
Get-Content "$env:LOCALAPPDATA\KimiClawGateway\gateway.18082.stderr.log" -Tail 80
```

Unit-тесты gateway:

```powershell
Set-Location $gateway
python -m unittest discover -s tests -v
```

Тест контроллера без расхода API:

```powershell
Set-Location $repo
.\windows\agent-control\tests\test-agent-control.ps1
```

Rust-проверки:

```powershell
Set-Location (Join-Path $repo 'rust')
cargo check -p rusty-claude-cli --bin claw
cargo test -p rusty-claude-cli --bin claw
cargo build -p rusty-claude-cli --bin claw --release
```

## 7. Лимиты продолжительного чата

По умолчанию safety-пороги одного upstream-чата — `1000` накопительных сообщений или `200000` накопительных токенов. Обычный Claw compaction не обнуляет эти upstream-счётчики. При достижении порога gateway создаёт новую upstream-generation, но сохраняет локальную сессию, точную модель и передаёт текущий summary с сохранённым хвостом.

## 8. Остановка gateway

```powershell
& "$gateway\windows\stop-kimi-gateway.ps1" -Port 18082
```

Если `/health` отвечает, а модель сообщает об истёкшей сессии, повтори только импорт из раздела 2 и снова запусти gateway. Не удаляй JSONL-сессию Claw: она нужна для продолжения контекста.
