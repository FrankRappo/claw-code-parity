use std::future::Future;
use std::pin::Pin;

use crate::error::ApiError;
use crate::types::{MessageRequest, MessageResponse};

pub mod anthropic;
pub mod openai_compat;

#[allow(dead_code)]
pub type ProviderFuture<'a, T> = Pin<Box<dyn Future<Output = Result<T, ApiError>> + Send + 'a>>;

#[allow(dead_code)]
pub trait Provider {
    type Stream;

    fn send_message<'a>(
        &'a self,
        request: &'a MessageRequest,
    ) -> ProviderFuture<'a, MessageResponse>;

    fn stream_message<'a>(
        &'a self,
        request: &'a MessageRequest,
    ) -> ProviderFuture<'a, Self::Stream>;
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ProviderKind {
    Anthropic,
    Xai,
    OpenAi,
    Google,
    Groq,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ProviderMetadata {
    pub provider: ProviderKind,
    pub auth_env: &'static str,
    pub base_url_env: &'static str,
    pub default_base_url: &'static str,
}

const MODEL_REGISTRY: &[(&str, ProviderMetadata)] = &[
    (
        "opus",
        ProviderMetadata {
            provider: ProviderKind::Anthropic,
            auth_env: "ANTHROPIC_API_KEY",
            base_url_env: "ANTHROPIC_BASE_URL",
            default_base_url: anthropic::DEFAULT_BASE_URL,
        },
    ),
    (
        "sonnet",
        ProviderMetadata {
            provider: ProviderKind::Anthropic,
            auth_env: "ANTHROPIC_API_KEY",
            base_url_env: "ANTHROPIC_BASE_URL",
            default_base_url: anthropic::DEFAULT_BASE_URL,
        },
    ),
    (
        "haiku",
        ProviderMetadata {
            provider: ProviderKind::Anthropic,
            auth_env: "ANTHROPIC_API_KEY",
            base_url_env: "ANTHROPIC_BASE_URL",
            default_base_url: anthropic::DEFAULT_BASE_URL,
        },
    ),
    (
        "grok",
        ProviderMetadata {
            provider: ProviderKind::Xai,
            auth_env: "XAI_API_KEY",
            base_url_env: "XAI_BASE_URL",
            default_base_url: openai_compat::DEFAULT_XAI_BASE_URL,
        },
    ),
    (
        "grok-3",
        ProviderMetadata {
            provider: ProviderKind::Xai,
            auth_env: "XAI_API_KEY",
            base_url_env: "XAI_BASE_URL",
            default_base_url: openai_compat::DEFAULT_XAI_BASE_URL,
        },
    ),
    (
        "grok-mini",
        ProviderMetadata {
            provider: ProviderKind::Xai,
            auth_env: "XAI_API_KEY",
            base_url_env: "XAI_BASE_URL",
            default_base_url: openai_compat::DEFAULT_XAI_BASE_URL,
        },
    ),
    (
        "grok-3-mini",
        ProviderMetadata {
            provider: ProviderKind::Xai,
            auth_env: "XAI_API_KEY",
            base_url_env: "XAI_BASE_URL",
            default_base_url: openai_compat::DEFAULT_XAI_BASE_URL,
        },
    ),
    (
        "grok-2",
        ProviderMetadata {
            provider: ProviderKind::Xai,
            auth_env: "XAI_API_KEY",
            base_url_env: "XAI_BASE_URL",
            default_base_url: openai_compat::DEFAULT_XAI_BASE_URL,
        },
    ),
    // Gemma 3 — NO function calling via Google AI Studio API
    (
        "gemma-3-27b-it",
        ProviderMetadata {
            provider: ProviderKind::Google,
            auth_env: "GOOGLE_API_KEY",
            base_url_env: "GOOGLE_BASE_URL",
            default_base_url: openai_compat::DEFAULT_GOOGLE_BASE_URL,
        },
    ),
    (
        "gemma-3-12b-it",
        ProviderMetadata {
            provider: ProviderKind::Google,
            auth_env: "GOOGLE_API_KEY",
            base_url_env: "GOOGLE_BASE_URL",
            default_base_url: openai_compat::DEFAULT_GOOGLE_BASE_URL,
        },
    ),
    // Gemma 4 — supports function calling, 256K context
    (
        "gemma",
        ProviderMetadata {
            provider: ProviderKind::Google,
            auth_env: "GOOGLE_API_KEY",
            base_url_env: "GOOGLE_BASE_URL",
            default_base_url: openai_compat::DEFAULT_GOOGLE_BASE_URL,
        },
    ),
    (
        "gemma4",
        ProviderMetadata {
            provider: ProviderKind::Google,
            auth_env: "GOOGLE_API_KEY",
            base_url_env: "GOOGLE_BASE_URL",
            default_base_url: openai_compat::DEFAULT_GOOGLE_BASE_URL,
        },
    ),
    (
        "gemma-4-31b-it",
        ProviderMetadata {
            provider: ProviderKind::Google,
            auth_env: "GOOGLE_API_KEY",
            base_url_env: "GOOGLE_BASE_URL",
            default_base_url: openai_compat::DEFAULT_GOOGLE_BASE_URL,
        },
    ),
    // gemma-4-27b-it is the alias used by users; real model ID is gemma-4-26b-a4b-it (MoE)
    (
        "gemma-4-27b-it",
        ProviderMetadata {
            provider: ProviderKind::Google,
            auth_env: "GOOGLE_API_KEY",
            base_url_env: "GOOGLE_BASE_URL",
            default_base_url: openai_compat::DEFAULT_GOOGLE_BASE_URL,
        },
    ),
    (
        "gemma-4-26b-a4b-it",
        ProviderMetadata {
            provider: ProviderKind::Google,
            auth_env: "GOOGLE_API_KEY",
            base_url_env: "GOOGLE_BASE_URL",
            default_base_url: openai_compat::DEFAULT_GOOGLE_BASE_URL,
        },
    ),
    // Gemini models — support tool calling
    (
        "gemini",
        ProviderMetadata {
            provider: ProviderKind::Google,
            auth_env: "GOOGLE_API_KEY",
            base_url_env: "GOOGLE_BASE_URL",
            default_base_url: openai_compat::DEFAULT_GOOGLE_BASE_URL,
        },
    ),
    (
        "gemini-2.0-flash",
        ProviderMetadata {
            provider: ProviderKind::Google,
            auth_env: "GOOGLE_API_KEY",
            base_url_env: "GOOGLE_BASE_URL",
            default_base_url: openai_compat::DEFAULT_GOOGLE_BASE_URL,
        },
    ),
    (
        "gemini-2.5-flash",
        ProviderMetadata {
            provider: ProviderKind::Google,
            auth_env: "GOOGLE_API_KEY",
            base_url_env: "GOOGLE_BASE_URL",
            default_base_url: openai_compat::DEFAULT_GOOGLE_BASE_URL,
        },
    ),
    (
        "gemini-2.5-flash-lite",
        ProviderMetadata {
            provider: ProviderKind::Google,
            auth_env: "GOOGLE_API_KEY",
            base_url_env: "GOOGLE_BASE_URL",
            default_base_url: openai_compat::DEFAULT_GOOGLE_BASE_URL,
        },
    ),
    (
        "gemini-2.5-pro",
        ProviderMetadata {
            provider: ProviderKind::Google,
            auth_env: "GOOGLE_API_KEY",
            base_url_env: "GOOGLE_BASE_URL",
            default_base_url: openai_compat::DEFAULT_GOOGLE_BASE_URL,
        },
    ),
    // Groq models — note: free tier TPM limits make most models unusable with
    // claw's large system prompt (~21K tokens). Only llama4-scout has sufficient TPM,
    // but tool calling (agent mode) returns empty content on free tier.
    // llama4-scout: 30K RPD, high TPM — CHAT ONLY (tools not working on free tier)
    (
        "llama4-scout",
        ProviderMetadata {
            provider: ProviderKind::Groq,
            auth_env: "GROQ_API_KEY",
            base_url_env: "GROQ_BASE_URL",
            default_base_url: openai_compat::DEFAULT_GROQ_BASE_URL,
        },
    ),
    // Gemma 4 — supports function calling
    (
        "gemma4",
        ProviderMetadata {
            provider: ProviderKind::Google,
            auth_env: "GOOGLE_API_KEY",
            base_url_env: "GOOGLE_BASE_URL",
            default_base_url: openai_compat::DEFAULT_GOOGLE_BASE_URL,
        },
    ),
    (
        "gemma-4-31b-it",
        ProviderMetadata {
            provider: ProviderKind::Google,
            auth_env: "GOOGLE_API_KEY",
            base_url_env: "GOOGLE_BASE_URL",
            default_base_url: openai_compat::DEFAULT_GOOGLE_BASE_URL,
        },
    ),
];

#[must_use]
pub fn resolve_model_alias(model: &str) -> String {
    let trimmed = model.trim();
    let lower = trimmed.to_ascii_lowercase();
    MODEL_REGISTRY
        .iter()
        .find_map(|(alias, metadata)| {
            (*alias == lower).then_some(match metadata.provider {
                ProviderKind::Anthropic => match *alias {
                    "opus" => "claude-opus-4-6",
                    "sonnet" => "claude-sonnet-4-6",
                    "haiku" => "claude-haiku-4-5-20251213",
                    _ => trimmed,
                },
                ProviderKind::Xai => match *alias {
                    "grok" | "grok-3" => "grok-3",
                    "grok-mini" | "grok-3-mini" => "grok-3-mini",
                    "grok-2" => "grok-2",
                    _ => trimmed,
                },
                ProviderKind::OpenAi => trimmed,
                ProviderKind::Google => match *alias {
                    // gemma (no version) → best Gemma 4 = 31B
                    "gemma" | "gemma4" => "gemma-4-31b-it",
                    // user says "27b" → real model is 26B MoE
                    "gemma-4-27b-it" => "gemma-4-26b-a4b-it",
                    "gemini" => "gemini-2.5-flash",
                    _ => trimmed,
                },
                ProviderKind::Groq => match *alias {
                    "llama4-scout" => "meta-llama/llama-4-scout-17b-16e-instruct",
                    _ => trimmed,
                },
            })
        })
        .map_or_else(|| trimmed.to_string(), ToOwned::to_owned)
}

#[must_use]
pub fn metadata_for_model(model: &str) -> Option<ProviderMetadata> {
    let canonical = resolve_model_alias(model);
    if canonical.starts_with("claude") {
        return Some(ProviderMetadata {
            provider: ProviderKind::Anthropic,
            auth_env: "ANTHROPIC_API_KEY",
            base_url_env: "ANTHROPIC_BASE_URL",
            default_base_url: anthropic::DEFAULT_BASE_URL,
        });
    }
    if canonical.starts_with("grok") {
        return Some(ProviderMetadata {
            provider: ProviderKind::Xai,
            auth_env: "XAI_API_KEY",
            base_url_env: "XAI_BASE_URL",
            default_base_url: openai_compat::DEFAULT_XAI_BASE_URL,
        });
    }
    if canonical.starts_with("gemma") || canonical.starts_with("gemini") {
        return Some(ProviderMetadata {
            provider: ProviderKind::Google,
            auth_env: "GOOGLE_API_KEY",
            base_url_env: "GOOGLE_BASE_URL",
            default_base_url: openai_compat::DEFAULT_GOOGLE_BASE_URL,
        });
    }
    if canonical.starts_with("llama")
        || canonical.starts_with("mixtral")
        || canonical.starts_with("deepseek")
        || canonical.starts_with("meta-llama")
        || canonical.starts_with("qwen")
        || canonical.starts_with("kimi")
    {
        return Some(ProviderMetadata {
            provider: ProviderKind::Groq,
            auth_env: "GROQ_API_KEY",
            base_url_env: "GROQ_BASE_URL",
            default_base_url: openai_compat::DEFAULT_GROQ_BASE_URL,
        });
    }
    None
}

#[must_use]
pub fn detect_provider_kind(model: &str) -> ProviderKind {
    if let Some(metadata) = metadata_for_model(model) {
        return metadata.provider;
    }
    if anthropic::has_auth_from_env_or_saved().unwrap_or(false) {
        return ProviderKind::Anthropic;
    }
    if openai_compat::has_api_key("GROQ_API_KEY") {
        return ProviderKind::Groq;
    }
    if openai_compat::has_api_key("GOOGLE_API_KEY") {
        return ProviderKind::Google;
    }
    if openai_compat::has_api_key("OPENAI_API_KEY") {
        return ProviderKind::OpenAi;
    }
    if openai_compat::has_api_key("XAI_API_KEY") {
        return ProviderKind::Xai;
    }
    ProviderKind::Anthropic
}

#[must_use]
pub fn max_tokens_for_model(model: &str) -> u32 {
    let canonical = resolve_model_alias(model);
    // Local Qwen 3.5 deployments support longer completions than Groq's
    // conservative default. Keep the override within the model's configured
    // output budget so a typo cannot request an unbounded completion.
    if canonical.to_ascii_lowercase().starts_with("qwen3.5") {
        return std::env::var("CLAW_QWEN_MAX_OUTPUT_TOKENS")
            .ok()
            .and_then(|value| value.trim().parse::<u32>().ok())
            .filter(|value| (1..=32_000).contains(value))
            .unwrap_or(32_000);
    }
    // Groq models: keep well below free-tier context limits
    if let Some(meta) = metadata_for_model(model) {
        if meta.provider == ProviderKind::Groq {
            return 8_000;
        }
    }
    // Gemma 4 models: outputTokenLimit = 32 768 (from API)
    if canonical.starts_with("gemma-4") {
        // Local Gemma is used as an autonomous tool agent. Keep per-turn output
        // bounded so long-running tasks do not spend minutes draining an unused
        // 32k-token completion budget. Operators can override this explicitly.
        return std::env::var("CLAW_GEMMA_MAX_OUTPUT_TOKENS")
            .ok()
            .and_then(|value| value.parse::<u32>().ok())
            .unwrap_or(4_096);
    }
    if canonical.contains("opus") {
        32_000
    } else {
        64_000
    }
}

#[cfg(test)]
mod tests {
    use std::ffi::OsString;
    use std::sync::Mutex;

    use super::{detect_provider_kind, max_tokens_for_model, resolve_model_alias, ProviderKind};

    static ENV_LOCK: Mutex<()> = Mutex::new(());

    struct EnvGuard {
        key: &'static str,
        original: Option<OsString>,
    }

    impl EnvGuard {
        fn unset(key: &'static str) -> Self {
            let original = std::env::var_os(key);
            std::env::remove_var(key);
            Self { key, original }
        }
    }

    impl Drop for EnvGuard {
        fn drop(&mut self) {
            if let Some(value) = self.original.take() {
                std::env::set_var(self.key, value);
            } else {
                std::env::remove_var(self.key);
            }
        }
    }

    #[test]
    fn resolves_grok_aliases() {
        assert_eq!(resolve_model_alias("grok"), "grok-3");
        assert_eq!(resolve_model_alias("grok-mini"), "grok-3-mini");
        assert_eq!(resolve_model_alias("grok-2"), "grok-2");
    }

    #[test]
    fn detects_provider_from_model_name_first() {
        assert_eq!(detect_provider_kind("grok"), ProviderKind::Xai);
        assert_eq!(
            detect_provider_kind("claude-sonnet-4-6"),
            ProviderKind::Anthropic
        );
    }

    #[test]
    fn keeps_existing_max_token_heuristic() {
        assert_eq!(max_tokens_for_model("opus"), 32_000);
        assert_eq!(max_tokens_for_model("grok-3"), 64_000);
    }

    #[test]
    fn qwen35_uses_bounded_local_max_output_override() {
        let _lock = ENV_LOCK.lock().expect("environment lock must be available");
        let _env = EnvGuard::unset("CLAW_QWEN_MAX_OUTPUT_TOKENS");

        assert_eq!(max_tokens_for_model("qwen-2.5-72b"), 8_000);
        assert_eq!(max_tokens_for_model("qwen3.5-27b-uncensored"), 32_000);

        std::env::set_var("CLAW_QWEN_MAX_OUTPUT_TOKENS", "12000");
        assert_eq!(max_tokens_for_model("qwen3.5-27b-uncensored"), 12_000);

        for invalid in ["", "not-a-number", "0", "32001"] {
            std::env::set_var("CLAW_QWEN_MAX_OUTPUT_TOKENS", invalid);
            assert_eq!(max_tokens_for_model("qwen3.5-27b-uncensored"), 32_000);
        }
    }
}
