# claw — дальнейшие планы

## 🔑 Key Rotation (ротация API ключей)

**Задача:** Автоматически переключаться на следующий ключ при исчерпании квоты (429).

**Как сделать:**
- Добавить поддержку `GOOGLE_API_KEY_1`, `GOOGLE_API_KEY_2`, ... в `openai_compat.rs`
- При ошибке 429 — взять следующий ключ из списка и повторить запрос
- Работает для любого провайдера (Google, Groq, Anthropic)

**Файлы:** `rust/crates/api/src/providers/openai_compat.rs`

---

## 🤖 Groq — починить tool calling

**Задача:** llama4-scout возвращает пустой контент при попытке вызвать инструменты на free tier.

**Что исследовать:**
- Проверить raw SSE ответ от Groq при tool call запросе
- Возможно модель возвращает XML `<tool_call>` вместо OpenAI function calling формата
- Или free tier намеренно блокирует tool use

**Файлы:** `rust/crates/api/src/providers/openai_compat.rs` → `ingest_chunk()`

---

## 🆕 Новые провайдеры

### OpenRouter
- Агрегатор — один ключ даёт доступ к сотням моделей
- Бесплатные модели есть (`/free` суффикс)
- OpenAI-совместимый API → добавить легко

### Qwen (прямой API)
- api.qwen.ai — отдельный ключ, не через Groq
- Более высокие лимиты чем Groq free tier

### Mistral
- mistral.ai — есть бесплатный tier
- OpenAI-совместимый API

---

## 📊 Улучшения /models команды

- Показывать текущий статус квоты (сколько осталось запросов)
- Цветовая индикация: зелёный = OK, жёлтый = мало, красный = исчерпан
- Показывать ping/latency каждого провайдера

---

## 💾 Сохранение API ключей

**Задача:** Сейчас ключи надо вводить каждый раз через `$env:`. Сделать `/apikey set <provider> <key>` — сохраняет в зашифрованный локальный файл.

**Команды:**
```
/apikey set google AIza...
/apikey set groq gsk_...
/apikey list
/apikey remove google
```

---

## 🌐 Gemma 4 4B (когда появится в API)

Сейчас `gemma-4-4b-it` возвращает 404. Когда Google добавит её — добавить в реестр.
Полезна для быстрых задач с меньшим расходом квоты.

---

## 📝 Заметки

- Все изменения опубликованы на: https://github.com/FrankRappo/claw-code-parity
- Рабочие бесплатные агенты сейчас: `gemma4`, `gemini-2.5-flash-lite`
- Groq — только чат (`llama4-scout`), tool calling не работает на free tier
