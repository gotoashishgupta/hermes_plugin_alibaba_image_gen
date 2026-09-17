---
name: hermes-image-pipeline
description: Quality-gated image pipeline for the Hermes agent (brand_analysis -> generate_design_concepts -> generate_idea_image -> evaluate_image). Use whenever asked (here or via a task) to generate an image, visual, mockup, cover, poster, ad, UI art or brand/design asset from a description, brand or reference image — accepting only images scoring >= 4.0/5 after up to 2 retries. Hermes credentials decide what is selectable: providers configured in `hermes tools` (including OAuth like openai-codex) and the env keys of enabled image_gen plugins (alibaba, openrouter, fal, openai, ...). Skip when a generic (no-Hermes) image-pipeline skill is the one your harness already loaded.
compatibility: Hermes-agent environment — a hermes-agent checkout (HERMES_AGENT_REPO or ~/.hermes/hermes-agent) with its venv, and at least one credentialed image_gen provider via `hermes tools` or ~/.hermes/.env.
---

# Image Pipeline (Hermes-native)

Loadable as the plugin skill ``hermes-image-pipeline:image-pipeline``; the frontmatter
name matches the install directory so skills.sh-style installers key on one identity.

Same four stages and the same accept gate as the generic skill:
**brand_analysis → generate_design_concepts → generate_idea_image → evaluate_image**;
read `references/pipeline.md` when you start and follow its stage order; read
`references/prompt-guide.md` before writing any prompt. Pass is `overall ≥ 4.0/5`,
max 2 retries per concept, each retry a *changed* prompt — never a silent re-roll.
Scene-possibility checks (divider sides, ground, counts, physics, dial conventions)
and text read-back run **before** scoring; an impossible scene or unrequested
copy is a gate failure regardless of design scores.

What is Hermes-specific is everything below the prompt.

## Provider routing (Hermes first, always explicit)

`scripts/imagegen.py` runs on the Hermes venv python and drives Hermes' own
image_gen registry:

```bash
PY=${HERMES_AGENT_REPO:-$HOME/.hermes/hermes-agent}/venv/bin/python
"$PY" "$(dirname <this SKILL.md>)/scripts/imagegen.py" list   # which providers are credentialed (OK rows)
"$PY" "$(dirname <this SKILL.md>)/scripts/imagegen.py" generate \
  --prompt "<concept prompt>" [--provider <name>] [--model <id>] \
  [--aspect landscape|square|portrait] [--size WxH] [--ref <path>]... \
  --out "<workspace>/attempt-N.png" --json [--fallback]
```

- **Configured first.** Providers that `hermes tools` set up (or that resolve
  through Hermes' credential ladder — auth pool, `~/.hermes/.env`, OpenBao
  injected keys) serve the image; the script stores nothing.
- **Plugin-by-env second.** With no `hermes tools` configuration, enabled
  plugins whose declared env vars are set still work (the payload reports
  `credential_source: "env"`); OAuth providers (openai-codex, nous) need their
  Hermes configuration.
- **Truth in the payload.** `route`/`provider`/`model`/`plan` say who actually
  produced the image — read them into the deliverable every time. A
  config-driven fallback onto FAL (Hermes maps unconfigured or `nous`
  selections there) is always visible, never silent.
- **`--fallback` for unnamed auto-picks**; when the user named a provider,
  surface failures verbatim and ask instead of switching.
- **No-Hermes fallback.** If Hermes is unreachable, this skill exits 2 and
  points at the generic `image-pipeline` skill (built-in adapters work from env
  keys alone) — don't hand-roll curl calls.

The `alibaba` provider here is the unified plugin from this repo
(`hermes plugins install <org>/hermes_plugin_alibaba_image_gen/plugins/image_gen_alibaba --enable`):
Token Plan intl/CN + DashScope PAYG + custom endpoints behind one name; Token
Plan endpoint (chat vs images) is chosen for you; `plan` in the payload names the
login that answered.

## Deck collaboration, refs, text — same discipline as the generic skill

- For PowerPoint/slide callers: take the deck's brand DNA as stage-1 input but
  never skip stages 2 and 4; request the canvas with `--size 1920x1080` (or the
  deck's own pixels), treat `notes` + measured `width`/`height` as what
  actually arrived, place crop-to-fill — never stretch;
  `<deck>/art/slide-<NN>-attempt-<N>.png`; the 4.0 gate is not negotiable for
  placeholders.
- `--ref` narrows to reference-capable providers and fails loudly; never drop
  `--ref` to make a call succeed — switch provider.
- Quote the exact strings, cap 25 characters, forbid extra text; never re-roll
  to fix text — composite it.
- State divider sides and what subjects stand on; physics is default-on.

## Troubleshooting

- **exit 2 "no credentialed image provider"** — configure one in `hermes tools`
  → Image Generation (or `hermes plugins enable alibaba` + keys), or pass
  `--provider X --api-key <key>`.
- **401/429/API error** — rerun with `--fallback`; for a named provider, report
  verbatim and ask.
- **Vision unavailable** — the gate must *see* the image: use a credentialed
  Hermes vision model; if none exists, say the full gate isn't possible and
  never fabricate findings.
