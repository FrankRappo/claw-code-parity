# Инструкция для Copilot: подключить Gemma 4 к claw.exe

## Контекст
- Скомпилированный бинарник: `rust/target/release/claw.exe`
- Rust crate для API: `rust/crates/api/` — уже поддерживает OpenAI-совместимый клиент
- Google AI Studio даёт OpenAI-совместимый endpoint → **перекомпиляция не нужна**
- Google API ключ: задаётся через `$env:GOOGLE_API_KEY`

---

## Задача для Copilot

### Шаг 1: Добавить Gemma/Google в провайдер-реестр Rust

Файл: `rust/crates/api/src/providers/mod.rs`

Нужно:
1. Добавить `ProviderKind::Google` (или использовать `OpenAi` с другим конфигом)
2. Добавить `OpenAiCompatConfig::google()` в `rust/crates/api/src/providers/openai_compat.rs`:
   ```rust
   pub const DEFAULT_GOOGLE_BASE_URL: &str = "https://generativelanguage.googleapis.com/v1beta/openai/";
   
   pub const fn google() -> Self {
       Self {
           provider_name: "Google",
           api_key_env: "GOOGLE_API_KEY",
           base_url_env: "GOOGLE_BASE_URL",
           default_base_url: DEFAULT_GOOGLE_BASE_URL,
       }
   }
   ```
3. Добавить определение Gemma-моделей в `MODEL_REGISTRY` и `detect_provider_kind` / `metadata_for_model`:
   - Префикс `gemma-` → Google provider
   - `GOOGLE_API_KEY` → используется для аутентификации
4. В `client.rs` добавить `ProviderClient::Google(OpenAiCompatClient)` вариант и направить gemma-модели туда

### Шаг 2: Создать `.env` файл в `rust/`

```
GOOGLE_API_KEY=<your_google_api_key>
GOOGLE_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
```

### Шаг 3: Пересобрать

```
cd rust
cargo build --release --bin claw
```

### Шаг 4: Протестировать

```
cd rust
set GOOGLE_API_KEY=<your_google_api_key>
target\release\claw.exe --model gemma-4-27b-it prompt "привет! кто ты?"
```

Если `gemma-4-27b-it` не найдена, попробовать: `gemma-3-27b-it`

### Шаг 5: Запустить интерактивно

```
target\release\claw.exe --model gemma-4-27b-it
```

---

## Важные файлы

| Файл | Роль |
|------|------|
| `rust/crates/api/src/providers/mod.rs` | реестр провайдеров и моделей |
| `rust/crates/api/src/providers/openai_compat.rs` | OpenAI-совместимый HTTP клиент |
| `rust/crates/api/src/client.rs` | `ProviderClient` enum — точка входа |
| `rust/crates/rusty-claude-cli/src/args.rs` | CLI аргументы (default model) |
| `rust/target/release/claw.exe` | скомпилированный бинарник |

---

## Быстрый путь (без перекомпиляции)

Если нет Rust toolchain — просто запустить с env:
```cmd
set OPENAI_API_KEY=<your_google_api_key>
set OPENAI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
set ANTHROPIC_API_KEY=
target\release\claw.exe --model gemma-4-27b-it
```
(claw выберет OpenAI-провайдер автоматически когда OPENAI_API_KEY установлен)
