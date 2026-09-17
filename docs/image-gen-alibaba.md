# hermes-plugin-alibaba-image-gen

Hermes Agent `image_gen` provider — unified Alibaba image generation. One provider name
(`alibaba`) covers every Alibaba plan: Token Plan international, Token Plan China,
DashScope PAYG international, DashScope PAYG China, and your own dedicated workspace /
OpenAI-compatible endpoint. The plan and base URL are picked from whichever API key
resolves.

Default model: `wan2.7-image` (~15s). Also available: `wan2.7-image-pro` (~40s).

---

## Prerequisites

- [Hermes Agent](https://github.com/NousResearch/hermes-agent) with native image-generation provider plugin support
- Python >= 3.11 in the Hermes runtime
- An [Alibaba Model Studio](https://bailian.console.aliyun.com) account (Token Plan or DashScope PAYG), or a custom compatible endpoint
- `requests>=2.31,<3` in the Hermes environment; Pillow is optional for reference-image recompression

Hermes reports missing `python_dependencies` from the manifest but does not install them automatically.

---

## GETTING STARTED

### 1. Install the plugin

Shorthand uses slashes only — never `#`:

```bash
hermes plugins install <org>/hermes_plugin_alibaba_image_gen/plugins/image_gen_alibaba --enable
```

Full URL uses exactly one `#` followed by the full subdir path:

```bash
hermes plugins install https://github.com/<org>/hermes_plugin_alibaba_image_gen#plugins/image_gen_alibaba --enable
```

Do **not** mix them: `<repo>#plugins/image_gen_alibaba` parses as repo
`<repo>#plugins` + subdir `image_gen_alibaba`, cloning
`https://github.com/<org>/hermes_plugin_alibaba_image_gen#plugins.git` and failing with
`info/refs not valid`. Expect `Cloning https://github.com/<org>/hermes_plugin_alibaba_image_gen.git (subdir: plugins/image_gen_alibaba)...`.

Replace `<org>` with the GitHub owner hosting this repository. The plugin lives in
`plugins/image_gen_alibaba/plugin.yaml` + `plugins/image_gen_alibaba/__init__.py` +
`plugins/image_gen_alibaba/alibaba.py` — only that subdirectory is copied. Hermes
installs it at `$HERMES_HOME/plugins/alibaba/` (default `~/.hermes/plugins/alibaba/`)
and enables plugin ID `alibaba`. No pip installation, relocation script, or manual copy
is needed; `tests/`, `README.md`, `pyproject.toml`, and `uv.lock` stay out of the install.

### 2. Verify discovery

```bash
hermes plugins list
hermes plugins doctor alibaba --ci
```

If installed without `--enable`, run `hermes plugins enable alibaba`.
Restart running Hermes sessions or the gateway after installation.
The plugin registers provider ID `alibaba` for `hermes tools` → Image Generation.
Registration does not require a Token Plan key: configure any supported credential
source below. Missing credentials make generation unavailable, not plugin discovery.

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

Provider selection is via `image_gen.provider: alibaba` as above; no separate CLI invocation is needed.

### 5. Verify it works and that requests hit Token Plan

**a) Plugin is loaded (no network):**

```bash
hermes plugins list | grep -i alibaba
hermes plugins doctor alibaba --ci
# expect: alibaba  user  0.1.0  backend  enabled .../plugins/alibaba
```

**b) Credential ladder resolves to Token Plan (`alibaba.py:342` `is_available()`):**

```bash
~/.hermes/hermes-agent/venv/bin/python -c "
from plugins.image_gen_alibaba.alibaba import AlibabaImageGenProvider, _candidate_plans, _resolve_plan_credentials
p=AlibabaImageGenProvider()
print('provider', p.name, p.display_name)
print('is_available', p.is_available())
print('models', [r['id'] for r in p.list_models()])
for pl in _candidate_plans():
    cred=_resolve_plan_credentials(pl)
    print(pl.profile, '->', cred[1] if cred else None)
"
# Token Plan must show: alibaba-token-plan -> https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1
grep -A2 image_gen ~/.hermes/config.yaml  # must be provider: alibaba
```

