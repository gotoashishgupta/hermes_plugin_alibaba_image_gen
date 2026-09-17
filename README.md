# hermes-extensions — monorepo for Hermes

Monorepo for Hermes extensions. Each leaf under `plugins/`, `skills/`, `providers/`, `tools/` is an **install unit** — `hermes plugins install <org>/<repo>/<leaf> --enable` copies **only that leaf** (`_resolve_subdir_within` + `_swap_in_plugin`). Tests, root docs, `pyproject.toml`, and other leaves never ship.

## Layout

```
repo/
  plugins/
    image_gen_alibaba/   # image_gen backend: alibaba unified (Token Plan / DashScope / custom) — __init__.py + alibaba.py + plugin.yaml
    openbao/             # secret-source: OpenBao Vault AppRole — __init__.py + plugin.yaml
  skills/
    image-pipeline/      # generic quality-gated image skill (harness-independent; Hermes optional) — SOURCE OF TRUTH for scripts/
    hermes-image-pipeline/ # Hermes-native skill leaf: SKILL.md + byte-identical scripts/ copy (parity test enforced)
  providers/             # reserved — model-providers per category (own discovery)
  tools/                 # reserved — standalone-kind plugins
  tests/
    conftest.py          # shared: Hermes-repo path, env hygiene
    image_gen_alibaba/   # alibaba suite (ladder, payload, refs, failover, native install)
    image_pipeline/      # driver suite (adapters, routing, envelope, leaf parity — stdlib-only, no Hermes anywhere)
    openbao/             # openbao suite (importable from repo/plugins, fully mocked)
  docs/
    image-gen-alibaba.md # alibaba install + 12 working configs + verification
    image-pipeline.md    # driver routing spec + provider/env matrix + sizing/exit codes
    openbao.md           # openbao secret-source docs
  pyproject.toml / uv.lock / .python-version  # single toolchain (package=false, dev group)
```

Install units are leaves; everything else (`tests/`, `docs/`, `pyproject.toml`, `graphify-out/`, sibling leaves) stays out of `~/.hermes/plugins/<name>/`.

## Install

Shorthand uses slashes only — never `#` alone; full URL uses one `#` with full subdir:

```bash
# alibaba image_gen
hermes plugins install <org>/hermes_plugin_alibaba_image_gen/plugins/image_gen_alibaba --enable
# or: https://github.com/<org>/hermes_plugin_alibaba_image_gen#plugins/image_gen_alibaba

# openbao secret-source
hermes plugins install <org>/hermes_plugin_alibaba_image_gen/plugins/openbao --enable
# or: https://github.com/<org>/hermes_plugin_alibaba_image_gen#plugins/openbao

# hermes-image-pipeline skill leaf
hermes plugins install <org>/hermes_plugin_alibaba_image_gen/skills/hermes-image-pipeline --enable
# then load the skill in-session as: hermes-image-pipeline:image-pipeline
```

Do **not** mix: `<repo>#plugins/image_gen_alibaba` parses as repo `<repo>#plugins` + subdir `image_gen_alibaba` → `info/refs not valid`. Expect `Cloning https://github.com/<org>/hermes_plugin_alibaba_image_gen.git (subdir: plugins/...)...` and `~/.hermes/plugins/alibaba/` or `~/.hermes/plugins/openbao/` containing only the leaf files.

Verify:
```bash
hermes plugins list | grep -E "alibaba|openbao"
hermes plugins doctor alibaba --ci
hermes plugins doctor openbao --ci
tail -20 ~/.hermes/logs/openbao.log
hermes config | grep "(from OpenBao)"
```

## Docs

* Alibaba image_gen — `docs/image-gen-alibaba.md` (12 copy-paste configs, model/endpoint/pin, live `plan==alibaba-token-plan` verification)
* Image pipeline driver — `docs/image-pipeline.md` (routing modes, provider/env matrix, `--size`/`--endpoint` semantics, exit codes, payload contract)
* OpenBao secret-source — `docs/openbao.md` (AppRole, `kv-dev/hermes/*` wildcard policy, `-mount=kv-dev` reads, ` (from OpenBao)` provenance)

## Testing

Single toolchain at root (`package=false`):

```bash
uv run --locked --group dev pytest -q                 # all extensions (257 tests: 90 alibaba + 78 openbao + 89 image_pipeline)
uv run --locked --group dev pytest -q tests/image_gen_alibaba
uv run --locked --group dev pytest -q tests/openbao
uv run --locked --group dev pytest -q tests/image_pipeline
```

No live vault, no network, no `bao` binary needed — all mocked, mocked Git cloning too.

Project-local discovery for dev:
```bash
ln -s $(pwd)/plugins/image_gen_alibaba /tmp/ws/.hermes/plugins/alibaba
ln -s $(pwd)/plugins/openbao /tmp/ws/.hermes/plugins/openbao
HERMES_ENABLE_PROJECT_PLUGINS=1 hermes plugins doctor alibaba --ci
```

## Adding a new extension

1. Create a leaf: `plugins/<kind>_<name>/` (or `skills/<name>/`, `providers/<cat>/<name>/`) — only runtime files inside.
2. Add tests under `tests/<leaf>/` and docs under `docs/<leaf>.md`.
3. Extend `pyproject.toml` dev group if needed.
4. Document install as `<org>/<repo>/<leaf> --enable` in this README.

All extensions ship clean — no `README.md`, `tests/`, or sibling leaves in the installed path.
