#!/bin/bash
# Install Errander-AI docker wrapper scripts on a target VM.
# Run as root: sudo bash install-docker-wrappers.sh
# Idempotent — re-running overwrites cleanly without error.
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "ERROR: must run as root (sudo bash $0)" >&2; exit 1; }

# --- errander-docker-assess ---
cat > /usr/local/sbin/errander-docker-assess << 'WRAPPER_EOF'
#!/bin/bash
set -euo pipefail

if [ "${1:-}" = "--check" ]; then
    echo "ok"
    exit 0
fi

if ! /usr/bin/docker info >/dev/null 2>&1; then
    echo "reachable=no"
    echo "error=docker daemon not reachable"
    exit 0
fi

echo "reachable=yes"
dangling=$(/usr/bin/docker images -f dangling=true -q 2>/dev/null | wc -l)
echo "dangling_images=${dangling}"
stopped=$(/usr/bin/docker ps -a -f status=exited -q 2>/dev/null | wc -l)
echo "stopped_containers=${stopped}"
echo "error="
echo "system_df_begin"
/usr/bin/docker system df 2>/dev/null || true
echo "system_df_end"
WRAPPER_EOF

# --- errander-docker-prune-safe ---
cat > /usr/local/sbin/errander-docker-prune-safe << 'WRAPPER_EOF'
#!/bin/bash
set -euo pipefail

if [ "${1:-}" = "--check" ]; then
    echo "ok"
    exit 0
fi

# Dangling images + stopped containers only. No -a flag.
/usr/bin/docker image prune -f 2>&1
/usr/bin/docker container prune -f 2>&1
WRAPPER_EOF

# --- errander-docker-prune-aggressive ---
cat > /usr/local/sbin/errander-docker-prune-aggressive << 'WRAPPER_EOF'
#!/bin/bash
set -euo pipefail

if [ "${1:-}" = "--check" ]; then
    echo "ok"
    exit 0
fi

# Full system prune including ALL unused images.
# Only used when aggressive=true is approved in the maintenance plan.
/usr/bin/docker system prune -af 2>&1
WRAPPER_EOF

chmod 755 /usr/local/sbin/errander-docker-assess \
          /usr/local/sbin/errander-docker-prune-safe \
          /usr/local/sbin/errander-docker-prune-aggressive
chown root:root /usr/local/sbin/errander-docker-assess \
                /usr/local/sbin/errander-docker-prune-safe \
                /usr/local/sbin/errander-docker-prune-aggressive

# --- sudoers entry ---
cat > /etc/sudoers.d/errander-docker << 'SUDOERS_EOF'
errander ALL=(root) NOPASSWD: \
  /usr/local/sbin/errander-docker-assess, \
  /usr/local/sbin/errander-docker-prune-safe, \
  /usr/local/sbin/errander-docker-prune-aggressive
SUDOERS_EOF
chmod 440 /etc/sudoers.d/errander-docker

if ! visudo -c -f /etc/sudoers.d/errander-docker >/dev/null 2>&1; then
    echo "ERROR: sudoers validation failed — removing /etc/sudoers.d/errander-docker" >&2
    rm -f /etc/sudoers.d/errander-docker
    exit 1
fi

echo "Wrapper install complete. Run \`uv run python -m errander --check-targets <env>\` from the controller to verify."
