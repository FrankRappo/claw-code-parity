# Запуск claw

## 1. Сборка (один раз)

```powershell
cd "C:\claw cod\claw-code-parity — копия (2)\rust"
cargo build --release --bin claw
```

Бинарник появится в: `rust\target\release\claw.exe`

---

## 2. Обязательно перед каждым запуском (Windows)

```powershell
$env:HOME = $env:USERPROFILE
```

---

## 3. Установите API ключ нужного провайдера

```powershell
# Groq (бесплатно) — рекомендуется
$env:GROQ_API_KEY = "gsk_..."

# Google Gemma/Gemini (бесплатно)
$env:GOOGLE_API_KEY = "AIza..."

# Anthropic Claude (платно)
$env:ANTHROPIC_API_KEY = "sk-ant-..."
```

---

## 4. Запуск

### Интерактивный режим (REPL — как Claude Code)

```powershell
cd "C:\claw cod\claw-code-parity — копия (2)\rust"

# Google Gemma 4 31B (бесплатно, агент с инструментами) — РЕКОМЕНДУЕТСЯ
.\target\release\claw.exe --model gemma4

# Google Gemini 2.5 Flash Lite (бесплатно, 1000 req/day)
.\target\release\claw.exe --model gemini-2.5-flash-lite

# Groq llama4-scout (бесплатно, только чат)
.\target\release\claw.exe --model llama4-scout

# Claude Sonnet (платно)
.\target\release\claw.exe --model sonnet
```

### Без запроса подтверждений (авто-режим)

По умолчанию claw спрашивает разрешение перед каждым действием с файлами.
Чтобы работал **без подтверждений** — добавь флаг:

```powershell
# Разрешить всё в рамках текущей директории (рекомендуется)
.\target\release\claw.exe --model gemma4 --dangerously-skip-permissions

# Или установить режим через команду внутри REPL:
/permissions workspace-write   # файлы только в текущем проекте
/permissions danger-full-access # без ограничений (как выше)
```

> ⚠ `--dangerously-skip-permissions` даёт агенту полный доступ — запускай
> только в доверенной директории проекта.

### Одиночный запрос (без REPL)

```powershell
.\target\release\claw.exe --model llama4-scout prompt "исправь баг в main.rs"
```

---

## 5. Команды внутри REPL

| Команда | Что делает |
|---------|------------|
| `/models` | Список всех доступных моделей |
| `/model gemma4` | Переключить модель прямо в сессии |
| `/status` | Статус сессии, контекст, файлы |
| `/diff` | Показать изменения |
| `/commit` | Сгенерировать коммит |
| `/help` | Все команды |
| `/compact` | Сжать историю (если контекст заполнился) |
| `Ctrl+C` | Выход |

---

## 6. Всё одной строкой (копировать и запускать)

```powershell
$env:HOME = $env:USERPROFILE; $env:GROQ_API_KEY = "<your_groq_api_key>"; Set-Location "rust"; .\target\release\claw.exe --model llama4-scout
```

---

## Рекомендуемые модели для старта

| Что нужно | Модель | Провайдер |
|-----------|--------|-----------|
| Агент с инструментами, быстро | `llama4-scout` | Groq (бесплатно) |
| Агент, большой контекст 256K | `gemma4` | Google (бесплатно) |
| Лучшее качество бесплатно | `gemini` | Google (бесплатно) |
| Максимальное качество | `sonnet` | Anthropic (платно) |

> Полный список моделей с лимитами: см. [MODELS.md](MODELS.md)
