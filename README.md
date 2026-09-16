# hermes-plugin-image-gen-ext

Hermes Agent `image_gen` provider extensions, distributed as one pip package.

**`alibaba` — unified Alibaba image login.** One provider name (`alibaba`) covers every
Alibaba plan; the plan and base URL are picked from whichever API key resolves, in
preference order:

| Priority | Plan (Hermes profile) | Key env var | Endpoint |
|---|---|---|---|
| 1 | `alibaba-token-plan` | `ALIBABA_TOKEN_PLAN_API_KEY` | token-plan.ap-southeast-1.maas (intl) |
| 2 | `alibaba-token-plan-cn` | `ALIBABA_TOKEN_PLAN_CN_API_KEY` (fallback: shared TP key) | token-plan.cn-beijing.maas |
| 3 | `alibaba` | `DASHSCOPE_API_KEY` | dashscope-intl (PAYG) |
| 4 | `alibaba-cn` | `DASHSCOPE_API_KEY` + `DASHSCOPE_CN_BASE_URL` | dashscope CN (skipped unless the CN base var is set) |
| 5 | `custom` | see below | your own dedicated workspace / OpenAI-compatible endpoint, tried **last** |

Models: `wan2.7-image` (default), `wan2.7-image-pro`, over the OpenAI-compatible
chat/completions endpoint; reference images supported (the model adopts the reference's
aspect and ignores `size` then — reported via `note`). Large local references are
recompressed to fit the gateway body limit before inlining.

## Custom endpoint (dedicated workspaces, OpenAI-compat servers)

Point the plugin at any Alibaba Model Studio **workspace endpoint**
(`https://ws-<id>.<region>.maas.aliyuncs.com/compatible-mode/v1`) or any OpenAI-compatible
image server. Three configuration tiers, first fully-set pair wins:

```bash
# 1) env pair (also readable from ~/.hermes/.env or an OpenBao-injected boot env)
export ALIBABA_API_KEY='sk-ws-...'
export ALIBABA_BASE_URL='https://ws-hb5vus2qhrcc9f96.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1'
export ALIBABA_IMAGE_MODEL='wan2.7-image-my-deploy'   # any model id, unknown ids pass through
```
```yaml
# 2) scoped config — ~/.hermes/config.yaml
image_gen:
  alibaba:
    api_key: sk-ws-...          # or better: keep the secret in env/vault, base here
    base_url: https://ws-.../compatible-mode/v1
# 3) reference a Hermes custom provider entry (pool/GUI-manageable)
providers:
  ws1: { api: https://ws-.../compatible-mode/v1, key_env: WS1_KEY }
image_gen:
  alibaba: { provider: ws1 }
```

Semantics: the custom rung is tried **after** the named plans (or exclusively with
`ALIBABA_IMAGE_PLAN=custom`); a half-set pair is ignored. Requests use
`chat/completions` first; on a `404` (never billed) a custom plan automatically retries
the OpenAI `POST {base}/images/generations` shape and parses `data[].url`, `data[].b64_json`
and `message.images[]` responses. With reference images there is no retry — edits need the
chat surface, and a reference is never silently dropped. One-off use without any config:
`hg_image.py generate --provider alibaba --api-key K --base-url U --model M …` (the kwargs
pin is tried **first**, bypassing the ladder).

Keys are read through Hermes' own credential ladder (`resolve_runtime_provider`:
`auth.json` credential pool → `~/.hermes/.env` → process env), so OpenBao-injected boot
keys, `hermes tools`-saved keys, and `hermes auth`-pooled keys all work identically.
On a generate call, 401/403/429/5xx/timeout/unknown-model-on-PAYG failures **advance to
the next plan**; the result reports `plan` and `plans_tried`.

## Install (editable dev) + enable

```bash
uv pip install --python ~/.hermes/hermes-agent/venv/bin/python3 -e .
~/.hermes/hermes-agent/venv/bin/hermes plugins enable alibaba-imggen --no-allow-tool-override
```

`alibaba-imggen` (the entry-point name) becomes a row in `hermes tools` → Image
Generation. Hermes' own `image_generate` tool uses it once
`image_gen.provider: alibaba` is set in `~/.hermes/config.yaml`; the
`image-pipeline` skill's `hg_image.py` reaches it by name regardless.

Optional config / env:

- `image_gen.alibaba.model` or `ALIBABA_IMAGE_MODEL` — default model.
- `ALIBABA_IMAGE_PLAN` — pin one profile (e.g. `alibaba`) and skip the ladder.
- `ALIBABA_TOKEN_PLAN_BASE_URL` / `ALIBABA_TOKEN_PLAN_CN_BASE_URL` / `DASHSCOPE_CN_BASE_URL`
  — per-plan endpoint overrides.

`generate()` also honors `api_key=` / `base_url=` kwargs (used by `hg_image.py
--api-key`) — pinned for that call only, never persisted. Prefer these over writing
`os.environ`: Hermes' ladder *prefers `~/.hermes/.env` over the process env*, so a
same-named env var would shadow it.

## Caveats

- **PAYG (rungs 3–4) model availability is unverified** — wan models are proven on Token
  Plan; if a PAYG endpoint lacks them you get an explicit error pointing at
  `GET {base}/models`. Unknown model ids always pass through so you can name what's there.
- A later plugin registering provider name `alibaba` **replaces this one** (Hermes'
  registry is last-writer-wins) — that's the supported override surface; see
  `packaging/alibaba-dir-install/` for the directory-install manifest reference.
- `hermes tools` prompts only `ALIBABA_TOKEN_PLAN_API_KEY` (one prompt); PAYG users set
  `DASHSCOPE_API_KEY` out of band — the ladder picks it up.

## Tests

```bash
~/.hermes/hermes-agent/venv/bin/python3 -m pytest tests   # needs the Hermes venv (imports agent/hermes_cli)
```
