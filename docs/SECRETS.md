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

## Key rotation

The `v1:` prefix in ciphertext enables future key rotation:

1. Generate a new key: `--generate-secrets-key`
2. Re-encrypt each value with the new key: `--encrypt <plaintext>`
3. Replace `enc:v1:...` blobs in your config
4. Update `ERRANDER_SECRETS_KEY`

There is no automated rotation tooling yet — it is deferred to the v2 Vault integration.

---

## What happens if the master key is lost?

All values encrypted with that key are **permanently unrecoverable**. You must:
1. Rotate all affected secrets (Slack tokens, API keys, etc.)
2. Re-encrypt with a new key

Recommendation: back up `ERRANDER_SECRETS_KEY` alongside your infrastructure
credentials in a password manager or secrets vault.

---

## Scope

Only secrets benefit from encryption. Operational config (model IDs, timeouts,
hostnames, ssh usernames) stays plaintext for debuggability. The agent never
encrypts data in the SQLite audit trail — use filesystem encryption (LUKS,
dm-crypt) if the audit DB contains sensitive detail.
