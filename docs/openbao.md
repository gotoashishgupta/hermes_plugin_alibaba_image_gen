# OpenBao Secret Source Plugin for Hermes

`OpenBaoSource` is a Hermes `SecretSource` plugin (API v1, `shape: mapped`) that reads LLM provider API keys from OpenBao Vault instead of the dotenv file, and keeps them fresh without restarts.

At every Hermes start it logs in with AppRole, lists all keys under a configured prefix, reads each value, and hands them to the process environment — providers see `OPENAI_API_KEY` etc. exactly as if they came from `.env`. Each key shows as `(from OpenBao)` in status output. A background daemon poller picks up rotations. Failures are soft: vault unreachable never blocks startup.

## How it works

Fetch lifecycle (`fetch()` → auth → discover → read → watch, never raises, never prompts):

1. **Validate + gate** — non-dict config → `NOT_CONFIGURED`; `enabled: false` → empty ok result.
2. **AppRole login** — reads `role_id` from config and `secret_id` from the file at `secret_id_file`. Runs `bao write auth/approle/login` via argv (no shell). Caches the token until 80% of `min(ttl_seconds, real lease_duration)` — the Ansible role issues 15m tokens, so the lease wins when shorter.
3. **Discover** — `bao kv list <mount>/<base>` (default `kv-dev/hermes`) with `-namespace=<namespace>` (default `m5`). Every leaf entry is a candidate env var.
4. **Read** — `bao kv get -format=json <mount>/<base>/<NAME>` per key, in parallel (`read_workers: 8` — one call takes ~5s against this server, so 33 sequential reads would blow the orchestrator's 120s fetch budget and be discarded as TIMEOUT). KV v2 payloads nest fields under `data.data`; the plugin prefers the `value` field (warns on multi-field ambiguity). Empty/unfilled placeholders return `None` and become warnings, never empty env vars.
5. **Apply + watch** — valid names (`^[A-Za-z_][A-Za-z0-9_]*$`, normalized via uppercasing, `-`/`.` → `_`) go into `FetchResult.secrets`; the orchestrator applies them to the process environment after dotenv loads. Then a daemon `threading.Timer` polls (default 60s), samples up to 3 keys, writes rotations straight to the process environment, and self-heals: if all samples miss (expired token / rotated secret-id), it re-reads the secret-id file and re-logs in without a restart.

With `override_existing: false` (default), any key still in `.env` wins; delete the `.env` line and the OpenBao value takes over. Set `override_existing: true` to let the vault clobber `.env`/shell values (honored via `override_existing()`).

## Prerequisites

All of these must exist before the plugin returns secrets:

- **OpenBao server + `bao` CLI v2.x** on PATH (`/opt/homebrew/bin/bao`). Verify: `bao version` shows `v2.x`, `bao status` succeeds. `addr` config or `$BAO_ADDR` must point at it.
- **Server-side objects (Ansible converge, committed, must be run):** AppRole role `hermes` in namespace `m5`, read-only `hermes-read` policy over `kv-dev/data/hermes/*`, and the 33 seeded (empty) placeholders. Until converge runs, login/list return nothing.
- **Secret-id file:** the path in `secret_id_file` (default `~/.hermes/auth/openbao-secret-id`), `chmod 0600`, containing only the secret-id. Missing/unreadable → `NOT_CONFIGURED`, never an exception. Only `google_oauth.json` ships by default — this file is created by converge.
- **`secrets.openbao` in `config.yaml`:** only `bitwarden` (disabled) ships by default; you must add the `openbao` section (see below). Non-secret `role_id` comes from converge output; the `secret_id` itself never goes in config.
- **Filled values:** after converge, fill the 33 placeholders once via OIDC login + `bao kv patch`. Until then every key surfaces as a "no value … (empty placeholder or read failure)" warning and `.env` stays the live source.
- **Plugin install location:** `~/.hermes/plugins/openbao/__init__.py` + `plugin.yaml` (this repo). Hermes auto-discovers it; `register()` calls `ctx.register_secret_source(OpenBaoSource())`.
- **Python 3.11+**, zero pip dependencies. TLS setups needing self-signed certs rely on `BAO_CACERT` / `BAO_SKIP_VERIFY` passthrough (see Security).

## Install

```bash
mkdir -p ~/.hermes/plugins/openbao ~/.hermes/auth
cp __init__.py plugin.yaml ~/.hermes/plugins/openbao/
echo -n "<approle-secret-id>" > ~/.hermes/auth/openbao-secret-id
chmod 0600 ~/.hermes/auth/openbao-secret-id
```

## Configuration

```yaml
secrets:
  openbao:
    enabled: true
    role_id: "<from converge output>"
    secret_id_file: "~/.hermes/auth/openbao-secret-id"
    namespace: "m5"              # or $BAO_NAMESPACE; empty = server default (this deployment uses m5)
    mount: "kv-dev"
    base: "hermes"               # objects live at kv-dev/hermes/<NAME> in ns m5
    addr: ""                    # defaults to $BAO_ADDR
    ttl_seconds: 300            # cache window; real lease wins when shorter
    watch_interval_seconds: 60  # 0 disables the background poller
    override_existing: false    # true lets vault clobber .env/shell values
    # debug: false              # or HERMES_OPENBAO_DEBUG=1 for DEBUG logs
```

Logs go to stderr (picked up by Hermes) and `~/.hermes/logs/openbao.log`:

```sh
tail -f ~/.hermes/logs/openbao.log
HERMES_OPENBAO_DEBUG=1 hermes model
```

Levels: `INFO` fetch start (`addr_host`, `ns`, `prefix`, `override`), list count, fetched/warnings; `WARNING` login/list/get failures with `bao` stderr snippet, nested-entry skips, multi-field ambiguity, watcher re-login; `DEBUG` per-command (redacted `role_id`/`secret_id`), per-key reads, watcher ticks. Secret values, tokens, and secret-ids are never logged.

`base` replaced the original `namespace_prefix` key; old configs using `namespace_prefix: "hermes/"` still resolve unchanged.

## Migration from `.env`

1. Add the config above (at least `enabled`, `role_id`, `secret_id_file`).
2. `hermes model` (triggers fetch), then `hermes config` — expect `VAR (from OpenBao)` provenance per key.
3. Delete keys from your `.env` file one at a time, reload, send a test prompt each time.
4. Rollback: `enabled: false` skips the source entirely on next reload; `.env` entries take precedence again immediately.

## Failure modes (all soft, `fetch()` never raises)

| Situation | Result |
|---|---|
| Disabled / non-dict config | empty ok result |
| Missing `role_id` / `secret_id_file` / unreadable secret-id file | `NOT_CONFIGURED` |
| Login fails | `AUTH_FAILED` |
| Bad `base` leaf | `REF_INVALID` |
| Vault unreachable mid-run, empty read, bad name | warning per key, startup continues |
| Watcher tick throws | swallowed, retried next interval |

## Security

- `secret_id` lives in a `0600` file, read per fetch and per watcher re-login; never in config or env.
- Vault tokens stay in instance memory, passed per-call via `VAULT_TOKEN` in the child env; never exported to the process environment (`protected_env_vars()` returns empty for this reason).
- Child `bao` processes get an allowlist only: `PATH HOME USERPROFILE SYSTEMROOT LANG LC_ALL` + `BAO_SKIP_VERIFY BAO_CACERT BAO_CAPATH BAO_CLIENT_CERT BAO_CLIENT_KEY BAO_TLSSERVERNAME`; `stdin=/dev/null`, 30s timeout, no shell interpolation, `-namespace=` inserted after the subcommand.
- Tokens are 15m-lived; environment updates happen on a GIL-protected mapping from a daemon thread that dies with Hermes.

## Deliberately not built

No encrypted disk cache (the vault is the source of truth), no stale fallback on outage, no per-profile scoping (process-global), no `hermes secrets openbao setup` CLI, no event subscription — polling is the granularity.

## Layout / tests

- `__init__.py` — `OpenBaoSource`, `register()`, `_bao_cmd`, `_do_approle_login`, `_read_secret_id_file`, `_query_secrets_list`, `_query_secret_value`, name helpers, watcher (`_start_watcher` / `_stop_watcher` / `_check_for_changes` / `_relogin`).
- `plugin.yaml` — manifest (`openbao 0.1.0`, standalone).
- `tests/test_openbao_plugin.py` — identity/conformance, auth flow, name helpers, namespace passthrough, KV v2 parsing, lease-aware auth, watcher lifecycle/self-heal, security properties. Run: `python -m pytest tests/ -v`.

## Reading the secrets

```sh
# First login using your vault
exec bao login -method=oidc -path=oidc role=human

# Get the role-id for hermes
ROLE_ID="$(bao read -format=json -namespace=m5 auth/approle/role/hermes/role-id | jq -r .data.role_id)"

# Read the secret-id from the file
SECRET_ID="$(cat ~/.hermes/auth/openbao-secret-id)"

# Fetch temp token
HERMES_TOKEN="$(bao write -format=json -namespace=m5 auth/approle/login role_id="$ROLE_ID" secret_id="$SECRET_ID" | jq -r .auth.client_token)"

# List all secrets in the hermes namespace
VAULT_TOKEN="$HERMES_TOKEN" bao list -namespace=m5 kv-dev/metadata/hermes/

# Get a specific secret
VAULT_TOKEN="$HERMES_TOKEN" bao kv get -namespace=m5 kv-dev/hermes/OPENAI_API_KEY

# Revoke the temp token
VAULT_TOKEN="$HERMES_TOKEN" bao token revoke -self

unset HERMES_TOKEN ROLE_ID SECRET_ID
```