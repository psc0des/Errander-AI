# Secrets Management

Errander-AI supports encrypting sensitive values at rest using Fernet
(AES-128-CBC + HMAC-SHA256). Encrypted values are stored as `enc:v1:<ciphertext>`
and decrypted transparently at load time.

---

## Why encrypt at rest?

**Threat model:** Your `.env` file and `settings.yaml` may be read by:
- Other processes on the agent VM
- Backup systems that copy files without scrubbing
- Developers who accidentally commit `.env` to git

Encrypting secrets means a compromised file reveals `enc:v1:...` blobs,
not live API keys or tokens.

This is not a replacement for proper access controls — use file permissions
(`chmod 600 .env`), secrets managers (Vault), and git hooks to prevent
accidental commits. Encryption is a defence-in-depth layer.

---

## Setup

### 1. Generate a master key

```bash
uv run python -m errander --generate-secrets-key
```

Output:
```
ERRANDER_SECRETS_KEY=your-44-char-base64-key-here

Save this in a 0600-permissioned EnvironmentFile or your secrets manager.
Never commit it to git. Losing this key means losing all encrypted values.
```

Store the key securely — for example, in a systemd `EnvironmentFile` with
`0600` permissions:

```bash
# /etc/errander/secrets (chmod 600, owned by errander user)
ERRANDER_SECRETS_KEY=your-44-char-base64-key-here
```

Reference it in your systemd unit:
```ini
[Service]
EnvironmentFile=/etc/errander/secrets
EnvironmentFile=/home/errander/errander/.env
```

### 2. Encrypt a secret

```bash
ERRANDER_SECRETS_KEY=your-key uv run python -m errander --encrypt "xoxb-your-slack-token"
# → enc:v1:gAAAAA...
```

### 3. Use encrypted values in `.env` or `settings.yaml`

In `.env`:
```bash
ERRANDER_SLACK_BOT_TOKEN=enc:v1:gAAAAA...
ERRANDER_LLM_API_KEY=enc:v1:gBBBBB...
ERRANDER_UI_PASSWORD=enc:v1:gCCCCC...   # Web UI password
ERRANDER_ELK_API_KEY=enc:v1:gDDDDD...   # Elasticsearch API key (if auth enabled)
ERRANDER_SIGNING_SECRET=enc:v1:gEEEEE... # HMAC key for signed approval URLs
ERRANDER_SECRETS_KEY=your-44-char-key   # this one stays plaintext
```

In `settings.yaml`:
```yaml
llm:
  model: "enc:v1:gCCCCC..."    # encrypting model name is unusual but supported
  api_key: "enc:v1:gDDDDD..."
```

Plaintext values always work — encryption is optional per-field.

---

## Key rotation (old key still available)

Use this path when you want to rotate the master key proactively — you still
have the old key and can decrypt existing ciphertexts.

**Step 1 — stop the agent**
```bash
sudo systemctl stop errander
```

**Step 2 — decrypt all current encrypted values using the old key**

For each `enc:v1:` blob in your `.env` and `settings.yaml`, run:
```bash
ERRANDER_SECRETS_KEY=<old-key> uv run python -m errander --decrypt "enc:v1:gAAAAA..."
# → xoxb-your-slack-token
```
Note the plaintext for every encrypted variable.

**Step 3 — generate a new key**
```bash
uv run python -m errander --generate-secrets-key
# → ERRANDER_SECRETS_KEY=<new-key>
```

**Step 4 — re-encrypt every value with the new key**
```bash
ERRANDER_SECRETS_KEY=<new-key> uv run python -m errander --encrypt "xoxb-your-slack-token"
# → enc:v1:hBBBBB...
```
Repeat for each secret.

**Step 5 — update your config files**

Replace every old `enc:v1:...` blob in `.env` and `settings.yaml` with the new
ciphertexts, and update `ERRANDER_SECRETS_KEY` to the new key.

**Step 6 — restart the agent and verify**
```bash
sudo systemctl start errander
uv run python -m errander --check-llm    # verify LLM key decrypts correctly
```

There is no automated rotation tooling yet — it is deferred to the v2 Vault integration.

---

## Key rotation (master key lost)

If `ERRANDER_SECRETS_KEY` is lost, all `enc:v1:` ciphertexts are
**permanently unrecoverable** — Fernet provides no backdoor. You must treat
every encrypted secret as compromised and rotate it at the source.

**Step 1 — stop the agent immediately**
```bash
sudo systemctl stop errander
```

