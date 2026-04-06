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

# Groq llama4-scout (бесплатно, рекомендуется)
.\target\release\claw.exe --model llama4-scout

# Google Gemma 4 31B (бесплатно, с инструментами)
.\target\release\claw.exe --model gemma4

# Google Gemini 2.5 Flash (бесплатно)
.\target\release\claw.exe --model gemini

# Claude Sonnet (платно)
.\target\release\claw.exe --model sonnet
```

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
