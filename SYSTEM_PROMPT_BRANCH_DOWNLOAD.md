# Download the system-prompt injection build

This fork branch contains the Claw change that lets local OpenAI-compatible deployments inject a configurable system prompt from a file or environment variable.

## GitHub location

```text
fork:   FrankRappo/claw-code-parity
branch: system-prompt-injection-20260606
commit: 9ae9426 Allow local deployments to inject system prompts
```

Clone this exact branch:

```bash
git clone -b system-prompt-injection-20260606 git@github.com:FrankRappo/claw-code-parity.git
```

Or fetch it into an existing clone:

```bash
git fetch origin system-prompt-injection-20260606
git checkout system-prompt-injection-20260606
```

HTTPS clone also works if your GitHub credentials are configured:

```bash
git clone -b system-prompt-injection-20260606 https://github.com/FrankRappo/claw-code-parity.git
```

## Local Windows/WSL working copy

The already patched local working copy is here:

```text
C:\claw cod\claw-code-parity-system-prompt-work-20260606
\\wsl.localhost\Ubuntu-24.04\mnt\c\claw cod\claw-code-parity-system-prompt-work-20260606
```

The original backup copy was left untouched:

```text
C:\claw cod\claw-code-parity — копия (2)
```

## Build

From the patched branch:

```bash
cd rust
cargo build --release -p rusty-claude-cli --bin claw
```

The Linux release binary appears at:

```text
rust/target/release/claw
```

## Runtime prompt configuration

Set the prompt file before launching Claw:

```bash
export CLAW_SYSTEM_PROMPT_FILE=/home/ubuntu/second-llm/prompts/system-prompt-gemma4-abliterated-current.txt
```

The provider will prepend that file as the first OpenAI-compatible `role=system` message. To change the prompt later, edit the file; no rebuild is needed.
