# Imported ClawCod / Gemma runtime documentation

Project:

```text
/work/claster/claw-code-parity-telegram-20260720
```

This directory contains exact project-local copies of the ClawCod, Telegram,
Gemma, Vulkan, GPU, VM120 and context-parallel documentation that previously
existed only at the root of:

```text
/work/claster/analise_storage
```

The original files were preserved. No running service, source code,
configuration, Git index, GPU process or deployment state was changed.

## Integrity

- Imported Markdown documents: **12**
- Exact source/copy matches at creation: **12/12**
- `SOURCE-MANIFEST.tsv` records source path, project copy and SHA-256.
- `SHA256SUMS` verifies every imported Markdown document.

Verify:

```bash
cd /work/claster/claw-code-parity-telegram-20260720/docs/imported-from-analise-storage
sha256sum -c SHA256SUMS
```

## Scope

The archive includes:

- ClawCod/Gemma compatibility;
- AMD Vulkan and two-GPU setup;
- high-resolution vision;
- context-parallel probes;
- infrastructure separation;
- GPU monitoring and context tuning;
- VM120/Claw/Gemma storage setup;
- Telegram vision bot;
- srv2 handoff and storage-analysis plan.

The repository's existing canonical documentation remains in its original
locations, including `README.md`, `GEMMA_SETUP.md`, `MODELS.md`,
`integrations/telegram/` and `rust/`.

At copy time no local process was visible as an active ClawCod/Gemma GPU
consumer, so documents were classified by project responsibility rather than
by a live PID.

## Git rule

These files are owner-requested documentation. They were not staged or
committed. If committed later, use an explicit documentation allowlist; never
use `git add -A`.
