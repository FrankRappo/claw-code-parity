# Модели claw — справочник

## Быстрый старт

```bash
# Google Gemma 4 (бесплатно, АГЕНТ с инструментами ✓)
$env:GOOGLE_API_KEY = "AIza..."
.\claw.exe --model gemma4

# Google Gemini Flash Lite (бесплатно, АГЕНТ с инструментами ✓)
$env:GOOGLE_API_KEY = "AIza..."
.\claw.exe --model gemini-2.5-flash-lite

# Groq llama4-scout (бесплатно, только чат — без агента)
$env:GROQ_API_KEY = "gsk_..."
.\claw.exe --model llama4-scout

# Посмотреть все модели в сессии
/models

# Переключить модель в сессии
/model gemma4
```

---

## Anthropic (ANTHROPIC_API_KEY)

Платные модели. Лучшее качество, полная поддержка инструментов.

| Алиас | Реальный ID | Контекст | max_tokens | Примечание |
|-------|-------------|----------|------------|------------|
| `opus` | `claude-opus-4-6` | 200K | 32 000 | Умнейший |
| `sonnet` | `claude-sonnet-4-6` | 200K | 64 000 | Баланс цена/качество |
| `haiku` | `claude-haiku-4-5-20251213` | 200K | 64 000 | Самый быстрый/дешёвый |

---

## Google AI Studio (GOOGLE_API_KEY)

Бесплатный ключ: [aistudio.google.com](https://aistudio.google.com)

### Gemma 4 — поддерживает tool calling ✓ (АГЕНТ РАБОТАЕТ)

| Алиас | Реальный ID | Контекст (вход) | max_tokens (выход) | Лимиты free tier | Агент |
|-------|-------------|-----------------|---------------------|------------------|-------|
| `gemma`, `gemma4` | `gemma-4-31b-it` | **256K** (262 144) | 32K (32 768) | не опубликованы | ✅ |
| `gemma-4-27b-it` | `gemma-4-26b-a4b-it` | **256K** (262 144) | 32K (32 768) | не опубликованы | ✅ |

> Лимиты RPD/RPM для Gemma Google официально не публикует.
> Оба имеют thinking-режим (`"thinking": true` — включается при необходимости).
> При превышении квоты — получишь 429 ошибку.

### Gemini — поддерживает tool calling ✓ (АГЕНТ РАБОТАЕТ)

| Алиас | Реальный ID | Контекст (вход) | max_tokens (выход) | Лимиты free tier | Агент |
|-------|-------------|-----------------|--------------------|------------------|-------|
| `gemini` | `gemini-2.5-flash` | **1M** (1 048 576) | 64K (65 536) | **250 RPD**, 10 RPM, 250K TPM | ✅ |
| *(нет)* | `gemini-2.5-flash-lite` | **1M** (1 048 576) | 64K (65 536) | **1 000 RPD**, 15 RPM, 250K TPM | ✅ |
| *(нет)* | `gemini-2.5-pro` | **1M** (1 048 576) | — | **100 RPD**, 5 RPM, 125K TPM | ✅ |

> **Рекомендация:** для агентской работы — `gemini-2.5-flash-lite` (1000 RPD — максимум бесплатно).
>
> ⚠ Если видишь "no content" для `gemini` — превышена дневная квота (250/день).
> Переключись на `gemini-2.5-flash-lite`.
>
> ℹ Лимиты получены из официальной документации (декабрь 2025). Google может тихо менять их.

---

## Groq (GROQ_API_KEY)

Бесплатный ключ: [console.groq.com](https://console.groq.com)

⚠ **Важно:** Большинство моделей Groq на бесплатном уровне имеют TPM < 22K, но
системный промпт claw занимает ~21K токенов. Поэтому только `llama4-scout` работает.
Агентский режим (tool calling) на Groq free tier **не работает** — модель возвращает
пустой контент при попытке вызвать инструменты.

| Алиас | Реальный ID | TPM | RPD | max_tokens | Агент |
|-------|-------------|-----|-----|------------|-------|
| `llama4-scout` | `meta-llama/llama-4-scout-17b-16e-instruct` | высокий | 30 000 | 8 192 | ❌ чат only |

> **Groq подходит только для быстрых текстовых разговоров** (не для agentic coding).
> Для агентской работы используй Google (gemma4 или gemini-2.5-flash-lite).

---

## xAI (XAI_API_KEY)

| Алиас | Реальный ID | Примечание |
|-------|-------------|------------|
| `grok` | `grok-3` | Платный |
| `grok-mini` | `grok-3-mini` | Платный |

---

## Удалённые модели

| Модель | Причина удаления |
|--------|-----------------|
| `llama-3.3-70b-versatile` (Groq) | TPM = **12 000** на free tier. Системный промпт claw ~21K токенов → всегда 413 ошибка |
| `mixtral-8x7b-32768` (Groq) | Аналогично — низкий TPM |
| `llama-3.1-8b-instant` (Groq) | TPM = **6 000** на free tier — тоже недостаточно |
| `qwen/qwen3-32b` (Groq) | TPM = **6 000** — тоже недостаточно |
| `deepseek-r1-distill-qwen-32b` (Groq) | API возвращает "decommissioned" |
| `deepseek-r1-distill-llama-70b` (Groq) | Снята с производства Groq |
| `gemini-2.0-flash` | Бесплатная квота исчерпана на тестовом ключе |
| `gemma-3-27b-it` | Не поддерживает function calling |

---

## Переменные окружения

```powershell
# Windows PowerShell
$env:HOME = $env:USERPROFILE   # ОБЯЗАТЕЛЬНО на Windows
$env:ANTHROPIC_API_KEY = "sk-ant-..."
$env:GOOGLE_API_KEY    = "AIza..."
$env:GROQ_API_KEY      = "gsk_..."
$env:XAI_API_KEY       = "xai-..."
```
