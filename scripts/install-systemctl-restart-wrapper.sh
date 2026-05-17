#!/bin/bash
# Install Errander-AI systemctl-restart wrapper on a target VM.
# Usage: sudo bash install-systemctl-restart-wrapper.sh <unit1> [unit2] ...
# Example: sudo bash install-systemctl-restart-wrapper.sh nginx gunicorn redis-server
# Idempotent — re-running overwrites wrapper and allowlist cleanly without error.
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "ERROR: must run as root (sudo bash $0)" >&2; exit 1; }

if [[ $# -eq 0 ]]; then
    echo "ERROR: at least one unit name required" >&2
    echo "Usage: sudo bash $0 <unit1> [unit2] ..." >&2
    exit 1
fi

# --- errander-systemctl-restart wrapper ---
cat > /usr/local/sbin/errander-systemctl-restart << 'WRAPPER_EOF'
#!/bin/bash
set -euo pipefail

if [ "${1:-}" = "--check" ]; then
    echo "ok"
    exit 0
fi

if [ "${1:-}" = "--snapshot-only" ]; then
    UNIT="${2:-}"
    if [ -z "$UNIT" ]; then
        echo "ERROR: no unit specified" >&2
        exit 2
    fi
    echo "pre_status_begin"
    /bin/systemctl status "$UNIT" --no-pager 2>&1 || true
    echo "pre_status_end"
    echo "pre_journal_begin"
    /bin/journalctl -u "$UNIT" --since "5 minutes ago" --no-pager 2>&1 || true
    echo "pre_journal_end"
    exit 0
fi

UNIT="${1:-}"
if [ -z "$UNIT" ]; then
    echo "ERROR: no unit specified" >&2
    exit 2
fi

ALLOWLIST="/etc/errander/restart-allowlist"
if [ ! -r "$ALLOWLIST" ]; then
    echo "ERROR: allowlist $ALLOWLIST not readable" >&2
    exit 3
fi

if ! grep -qFx "$UNIT" "$ALLOWLIST"; then
    echo "ERROR: unit '$UNIT' not in allowlist" >&2
    exit 4
fi

echo "pre_status_begin"
/bin/systemctl status "$UNIT" --no-pager 2>&1 || true
echo "pre_status_end"

echo "pre_journal_begin"
/bin/journalctl -u "$UNIT" --since "5 minutes ago" --no-pager 2>&1 || true
echo "pre_journal_end"

/bin/systemctl restart "$UNIT"

sleep 2

echo "post_active_begin"
/bin/systemctl is-active "$UNIT" 2>&1 || true
echo "post_active_end"

echo "post_status_begin"
/bin/systemctl status "$UNIT" --no-pager 2>&1 || true
echo "post_status_end"

echo "post_journal_begin"
/bin/journalctl -u "$UNIT" --since "10 seconds ago" --no-pager 2>&1 || true
echo "post_journal_end"
WRAPPER_EOF

chmod 755 /usr/local/sbin/errander-systemctl-restart
chown root:root /usr/local/sbin/errander-systemctl-restart

# --- allowlist ---
mkdir -p /etc/errander
printf '%s\n' "$@" > /etc/errander/restart-allowlist
chmod 644 /etc/errander/restart-allowlist
chown root:root /etc/errander/restart-allowlist

# --- sudoers entry ---
cat > /etc/sudoers.d/errander-systemctl-restart << 'SUDOERS_EOF'
errander ALL=(root) NOPASSWD: /usr/local/sbin/errander-systemctl-restart
SUDOERS_EOF
chmod 440 /etc/sudoers.d/errander-systemctl-restart

if ! visudo -c -f /etc/sudoers.d/errander-systemctl-restart >/dev/null 2>&1; then
    echo "ERROR: sudoers validation failed — removing /etc/sudoers.d/errander-systemctl-restart" >&2
    rm -f /etc/sudoers.d/errander-systemctl-restart
    exit 1
fi

echo "Wrapper install complete. Allowlist: $*"
echo "Run \`uv run python -m errander --check-targets <env>\` from the controller to verify."