**Step 2 — revoke and reissue every encrypted secret**

| Variable | Where to rotate |
|---|---|
| `ERRANDER_SLACK_BOT_TOKEN` | Slack → Your App → OAuth & Permissions → Regenerate token |
| `ERRANDER_LLM_API_KEY` | Your LLM provider's API key dashboard |
| `ERRANDER_UI_PASSWORD` | Choose a new password (it is yours to set) |
| `ERRANDER_ELK_API_KEY` | Elasticsearch → Stack Management → API Keys → Invalidate + create new |
| `ERRANDER_SIGNING_SECRET` | Generate new: `head -c 32 /dev/urandom \| base64` — invalidates all in-flight approval URLs |

Any other `enc:v1:` values in your `.env` or `settings.yaml` must also be
treated as unknown — rotate them too.

**Step 3 — generate a new master key**
```bash
uv run python -m errander --generate-secrets-key
# → ERRANDER_SECRETS_KEY=<new-key>
```

**Step 4 — encrypt the new secrets with the new key**
```bash
ERRANDER_SECRETS_KEY=<new-key> uv run python -m errander --encrypt "xoxb-new-slack-token"
ERRANDER_SECRETS_KEY=<new-key> uv run python -m errander --encrypt "sk-new-llm-key"
ERRANDER_SECRETS_KEY=<new-key> uv run python -m errander --encrypt "my-new-ui-password"
```

**Step 5 — replace all config values and restart**

Update `.env` with new `enc:v1:` blobs and the new `ERRANDER_SECRETS_KEY`,
then restart:
```bash
sudo systemctl start errander
uv run python -m errander --check-llm
```

**Prevention:** back up `ERRANDER_SECRETS_KEY` in a password manager or
secrets vault alongside your other infrastructure credentials. Losing it
requires rotating every secret it protected.

---

## Scope

Only secrets benefit from encryption. Operational config (model IDs, timeouts,
hostnames, ssh usernames) stays plaintext for debuggability. The agent never
encrypts data in the PostgreSQL audit trail — use filesystem encryption (LUKS,
dm-crypt) if the audit DB contains sensitive detail.

### Notes on specific secrets

**`ERRANDER_LLM_API_KEY`** — passed as a Bearer token to the OpenAI-compatible
endpoint. Decrypted at startup by `settings.py`; handed to the OpenAI SDK.

**`ERRANDER_UI_PASSWORD`** — decrypted at startup and held in memory as
plaintext. On every `/ui/*` request the server compares the provided password
against this value using `secrets.compare_digest()` (timing-safe). The password
is never hashed at rest — encryption via `enc:v1:` is its protection at rest.
If you set `ERRANDER_UI_USER` without `ERRANDER_UI_PASSWORD` (or vice versa),
Basic Auth is disabled entirely and a warning is logged on startup.

**`ERRANDER_SIGNING_SECRET`** — HMAC-SHA256 key for signed web-approval URLs
issued by `docker_hygiene` (v1.1). The agent embeds a signed token in the
Slack approval message; the web approval route verifies the token before
accepting the operator's selection. Used by
`errander.integrations.signed_url.{make,verify}_signed_token`.

**Auto-generated by `configure.sh`** using `secrets.token_bytes(32)` — you do
not need to generate this manually. To rotate it manually:

```bash
head -c 32 /dev/urandom | base64
```

The signer fails loud (`SigningSecretMissingError`) when the env var is unset
— it will not auto-generate an ephemeral secret, because that would silently
disable signature verification. If the secret is rotated, in-flight signed
URLs are immediately invalidated (intentional: rotation means revocation).

Status as of 2026-05-22: fully live. Session 2b-iii wired batch orchestration —
the agent now mints signed URLs during maintenance runs and embeds them in Slack
approval messages. The web approval routes (`/ui/docker-hygiene/approve`) verify
the token on every GET and POST.

**`ERRANDER_WEB_BASE_URL`** — externally-reachable base URL for the agent VM's
web UI, e.g. `http://10.0.0.5:9090`. Used by `_run_docker_hygiene` to build the
signed web-approval URL that is included in Slack messages. Optional: when empty,
the web-approval URL is omitted from Slack messages and operators approve via
Slack structured reply only. Does not affect the web routes themselves — those
remain reachable regardless of this setting.

**Auto-detected by `configure.sh`** from the VM's primary private IP
(`hostname -I`). Override manually in `.env` if the agent VM is behind a NAT,
load balancer, or you want to use a hostname instead of an IP.