**c) Live generation — check `plan` in the response (charged, ~$0.01, ~15s):**

```bash
~/.hermes/hermes-agent/venv/bin/python <<'PY'
from plugins.image_gen_alibaba.alibaba import AlibabaImageGenProvider
p=AlibabaImageGenProvider()
assert p.is_available(), "no rung resolved — check ALIBABA_TOKEN_PLAN_API_KEY"
res=p.generate("a red fox in a forest, flat vector", aspect_ratio="square")
print(res)
# success asserts:
# res['provider']=='alibaba' and res['extra']['plan']=='alibaba-token-plan'
# res['extra']['plans_tried']==['alibaba-token-plan']  — no fallthrough to PAYG
# image is a URL or /cache/images/alibaba_...png path
PY
```

Or via gateway: `hermes -z "Generate an image of a red fox in a forest"` then `hermes logs --level DEBUG | grep -i "Alibaba alibaba-token-plan"`.

If `plan` is `alibaba` (PAYG) instead of `alibaba-token-plan`, the Token Plan key was not visible to Hermes — set it in `~/.hermes/.env` or `auth.json` pool, not just shell export. One-shot bypass still proves the endpoint: `p.generate(..., api_key='sk-tp-...', base_url='https://token-plan.ap-southeast-1.../compatible-mode/v1')`.

`hermes plugins doctor alibaba --ci` only checks plugin health; generation is live/charged.

### Updating and migrating

```bash
hermes plugins update alibaba
```

For the pre-rename pip install, remove the orphan entry-point (shows as
`alibaba-imggen` with `Failed to load ... No module named 'hermes_plugin_image_gen_ext'`):

```bash
hermes plugins disable alibaba-imggen
~/.hermes/hermes-agent/venv/bin/python -m pip uninstall hermes_plugin_image_gen_ext
hermes plugins list | grep -i alib  # alibaba-imggen row must be gone
```

If pip says “not installed”, delete the two orphans directly — they are the
entire install (RECORD shows only dist-info + `.pth`, no package files):

```bash
rm -rf ~/.hermes/hermes-agent/venv/lib/python3.11/site-packages/hermes_plugin_image_gen_ext-0.1.0.dist-info \
       ~/.hermes/hermes-agent/venv/lib/python3.11/site-packages/hermes_plugin_image_gen_ext.pth
```

Then `hermes gateway restart` (or fresh session) before reinstalling the
native path. Pip distribution and the old `hermes_plugin_alibaba_image_gen`
import path are no longer supported.

---

## Features

| Feature | Detail |
|---------|--------|
| **Auto plan selection** | Token Plan intl → Token Plan CN → PAYG intl → PAYG CN → custom. First key found wins. |
| **Two payload surfaces** | Default `/images/generations` (OpenAI standard). Override to `/chat/completions` for Token Plan-native deploys. |
| **Reference images** | Up to 4 refs (`MAX_REFERENCES=4`), auto-inlined as base64 data URIs (local files >3 MB recompressed via JPEG downscale loop; URLs/data-URIs pass through). `size` is ignored when refs are present. |
| **Model catalog** | `wan2.7-image` (default, ~15s) and `wan2.7-image-pro` (~40s). Unknown ids pass through. Resolution: `model=` kwarg → `ALIBABA_IMAGE_MODEL` env → `image_gen.alibaba.model` (scoped) → `image_gen.model` (top-level) → `wan2.7-image`. |
| **Plan advancement** | 401/403/429/5xx/timeout → next plan. Bad payload/model → stop immediately. |
| **One-shot kwargs** | `api_key=` / `base_url=` bypass the ladder entirely for a single `generate()` call. |
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

## Working Example Configurations (copy-paste)

Every example assumes the plugin is installed and enabled and this is set:

```yaml
# ~/.hermes/config.yaml
image_gen:
  provider: alibaba
```

