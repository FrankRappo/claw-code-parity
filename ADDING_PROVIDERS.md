# Руководство разработчика — добавление провайдеров, моделей и команд

Этот документ описывает архитектуру системы провайдеров в claw и пошаговые
инструкции для добавления нового провайдера, модели или slash-команды.

---

## Архитектура

```
rust/crates/
├── api/src/providers/
│   ├── mod.rs              ← реестр моделей, алиасы, определение провайдера
│   └── openai_compat.rs    ← конфигурация OpenAI-совместимых провайдеров
├── api/src/
│   └── client.rs           ← ProviderClient enum — точка входа для всех API
├── commands/src/lib.rs     ← SlashCommand enum, парсинг, is_repl_command
└── rusty-claude-cli/src/
    ├── main.rs             ← обработчик команд в REPL, format_models_list
    └── app.rs              ← CliApp slash-command dispatch, handle_models
```

### Поток вызова

```
пользователь вводит модель
    → resolve_model_alias()         [api/src/providers/mod.rs]
    → detect_provider_kind()        [api/src/providers/mod.rs]
    → ProviderClient::from_model()  [api/src/client.rs]
    → OpenAiCompatClient::new()     [api/src/providers/openai_compat.rs]
    → HTTP запрос к API
```

---

## Часть 1: Добавление нового провайдера

### Шаг 1 — `openai_compat.rs`

Файл: `rust/crates/api/src/providers/openai_compat.rs`

Добавьте константу URL и массив переменных окружения:

```rust
pub const DEFAULT_MYPROVIDER_BASE_URL: &str = "https://api.myprovider.com/v1/";

const MYPROVIDER_ENV_VARS: &[&str] = &["MYPROVIDER_API_KEY"];
```

Добавьте конструктор в `impl OpenAiCompatConfig`:

```rust
pub fn myprovider(model: &str, api_key: &str) -> Self {
    Self {
        base_url: std::env::var("MYPROVIDER_BASE_URL")
            .unwrap_or_else(|_| DEFAULT_MYPROVIDER_BASE_URL.to_string()),
        api_key: api_key.to_string(),
        model: model.to_string(),
    }
}
```

Добавьте ветку в `credential_env_vars`:

```rust
pub fn credential_env_vars(provider: ProviderKind) -> &'static [&'static str] {
    match provider {
        // ...существующие...
        ProviderKind::MyProvider => MYPROVIDER_ENV_VARS,
    }
}
```

### Шаг 2 — `providers/mod.rs`

Файл: `rust/crates/api/src/providers/mod.rs`

**2a. Добавьте вариант в enum:**

```rust
pub enum ProviderKind {
    Anthropic,
    OpenAi,
    XAi,
    Google,
    Groq,
    MyProvider,  // ← добавьте сюда
}
```

**2b. Добавьте модели в `MODEL_REGISTRY`:**

```rust
(
    "my-model-alias",
    ProviderMetadata {
        provider: ProviderKind::MyProvider,
        auth_env: "MYPROVIDER_API_KEY",
        base_url_env: "MYPROVIDER_BASE_URL",
        default_base_url: openai_compat::DEFAULT_MYPROVIDER_BASE_URL,
    },
),
```

**2c. Добавьте алиасы в `resolve_model_alias`:**

```rust
ProviderKind::MyProvider => match *alias {
    "mymodel" | "mymodel-fast" => "my-model-v1-real-id",
    _ => trimmed,
},
```

**2d. Добавьте определение провайдера в `detect_provider_kind`:**

```rust
// По началу имени модели:
if canonical.starts_with("my-model") {
    return ProviderMetadata {
        provider: ProviderKind::MyProvider,
        auth_env: "MYPROVIDER_API_KEY",
        base_url_env: "MYPROVIDER_BASE_URL",
        default_base_url: openai_compat::DEFAULT_MYPROVIDER_BASE_URL,
    };
}
```

**2e. Добавьте лимиты токенов в `max_tokens_for_model`:**

```rust
if let Some(meta) = MODEL_REGISTRY.iter().find(...) {
    if meta.provider == ProviderKind::MyProvider {
        return 8_192; // или реальный лимит провайдера
    }
}
```

### Шаг 3 — `client.rs`

Файл: `rust/crates/api/src/client.rs`

**3a. Добавьте вариант в `ProviderClient`:**

```rust
pub enum ProviderClient {
    Anthropic(AnthropicClient),
    OpenAiCompat(OpenAiCompatClient),
    Google(OpenAiCompatClient),
    Groq(OpenAiCompatClient),
    MyProvider(OpenAiCompatClient),  // ← добавьте
}
```

**3b. Обновите `from_model_with_anthropic_auth` — ветку создания клиента:**

```rust
ProviderKind::MyProvider => {
    let api_key = std::env::var("MYPROVIDER_API_KEY")
        .map_err(|_| "MYPROVIDER_API_KEY not set")?;
    let config = openai_compat::OpenAiCompatConfig::myprovider(&resolved, &api_key);
    ProviderClient::MyProvider(OpenAiCompatClient::new(config))
}
```

**3c. Добавьте `MyProvider` во все match arms:**

В методах `provider_kind`, `with_prompt_cache`, `prompt_cache_stats`,
`take_last_prompt_cache_record`, `send_message`, `stream_message` —
добавьте ветку аналогично `Groq` или `Google`.

### Шаг 4 — Обновите `/models`

В двух местах:

