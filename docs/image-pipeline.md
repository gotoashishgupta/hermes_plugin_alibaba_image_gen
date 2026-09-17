# image-pipeline — driver & provider routing

The skill's generation driver is `skills/image-pipeline/scripts/hg_image.py` (+
the `imagegen/` package). It is **harness-independent**: stdlib-only Python 3.11+,
no packages to install. Hermes is optional acceleration, never a requirement.

This document is the operational reference: routing modes, the provider/env
matrix, endpoint semantics, and behavior changes vs the pre-refactor script.

## Routing modes

```
--api-key / --base-url / --endpoint + --provider ....... direct (bypasses discovery)
Hermes repo found
   registry providers credentialed ............... mode 1  route=hermes-registry
   plugin reachable, keys from env only .......... mode 2  credential_source=env
   (repo found but THIS python can't import hermes deps → the script re-execs
    once into <repo>/venv/bin/python, loop-guarded by HG_IMAGE_NO_REEXEC=1;
    if no venv exists it continues standalone with a stderr warning — it never
    hard-fails because Hermes is absent)
No Hermes ........................................... mode 3  standalone adapters
```

`--mode hermes|standalone|auto` forces the path (`hermes` without a repo exits 2).
`list --json` reports the truth under `modes` (`reachable`, `drivable`, repo path).

Hermes detection is: `$HERMES_AGENT_REPO`, else `~/.hermes/hermes-agent`, validated
by the presence of `agent/image_gen_registry.py`. `~/.hermes/.env` is loaded
(no-clobber) before any credential check, and the credential *pool* /
`hermes tools` configuration is used only through Hermes' own code (mode 1/2).

## Provider / credential matrix (standalone adapters, mode 3 & direct)

| Provider | Key env(s) | Base URL (default) | Surfaces (`--endpoint`) | Model env | Refs |
|---|---|---|---|---|---|
| `alibaba` | `ALIBABA_TOKEN_PLAN_API_KEY` → `ALIBABA_TOKEN_PLAN_CN_API_KEY` → `DASHSCOPE_API_KEY` (+`DASHSCOPE_CN_BASE_URL` for CN) → `ALIBABA_API_KEY`+`ALIBABA_BASE_URL` (custom, last) | per plan (Token Plan intl/CN, DashScope PAYG intl/CN); `ALIBABA_TOKEN_PLAN_BASE_URL`/`ALIBABA_TOKEN_PLAN_CN_BASE_URL` override hosts; `ALIBABA_IMAGE_PLAN` pins one login | `/chat/completions` (auto-defaulted while a Token Plan key is in play — their hosts 404 on `/images/generations`) or `/images/generations` | `ALIBABA_IMAGE_MODEL` (default `wan2.7-image`) | ≤4, data-URI inline ≤3 MB |
| `openai` (+ any OpenAI-compatible) | `OPENAI_API_KEY` | `OPENAI_BASE_URL` or `https://api.openai.com/v1`; `--base-url` per call | `/images/generations` (default), `/images/edits` (auto with refs), `/chat/completions` (via `--endpoint`) | `OPENAI_IMAGE_MODEL`; virtual tiers `gpt-image-2-{low,medium,high}` | ≤16 multipart `image[]` |
| `openrouter` | `OPENROUTER_API_KEY` | `OPENROUTER_BASE_URL` or `https://openrouter.ai/api/v1` | dedicated `/images/generations` for curated models; legacy `/chat/completions` `modalities` path for the rest (tested defaults stay on chat) | `OPENROUTER_IMAGE_MODEL` | ≤14 (images) / ≤3 (chat) |
| `gemini` | `GEMINI_API_KEY` (alias `GOOGLE_API_KEY`) | fixed `generativelanguage.googleapis.com/v1beta` (no `--base-url`) | `models/{id}:generateContent` | `GEMINI_IMAGE_MODEL` (default `gemini-3.1-flash-image`) | ≤14 `inlineData` |
| `fal` | `FAL_KEY` | queue `https://queue.fal.run` / sync `https://fal.run` | per model; `<model>/edit` used automatically with `--ref` | `FAL_IMAGE_MODEL` (default `fal-ai/flux-2/klein/9b`) | ≤ model cap via edit endpoint |
| `openai-codex` | — | — | **Hermes modes only** (ChatGPT OAuth) | | |

Custom endpoint: `--provider <any-other-name> --api-key K --base-url U [--endpoint E]`
synthesizes an OpenAI-compatible adapter named after the provider.

Cache/materialization: images land in `$HG_IMAGE_CACHE` (default
`~/.cache/image-pipeline/`) and are copied to `--out`; provider URLs (fal media links
expire) are always downloaded before the payload is emitted.

## Sizing

- `--aspect landscape|square|portrait` — semantic, ratio-exact on every adapter.
- `--size WxH` — exact-canvas request (deck work, e.g. `1920x1080`). Wins over
  `--aspect` for the reported ratio. Only `fal` `image_size` models accept arbitrary
  pixels; everything else snaps to its nearest supported size/ratio. The payload
  carries `size_requested` + `size_note`, and `width`/`height` are MEASURED from the
  delivered file (never trusted from provider metadata) — a >5% ratio deviation gets
  a note. Deck consumers crop-to-fill/upscale from the measured dims; never stretch.

## Exit codes

- `0` success
- `1` generation attempted and failed — the envelope JSON goes to stderr
- `2` bad usage / no usable provider (was `1` before the refactor; the old
  docstring always promised 2)

## Payload (back-compatible superset)

Old keys unchanged: `success, provider, model, prompt, aspect_ratio, image,
references` + optional `plan, note, seed, width, height`. New additive keys:
`route` (`hermes-registry|standalone|direct`), `credential_source`
(`hermes-ladder|env|cli`), `size_requested`, `size_note`, `attempts[]` (failed
backends before the winner, `--fallback` runs only). `provider`/`model` always name
what ACTUALLY produced the image.

## Behavior changes vs the pre-refactor script

1. No Hermes repo no longer aborts — the standalone adapters answer with env keys.
2. `--base-url` alone no longer silently rewrites the Token Plan host for EVERY
   provider; it still does (with a stderr note) for `--provider alibaba`/no provider
   for one release, then retires. Named providers get it per-backend.
3. All `ALIBABA_*` special-casing moved into the alibaba adapter; the CLI is generic.
4. Registry access uses the public `list_providers()` API (was private
   `_registry.merged()`).
5. Exit 2 for the no-provider/usage class (was exit 1).
6. `--json` payloads gained the additive keys above; nothing removed or renamed.

## Drift contract

`imagegen/alibaba.py` is a Hermes-free port of `plugins/image_gen_alibaba/alibaba.py`
(the plugin remains the reference for hermes mode; both share the plan ladder,
payload inference, failure classification and response-shape tolerance). When you
change either, check the other and record it here; the mirrored response fixtures in
`tests/image_pipeline/` vs `tests/image_gen_alibaba/` keep both honest.

## Testing

- `uv run --locked --group dev pytest -q tests/image_pipeline` (83 tests, faked
  network only, no Hermes anywhere)
- `tests/image_pipeline/test_purity.py` proves stdlib-only imports in a fresh `-I`
  interpreter; `test_leaf_parity.py` pins the hermes leaf's scripts copy to this tree.
- Live verification (spends credits; run consciously):
  `python3 skills/image-pipeline/scripts/hg_image.py list` (re-exec exhibit),
  `... generate --mode standalone --provider alibaba --prompt "..." --json` etc.
