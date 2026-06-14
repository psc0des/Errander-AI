# 59 — Automated Docker + Docker Compose + PostgreSQL Provisioning

## What was built and why

PostgreSQL is the only supported audit-DB backend (owner decision 2026-06-10,
"PostgreSQL-only" — see `decision_postgresql_only.md` memory). But nothing in
`scripts/bootstrap.sh` or `scripts/configure.sh` installed Docker or started
Postgres — the operator had to manually run `docker compose up -d` after
Step B, a command that itself requires Docker, which nothing installed.
SETUP.md repeated this manual instruction. On top of that,
`deploy/errander-agent.service` / `deploy/errander-web.service` declared
`After=network.target postgresql.service` — implying a native systemd
Postgres unit, which doesn't exist once Postgres runs as a Docker container.

This change makes the default "easiest path" fully automatic:

1. `bootstrap.sh` (Phase 1, sudo, fresh VM) now installs Docker Engine +
   the Compose plugin, enables `docker.service`, and adds the
   `errander-agent` service user to the `docker` group.
2. `configure.sh` (Phase 2, no sudo) brings up local PostgreSQL with
   `docker compose up -d --wait` automatically — but **only** when the
   operator keeps the documented default `ERRANDER_AUDIT_DB_URL`. Pointing
   `configure.sh` at an existing PostgreSQL server (any other URL) skips
   the docker-compose step entirely — "bring your own Postgres" remains a
   first-class path.
3. `docker-compose.yml`'s `postgres` service gets `restart: unless-stopped`
   so the container survives host reboots.
4. `deploy/errander-agent.service` / `deploy/errander-web.service`:
   `After=network.target postgresql.service` →
   `After=network.target docker.service` + `Requires=docker.service`.

This is an infra/setup-only change — no `errander/` Python code touched, no
fable.md §8 R-step involved.

## Key concepts

### `get.docker.com` — Docker's official convenience install script

```bash
step "6/8" "Docker + Docker Compose"

if command -v docker &>/dev/null && docker compose version &>/dev/null 2>&1; then
    ok "already installed  ($(docker --version | awk '{print $3}' | tr -d ','))"
else
    warn "not found — installing via get.docker.com..."
    curl -fsSL https://get.docker.com | sudo sh
    ok "Docker Engine + Compose plugin installed"
fi

sudo systemctl enable --now docker
ok "docker.service enabled and running"

if id -nG "$SERVICE_USER" | grep -qw docker; then
    ok "${SERVICE_USER} already in docker group"
else
    sudo usermod -aG docker "$SERVICE_USER"
    ok "${SERVICE_USER} added to docker group (takes effect on next login — Step B already re-logs in)"
fi
```

`get.docker.com` auto-detects Ubuntu/Debian/RHEL/CentOS/Fedora and installs
`docker-ce` + `docker-compose-plugin` (Compose v2, invoked as `docker
compose` — not the old standalone `docker-compose` binary). It's idempotent:
re-running it on a host that already has Docker is a clean no-op. This
matches the existing `curl | sh` pattern already used for `uv` in step 3 of
`bootstrap.sh`.

`set -euo pipefail` is active at the top of `bootstrap.sh`, so a failed
install on an unsupported distro aborts the whole script (fail-closed) — no
extra error handling needed, consistent with the existing `fail "Unsupported
distribution..."` pattern for the package-manager step.

### Step renumbering: 0/7 → 0/8

Inserting the new Docker step required renumbering every step label in
`bootstrap.sh`. The new step sits **after** step 5 (`errander-agent` user
creation — the new step needs `$SERVICE_USER` to exist for `usermod -aG`)
and **before** the web-service-user step:

| Old | New | Step |
|---|---|---|
| 0/7–5/7 | 0/8–5/8 | unchanged (distro, git, curl, uv, Python, agent user) |
| — | **6/8** | **NEW: Docker + Docker Compose** |
| 6/7 | 7/8 | web service user (`errander-web`) |
| 7/7 | 8/8 | clone repo |

### `docker compose up -d --wait` + `pg_isready` fallback

```bash
_default_db_url="postgresql://errander:errander@localhost:5432/errander"
if [ "$DB_URL" = "$_default_db_url" ]; then
    if command -v docker &>/dev/null && docker compose version &>/dev/null 2>&1; then
        echo "  Starting local PostgreSQL (docker compose)..."
        if docker compose up -d --wait 2>/dev/null; then
            ok "PostgreSQL ready at localhost:5432"
        else
            docker compose up -d
            warn "Waiting for PostgreSQL to become healthy..."
            for _i in $(seq 1 30); do
                docker compose exec -T postgres pg_isready -U errander -d errander &>/dev/null && break
                sleep 1
            done
            ok "PostgreSQL ready at localhost:5432"
        fi
    else
        warn "Docker not found — start PostgreSQL manually: docker compose up -d"
        warn "(re-run bootstrap.sh to install Docker automatically)"
    fi
fi
```

`docker compose up -d --wait` (Compose v2.17+, what `get.docker.com`
installs) blocks until the `postgres` service's healthcheck — already
defined in `docker-compose.yml` as `pg_isready -U errander -d errander`,
checked every 5s up to 10 times — reports healthy. Both branches are
idempotent: if the container is already running and healthy, `docker compose
up -d --wait` returns immediately.