1. `rust/crates/rusty-claude-cli/src/main.rs` — функция `format_models_list`
2. `rust/crates/rusty-claude-cli/src/app.rs` — метод `handle_models`

### Шаг 5 — Пересоберите

```powershell
cd rust
cargo build --release --bin claw
```

---

## Часть 2: Добавление модели к существующему провайдеру

Нужны только шаги из `providers/mod.rs`:

1. Добавьте запись в `MODEL_REGISTRY` (Шаг 2b выше)
2. Добавьте алиас в `resolve_model_alias` (Шаг 2c), если нужно
3. Обновите список в `format_models_list` и `handle_models`

Пример — добавить `gemma-4-9b-it`:

```rust
// В MODEL_REGISTRY:
(
    "gemma-4-9b",
    ProviderMetadata {
        provider: ProviderKind::Google,
        auth_env: "GOOGLE_API_KEY",
        base_url_env: "GOOGLE_BASE_URL",
        default_base_url: openai_compat::DEFAULT_GOOGLE_BASE_URL,
    },
),

// В resolve_model_alias (ветка Google):
"gemma-4-9b" => "gemma-4-9b-it",
```

---

## Часть 3: Добавление slash-команды

### Шаг 1 — Добавьте вариант в enum

Файл: `rust/crates/commands/src/lib.rs`

```rust
pub enum SlashCommand {
    // ...существующие...
    MyCommand {
        arg: Option<String>,  // или без полей если не нужны
    },
}
```

### Шаг 2 — Добавьте парсинг

В функции `parse_slash_command` (там же в lib.rs):

```rust
"my-command" => SlashCommand::MyCommand {
    arg: optional_single_arg(command, &args, "[arg]")?,
},
// или для команды без аргументов:
"my-command" => {
    validate_no_args(command, &args)?;
    SlashCommand::MyCommand
}
```

### Шаг 3 — Добавьте в `is_repl_command` / catch-all match

В той же функции где есть большой match с `| SlashCommand::Model { .. }` —
добавьте `| SlashCommand::MyCommand { .. }` в нужную ветку:

- **Для REPL-команды** (выполняется в интерактивном режиме): в ветку
  `SlashCommand::Status | SlashCommand::Model ...`
- **Для команды без обработчика** (return None): в последнюю ветку

### Шаг 4 — Добавьте обработчик в main.rs

Файл: `rust/crates/rusty-claude-cli/src/main.rs`

В большом match по `SlashCommand` (поиск по `SlashCommand::Model { model } =>`):

```rust
SlashCommand::MyCommand { arg } => {
    self.my_command_handler(arg)?;
    false
}
```

### Шаг 5 — Если команда также нужна в app.rs (lightweight REPL)

Файл: `rust/crates/rusty-claude-cli/src/app.rs`

```rust
// В SLASH_COMMAND_HANDLERS:
SlashCommandHandler {
    command: SlashCommand::MyCommand { arg: None },
    summary: "Описание команды",
},

// В dispatch match:
SlashCommand::MyCommand { arg } => self.handle_my_command(arg.as_deref(), out),

// В handle_help name match:
SlashCommand::MyCommand { .. } => "/my-command [arg]",

// Реализация:
fn handle_my_command(&mut self, arg: Option<&str>, out: &mut impl Write)
    -> io::Result<CommandResult>
{
    writeln!(out, "my command: {:?}", arg)?;
    Ok(CommandResult::Continue)
}
```

---

## Часть 4: Известные ограничения и решения

### Проблема: Groq TPM лимиты

Claw отправляет большой системный промпт (~21K токенов). Модели с TPM < 25K
на бесплатном уровне Groq будут падать с `413 Payload Too Large`.

**Решение:** использовать только модели с высоким TPM (`llama-3.1-8b-instant`
131K TPM, `llama4-scout` высокий TPM).

### Проблема: Google Gemma 3 — нет function calling

`gemma-3-*` модели возвращают ошибку `Function calling is not enabled for
models/gemma-3-27b-it`. Это ограничение Google AI Studio API, не баги claw.

**Решение:** использовать Gemma 4 (`gemma-4-31b-it`, `gemma-4-26b-a4b`).

### Проблема: HOME не установлен на Windows

Claw падает с `HOME is not set` при запуске на Windows.

**Решение:**
```powershell
$env:HOME = $env:USERPROFILE
```

### Проблема: Две функции `resolve_model_alias`

В `main.rs` была локальная функция `resolve_model_alias` которая затеняла
`api::resolve_model_alias` и знала только 3 алиаса Claude. Исправлено —
теперь делегирует в `api::`.

Аналогично для `max_tokens_for_model` — теперь делегирует в `api::max_tokens_for_model()`.

**Правило:** любая логика связанная с моделями — только в `api/src/providers/mod.rs`.
В `main.rs` только делегирование.

---

## История изменений

| Дата | Что добавлено |
|------|---------------|
| 2025 | Google AI Studio провайдер (`GOOGLE_API_KEY`) |
| 2025 | Groq провайдер (`GROQ_API_KEY`) |
| 2025 | Gemma 4 модели с tool calling |
| 2025 | Gemini 2.5 Flash / Flash-Lite / Pro |
| 2025 | Groq: llama4-scout, llama-3.1-8b, qwen-qwq-32b, deepseek-r1 |
| 2025 | Slash-команда `/models` — список всех доступных моделей |
| 2025 | Исправлен `max_tokens` для Groq (8K вместо 64K) |
| 2025 | Удалены: llama-3.3-70b, mixtral (низкий TPM), deepseek-llama-70b (снят) |