Only **one** credential rung needs to be present — first rung found wins
(`alibaba-token-plan` → `alibaba-token-plan-cn` → `alibaba` → `alibaba-cn` → `custom` last). Verify with `hermes plugins doctor alibaba --ci`; `is_available()` is `True` when any rung below resolves. No live key is committed — replace `sk-...` placeholders. Hermes resolves credentials via `resolve_runtime_provider` (`auth.json` pool → `~/.hermes/.env` → process env) with a host guard, falling back to plain `get_secret` env reads outside Hermes.

### 1. Token Plan international (verified, `wan2.7-image`/`wan2.7-image-pro`)

```bash
export ALIBABA_TOKEN_PLAN_API_KEY='sk-tp-intl-...'
```

Optional base override (must keep same host family):

```bash
export ALIBABA_TOKEN_PLAN_BASE_URL='https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1'
```

### 2. Token Plan China (verified; falls back to intl key)

```bash
export ALIBABA_TOKEN_PLAN_CN_API_KEY='sk-tp-cn-...'
# if unset, ALIBABA_TOKEN_PLAN_API_KEY is used for this rung
```

Optional:

```bash
export ALIBABA_TOKEN_PLAN_CN_BASE_URL='https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1'
```

### 3. DashScope PAYG international

```bash
export DASHSCOPE_API_KEY='sk-dashscope-...'
# base defaults to https://dashscope-intl.aliyuncs.com/compatible-mode/v1
```

### 4. DashScope PAYG China (requires explicit base)

```bash
export DASHSCOPE_API_KEY='sk-dashscope-...'
export DASHSCOPE_CN_BASE_URL='https://dashscope.aliyuncs.com/compatible-mode/v1'
```

Without `DASHSCOPE_CN_BASE_URL` the `alibaba-cn` rung is skipped.

### 5. Custom workspace — Tier 1: env pair (first fully-set pair wins)

```bash
export ALIBABA_API_KEY='sk-ws-...'
export ALIBABA_BASE_URL='https://ws-hb5vus2qhrcc9f96.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1'
```

Half-set (key without base or vice versa) is ignored — never mixes with another tier.

### 6. Custom workspace — Tier 2: scoped config

```yaml
# ~/.hermes/config.yaml
image_gen:
  provider: alibaba
  alibaba:
    api_key: sk-ws-...
    base_url: https://ws-hb5vus2qhrcc9f96.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1
```

### 7. Custom workspace — Tier 3: provider ref (pool/GUI-manageable)

```yaml
# ~/.hermes/config.yaml
providers:
  ws1:
    api: https://ws-hb5vus2qhrcc9f96.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1
    key_env: WS1_KEY   # or api_key: sk-... / key: sk-...
image_gen:
  provider: alibaba
  alibaba:
    provider: ws1
```

`providers.<name>` accepts `api`/`url`/`base_url` + `api_key`/`key`/`key_env`. Hermes pool/GUI can manage `WS1_KEY`.

### 8. Model selection

Default is `wan2.7-image` (also `wan2.7-image-pro`). Precedence is
`model=` kwarg → `ALIBABA_IMAGE_MODEL` env → `image_gen.alibaba.model` → `image_gen.model` → default. Unknown ids pass through (PAYG may serve other models).

```bash
export ALIBABA_IMAGE_MODEL='wan2.7-image-pro'
```

```yaml
# scoped beats top-level
image_gen:
  provider: alibaba
  model: wan2.7-image
  alibaba:
    model: wan2.7-image-pro
```

```python
# explicit kwarg wins over all
provider.generate("a cat", model="wan2.7-image-pro")
```

### 9. Endpoint surface

Default `POST {base}/images/generations` (`size` honoured, refs rejected as `modality_unsupported`). Set to `POST {base}/chat/completions` for Token Plan-native or reference images (refs inlined as base64; `size` ignored).

```bash
export ALIBABA_IMAGE_ENDPOINT='/chat/completions'
```

```yaml
image_gen:
  provider: alibaba
  alibaba:
    endpoint: /chat/completions
```

Non-`/` values fall through to `/images/generations`.