The `--wait` flag is relatively new. A host that was bootstrapped *before*
this change might have an older Compose plugin without it. The `2>/dev/null
||` fallback re-runs plain `docker compose up -d` (which always works) and
then polls `pg_isready` directly for up to 30 seconds — using the exact same
healthcheck command Compose itself would have used.

### Default-URL gating — "bring your own Postgres" stays first-class

The auto-provisioning block only fires when `DB_URL` is **exactly**
`postgresql://errander:errander@localhost:5432/errander` — the documented
default that matches `docker-compose.yml`. If the operator types any other
URL (their own server, or even a customized localhost port/user), `configure.sh`
assumes they're managing Postgres themselves and skips the block entirely.
This keeps the behavior predictable: the only way to trigger automatic
container management is to *not change* the default prompt answer.

### `usermod -aG docker` and login-shell timing

`usermod -aG docker errander-agent` only takes effect for **new** login
sessions — a shell that's already open keeps its original group list. The
documented SETUP.md flow already does `sudo su - errander-agent` (a fresh
login shell) in Step B, *after* `bootstrap.sh` finishes. So by the time
`configure.sh` runs in Step 5 (inside that fresh shell), `errander-agent`
already has `docker` group membership — no extra re-login instruction
needed. See `tasks/lessons.md` (2026-06-14) for the general pattern: if a
future script changes group membership and a *later step in the same
session* needs it, that step needs an explicit `su -`/`newgrp`/`sg`.

### `restart: unless-stopped` + systemd `After=docker.service`

```yaml
services:
  postgres:
    image: postgres:16
    container_name: errander-postgres
    restart: unless-stopped
    environment:
      ...
```

Pairs with the systemd change in `deploy/errander-agent.service` /
`deploy/errander-web.service`:

```ini
After=network.target docker.service
Requires=docker.service
```

On boot: `docker.service` starts → Docker restarts the `errander-postgres`
container per its restart policy → `errander-agent.service` /
`errander-web.service` start *after* `docker.service` and find Postgres
already up. `Requires=` additionally means these units stop if
`docker.service` stops — correct, since the audit DB is now a dependency of
the Docker daemon being alive.

Previously these units declared `After=network.target postgresql.service` —
a leftover from before Postgres moved into Docker, implying a native systemd
`postgresql.service` that nothing in this repo installs.

## Gotchas

- **Only the exact default URL triggers auto-provisioning.** A typo'd or
  intentionally-customized localhost URL (e.g. a different port) silently
  skips the docker-compose step — `configure.sh` will then prompt-loop or
  fail later when nothing is listening. This is intentional (predictability
  over magic), but worth knowing when debugging "configure.sh didn't start
  my database."
- **`bash -n` syntax-checks shell scripts but does not execute them** — there
  is no real fresh Linux VM available in this environment, so the new
  `bootstrap.sh` step 6/8 and the `configure.sh` docker-compose block are
  verified for syntax and logic by inspection only, not end-to-end. The
  `docker compose config` check confirms `docker-compose.yml` still parses.
- **The one remaining `postgresql.service` match in SETUP.md** (in the
  `restartable_units` example for a *target VM*, around line 724) is
  unrelated — it's an example of an inventory entry for a VM that itself
  runs PostgreSQL as a managed service, not Errander's own audit DB. Don't
  "fix" it as part of this change.
- **`scripts/teardown.sh` deliberately does not touch Docker or the
  `errander-postgres` container/volume** — audit history survives teardown
  by design. SETUP.md documents `docker compose down -v` as the explicit
  opt-in to wipe local DB data.

## Code map

| Piece | Where |
|---|---|
| Docker install step | `scripts/bootstrap.sh` step 6/8 |
| Postgres auto-bring-up | `scripts/configure.sh`, after the `DB_URL="$REPLY"` line |
| Compose restart policy | `docker-compose.yml::services.postgres.restart` |
| systemd ordering fix | `deploy/errander-agent.service`, `deploy/errander-web.service` |
| Setup docs | `SETUP.md` Step 1, Step B, Step 5, "Starting fresh / teardown" |

## Quiz yourself

1. Why does the new Docker-install step have to run **after** step 5
   (`errander-agent` user creation) in `bootstrap.sh`, rather than earlier
   alongside git/curl/uv?
2. `configure.sh` checks `DB_URL` against an exact string before starting
   docker-compose. What would go wrong if it instead matched on "any
   `localhost:5432` URL" (e.g. allowing a different username/password)?
3. `docker compose up -d --wait` and the `pg_isready` fallback loop both end
   by printing `"PostgreSQL ready at localhost:5432"`. Why is it safe for
   both branches to print the same success message even though one used
   Compose's built-in healthcheck wait and the other polled manually?
4. The systemd change adds both `After=docker.service` and
   `Requires=docker.service`. What's the difference between the two, and
   what would break if only `After=` were added (no `Requires=`)?
5. Why does `bootstrap.sh` not need to add a `su - errander-agent` step after
   `usermod -aG docker`, even though group membership changes don't apply to
   already-open shells?
