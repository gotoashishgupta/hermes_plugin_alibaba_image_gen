# hermes-plugin-alibaba-image-gen

Hermes Agent `image_gen` provider — unified Alibaba image generation. One provider name
(`alibaba`) covers every Alibaba plan: Token Plan international, Token Plan China,
DashScope PAYG international, DashScope PAYG China, and your own dedicated workspace /
OpenAI-compatible endpoint. The plan and base URL are picked from whichever API key
resolves.

Default model: `wan2.7-image` (~15s). Also available: `wan2.7-image-pro` (~40s).

---

## Prerequisites

- [Hermes agent](https://github.com/nicepkg/hermes) installed with its venv at `~/.hermes/hermes-agent/`
- Python >= 3.11
- An [Alibaba Model Studio](https://bailian.console.aliyun.com) account (Token Plan or DashScope PAYG)
- [`uv`](https://docs.astral.sh/uv/) (recommended) or pip

---

## GETTING STARTED

### 1. Install the plugin

```bash
cd hermes_plugin_alibaba_image_gen
uv pip install --python ~/.hermes/hermes-agent/venv/bin/python3 -e .
```

### 2. Enable in Hermes

```bash
~/.hermes/hermes-agent/venv/bin/hermes plugins enable alibaba-imggen --no-allow-tool-override
```

This registers the plugin — `alibaba-imggen` becomes a row in `hermes tools` → Image
Generation.

### 3. Configure credentials

Set **one** of these key sources (first one found wins):

```bash
# Option A: export (also readable from ~/.hermes/.env or OpenBao-injected boot env)
export ALIBABA_TOKEN_PLAN_API_KEY='sk-...'        # Token Plan intl (priority 1)
# or
export ALIBABA_TOKEN_PLAN_CN_API_KEY='sk-...'     # Token Plan China (priority 2)
# or
export DASHSCOPE_API_KEY='sk-...'                 # DashScope PAYG (priority 3–4)
```

| Variable | Plan | Key source |
|----------|------|-----------|
| `ALIBABA_TOKEN_PLAN_API_KEY` | Token Plan intl | [bailian.console.aliyun.com](https://bailian.console.aliyun.com/?apiKey=1) |
| `ALIBABA_TOKEN_PLAN_CN_API_KEY` | Token Plan China | Falls back to TP intl key if unset |
| `DASHSCOPE_API_KEY` | DashScope PAYG intl | Also serves CN rung (needs `DASHSCOPE_CN_BASE_URL`) |

Credentials are read through Hermes' own ladder: `auth.json` pool → `~/.hermes/.env`
→ process env — so `hermes tools`-saved keys, `auth.json`-pooled keys, and OpenBao-injected
boot keys all work identically.

### 4. Select the provider

In `~/.hermes/config.yaml`:
```yaml
image_gen:
  provider: alibaba
```

Or invoke directly (no config needed):
```bash
hg_image.py generate --provider alibaba --prompt "a red fox in a forest"
```

### 5. Verify it works

```bash
uv run --python ~/.hermes/hermes-agent/venv/bin/python3 -c "
from hermes_plugin_alibaba_image_gen.alibaba import AlibabaImageGenProvider
p = AlibabaImageGenProvider()
print('Available:', p.is_available())
print('Models:', [m['id'] for m in p.list_models()])
"
```

If `Available: True`, you're ready to generate.

---

## Features

| Feature | Detail |
|---------|--------|
| **Auto plan selection** | Token Plan intl → Token Plan CN → PAYG intl → PAYG CN → custom. First key found wins. |
| **Two payload surfaces** | Default `/images/generations` (OpenAI standard). Override to `/chat/completions` for Token Plan-native deploys. |
| **Reference images** | Up to 4 refs, auto-inlined as base64 (recompressed >3MB). Aspect adopted from ref. |
| **Model catalog** | `wan2.7-image` (default, ~15s) and `wan2.7-image-pro` (~40s). Unknown ids pass through. |
| **Plan advancement** | 401/403/429/5xx/timeout → next plan. Bad payload/model → stop immediately. |
| **One-shot kwargs** | `api_key=` / `base_url=` bypass the ladder entirely (used by `hg_image.py --api-key`). |
| **Never raises** | `generate()` catches all exceptions → `error_response` the LLM can explain. |
| **Response parsing** | Handles `data[].url`, `data[].b64_json`, `output.choices`, `message.images[]`. |
| **Seed reporting** | Extracts `actual_seed`, `output_W`, `output_H` from Token Plan debug info. |

---

## Configuration Reference

### Environment Variables

| Variable | Purpose | Required |
|----------|---------|----------|
| `ALIBABA_TOKEN_PLAN_API_KEY` | Token Plan intl key | One of these |
| `ALIBABA_TOKEN_PLAN_CN_API_KEY` | Token Plan CN key | — |
| `DASHSCOPE_API_KEY` | DashScope PAYG key | — |
| `DASHSCOPE_CN_BASE_URL` | Gates the `alibaba-cn` rung | For China |
| `ALIBABA_API_KEY` + `ALIBABA_BASE_URL` | Custom workspace / OpenAI-compat | For custom rung |
| `ALIBABA_IMAGE_MODEL` | Model override (beats config) | Optional |
| `ALIBABA_IMAGE_PLAN` | Pin one profile, skip ladder | Optional |
| `ALIBABA_IMAGE_ENDPOINT` | Override path (default `/images/generations`) | Optional |
| `ALIBABA_TOKEN_PLAN_BASE_URL` | Override Token Plan intl host | Optional |
| `ALIBABA_TOKEN_PLAN_CN_BASE_URL` | Override Token Plan CN host | Optional |

### config.yaml

```yaml
image_gen:
  provider: alibaba              # select as default provider
  alibaba:
    model: wan2.7-image          # default model
    endpoint: /images/generations  # or /chat/completions for Token Plan
    # api_key: sk-...            # or keep secrets in env/vault
    # base_url: https://...
    # provider: ws1              # reference top-level providers.<name>

# Optional: reference a Hermes custom provider entry
providers:
  ws1:
    api: https://ws-.../compatible-mode/v1
    key_env: WS1_KEY
```

### Credential Resolution Order

1. `api_key=` / `base_url=` kwargs (one-shot, never persisted)
2. Hermes resolver (`resolve_runtime_provider`): `auth.json` pool → `~/.hermes/.env` → process env
3. Fallback: plain env reads (outside a Hermes process)

---

## Custom Workspace Endpoints

Point the plugin at any Alibaba Model Studio workspace
(`https://ws-<id>.<region>.maas.aliyuncs.com/compatible-mode/v1`) or any OpenAI-compatible
image server. Three configuration tiers — first fully-set pair wins:

```bash
# Tier 1: env pair
export ALIBABA_API_KEY='sk-ws-...'
export ALIBABA_BASE_URL='https://ws-hb5vus2qhrcc9f96.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1'
```

```yaml
# Tier 2: scoped config — ~/.hermes/config.yaml
image_gen:
  alibaba:
    api_key: sk-ws-...
    base_url: https://ws-.../compatible-mode/v1
```

```yaml
# Tier 3: reference a Hermes custom provider entry (pool/GUI-manageable)
providers:
  ws1: { api: https://ws-.../compatible-mode/v1, key_env: WS1_KEY }
image_gen:
  alibaba: { provider: ws1 }
```

**Semantics:**
- The custom rung is tried **last** (after all named plans)
- Use `ALIBABA_IMAGE_PLAN=custom` to pin exclusively to the custom rung
- A half-set pair (key without base, or vice versa) is ignored
- If your workspace speaks `/chat/completions` natively, set `ALIBABA_IMAGE_ENDPOINT=/chat/completions`

---

## How It Works

### Request flow

```
_candidate_plans() → _resolve_plan_credentials() → _resolve_endpoint() → POST
```

1. **Plan selection** — builds the ladder (Token Plan → PAYG → custom), applies `ALIBABA_IMAGE_PLAN` pin
2. **Credential resolution** — for each plan, resolves `(api_key, base_url)` through Hermes' ladder
3. **Endpoint resolution** — `ALIBABA_IMAGE_ENDPOINT` env → config → `/images/generations` default
4. **Single POST** — no retry, no fallback. On failure, `_should_advance()` decides: next plan or stop

### Default surface (`/images/generations`)

```
POST {base}/images/generations
Body: {model, prompt, n: 1, size}
```

Reference images are **rejected** — returns `modality_unsupported` before any POST.

### Chat surface (`ALIBABA_IMAGE_ENDPOINT=/chat/completions`)

```
POST {base}/chat/completions
Body: {model, messages: [{role: "user", content: [text, ...images]}], size}
```

Reference images are supported — inlined as base64 content parts.

### Failure handling

| Failure | Action |
|---------|--------|
| 401, 403, 429 | Advance to next plan |
| 5xx, timeout, connection error | Advance to next plan |
| Model not found on unverified PAYG | Advance to next plan |
| 4xx (bad payload), model error on verified plan | Stop immediately |

The result always reports `plan` (which plan answered) and `plans_tried` (the full attempt chain).

---

## Caveats

- **PAYG (rungs 3–4) model availability is unverified** — wan models are proven on Token Plan
  only. If a PAYG endpoint lacks them, you get an explicit error pointing at `GET {base}/models`.
  Unknown model ids always pass through so you can name what's deployed.
- **Reference images require `/chat/completions`** — the `/images/generations` surface has no
  image input. Use `ALIBABA_IMAGE_ENDPOINT=/chat/completions` (applies to all plans), or omit refs.
- **Last-writer-wins** — another plugin registering provider name `alibaba` replaces this one.
  That's the supported override surface; see `packaging/alibaba-dir-install/` for the
  directory-install manifest.
- **Picker shows one prompt** — `hermes tools` only asks for `ALIBABA_TOKEN_PLAN_API_KEY`.
  PAYG users set `DASHSCOPE_API_KEY` out of band; the ladder picks it up.
- **`generate()` never raises** — all errors return an `error_response` dict the LLM can explain.

---

## Testing

```bash
uv run --python ~/.hermes/hermes-agent/venv/bin/python3 -m pytest tests/
```

83 tests covering: credential ladder order, payload shapes, response parsing, reference
images, plan advancement, env/config fallback, endpoint overrides, and the full generate
happy path. Tests run against mocked HTTP — no live API calls.