```yaml
# Token Plan-native example
image_gen:
  provider: alibaba
  alibaba:
    model: wan2.7-image
    endpoint: /chat/completions
```

### 10. Pin the ladder

Skip all other rungs; empty → normal ladder.

```bash
export ALIBABA_IMAGE_PLAN='custom'   # or alibaba-token-plan | alibaba-token-plan-cn | alibaba | alibaba-cn
```

```yaml
# config does not pin; use the env var above. Custom last unless pinned:
# ALIBABA_IMAGE_PLAN=custom makes only the custom rung eligible.
```

### 11. One-shot bypass (no config/env write)

Pinned synthetic plan `explicit`; skips the ladder because `~/.hermes/.env` shadows process env for one-offs.

```python
from alibaba import AlibabaImageGenProvider
p = AlibabaImageGenProvider()
res = p.generate(
    "a red fox in a forest",
    aspect_ratio="landscape",
    model="wan2.7-image",
    api_key="sk-oneoff-...",
    base_url="https://ws-hb5vus2qhrcc9f96.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
)
```

### 12. Where keys are read

- **Inside Hermes:** `resolve_runtime_provider(requested=profile)` probes `auth.json` pool → `~/.hermes/.env` → process env per rung; host guard rejects a runtime that points at the wrong host family.
- **Outside Hermes / tests:** `get_secret()` plain env reads with per-plan defaults above.
- **Picker:** `hermes tools` prompts `ALIBABA_TOKEN_PLAN_API_KEY` as required and `ALIBABA_API_KEY` as optional; PAYG/DashScope keys are set out-of-band and picked up by the ladder.

Check after each setup:

```bash
hermes plugins doctor alibaba --ci
hermes plugins list | grep alibaba
# is_available() == True when any rung above has a key; generation itself is live/charged
```

---

## Custom Workspace Endpoints (semantics)

The three custom tiers above are the only custom surface — first fully-set pair wins. Additional notes:

- The custom rung is tried **last** (after all named plans)
- `ALIBABA_IMAGE_PLAN=custom` pins exclusively to it
- A half-set pair is ignored
- If the workspace speaks `/chat/completions` natively, set `ALIBABA_IMAGE_ENDPOINT=/chat/completions`

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
  Keep only the native `alibaba` installation enabled; the canonical manifest is `plugin.yaml`.
- **Picker shows one prompt** — `hermes tools` only asks for `ALIBABA_TOKEN_PLAN_API_KEY`.
  PAYG users set `DASHSCOPE_API_KEY` out of band; the ladder picks it up.
- **`generate()` never raises** — all errors return an `error_response` dict the LLM can explain.

---

## Testing

The plugin is `plugins/image_gen_alibaba/` (`__init__.py` registers the provider
implemented once in `alibaba.py`; `plugin.yaml` is the manifest). `pyproject.toml` and
`uv.lock` are development-only, not a pip package; `tests/` stay out of the installed
plugin. Tests require a Hermes source checkout at `~/.hermes/hermes-agent`, or set
`HERMES_AGENT_REPO` to its path. The local uv development interpreter is selected by
`.python-version`; tests can also run using Hermes' Python with pytest installed.

```bash
uv run --locked --group dev pytest -q
```

Coverage includes credential resolution, payloads, response parsing, reference images,
plan advancement, endpoint overrides, native manifest discovery, registration without
pip entry points, and installer destination/metadata (fragment install copies only
`plugins/image_gen_alibaba/`). HTTP and Git cloning are mocked; no live API calls or
production Hermes configuration changes occur.

Project-local discovery is optional for development only: link `plugins/image_gen_alibaba/`
to `<workspace>/.hermes/plugins/alibaba` (symlink the repo's `plugins/image_gen_alibaba/` dir),
launch Hermes from that workspace with `HERMES_ENABLE_PROJECT_PLUGINS=1`, and enable
`alibaba`. The canonical distribution path remains
`hermes plugins install <org>/hermes_plugin_alibaba_image_gen/plugins/image_gen_alibaba --enable`.
