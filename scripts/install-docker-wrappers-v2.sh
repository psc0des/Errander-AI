#!/bin/bash
# Install Errander-AI docker_hygiene wrapper scripts on a target VM.
#
# v1.1 — replaces the legacy docker_prune wrappers (errander-docker-assess,
# errander-docker-prune-safe, errander-docker-prune-aggressive). This file
# does NOT remove the legacy wrappers — that happens in v1.1 Session 3 when
# docker_prune is deleted from the codebase. Both sets can safely coexist
# in /usr/local/sbin/ during the transition.
#
# Run as root: sudo bash install-docker-wrappers-v2.sh
# Idempotent — re-running overwrites cleanly without error.
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "ERROR: must run as root (sudo bash $0)" >&2; exit 1; }

# --- errander-docker-assess-v2 ---
# Emits structured findings across 5 resource classes.
# Output format is parsed by errander/agent/subgraphs/docker_hygiene.py
# (parse_assess_v2_output). Schema MUST stay in sync with the parser.
cat > /usr/local/sbin/errander-docker-assess-v2 << 'WRAPPER_EOF'
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
echo "error="
echo "docker_hygiene_begin"

now_epoch=$(date +%s)

# --- class=image_dangling ---
echo "class=image_dangling"
# Format: ID|CreatedAt|Size  (CreatedAt is RFC3339-ish)
/usr/bin/docker images --filter dangling=true \
    --format '{{.ID}}|{{.CreatedAt}}|{{.Size}}' 2>/dev/null \
    | while IFS='|' read -r img_id created_at size_human; do
        [ -z "$img_id" ] && continue
        # docker doesn't give us bytes here; we'll resolve via inspect below
        size_bytes=$(/usr/bin/docker image inspect "$img_id" \
            --format '{{.Size}}' 2>/dev/null || echo 0)
        created_epoch=$(date -d "$created_at" +%s 2>/dev/null || echo "$now_epoch")
        age_days=$(( (now_epoch - created_epoch) / 86400 ))
        echo "  id=${img_id} size_bytes=${size_bytes} age_days=${age_days} last_tag=<none>"
    done

# --- class=image_unused ---
# Unused = not currently referenced by any container (dangling = a subset where
# the image also has no tag). We exclude dangling here to avoid double-counting.
# docker images --filter dangling=false includes BOTH used and unused. We
# enumerate via image ls + cross-check against `docker ps -a --format '{{.Image}}'`.
echo "class=image_unused"
referenced_images=$(/usr/bin/docker ps -a --format '{{.Image}}' 2>/dev/null | sort -u || true)
/usr/bin/docker images --filter dangling=false \
    --format '{{.ID}}|{{.Repository}}:{{.Tag}}|{{.CreatedAt}}' 2>/dev/null \
    | while IFS='|' read -r img_id repo_tag created_at; do
        [ -z "$img_id" ] && continue
        # Skip if any container references this image (by id, repo:tag, or repo only)
        if echo "$referenced_images" | grep -qFx "$img_id"; then
            continue
        fi
        if echo "$referenced_images" | grep -qFx "$repo_tag"; then
            continue
        fi
        repo_only="${repo_tag%:*}"
        if echo "$referenced_images" | grep -qFx "$repo_only"; then
            continue
        fi
        size_bytes=$(/usr/bin/docker image inspect "$img_id" \
            --format '{{.Size}}' 2>/dev/null || echo 0)
        created_epoch=$(date -d "$created_at" +%s 2>/dev/null || echo "$now_epoch")
        age_days=$(( (now_epoch - created_epoch) / 86400 ))
        echo "  id=${img_id} size_bytes=${size_bytes} age_days=${age_days} last_tag=${repo_tag}"
    done

# --- class=container_stopped ---
echo "class=container_stopped"
/usr/bin/docker ps -a --filter status=exited \
    --format '{{.ID}}|{{.Names}}|{{.Status}}' 2>/dev/null \
    | while IFS='|' read -r cont_id cont_name status_str; do
        [ -z "$cont_id" ] && continue
        # Status string examples: "Exited (0) 12 days ago", "Exited (137) 2 hours ago"
        exit_code=$(echo "$status_str" | sed -n 's/.*Exited (\([0-9]\+\)).*/\1/p')
        [ -z "$exit_code" ] && exit_code=0
        finished_at=$(/usr/bin/docker inspect "$cont_id" \
            --format '{{.State.FinishedAt}}' 2>/dev/null || echo "")
        if [ -n "$finished_at" ] && [ "$finished_at" != "0001-01-01T00:00:00Z" ]; then
            finished_epoch=$(date -d "$finished_at" +%s 2>/dev/null || echo "$now_epoch")
            stopped_age_hours=$(( (now_epoch - finished_epoch) / 3600 ))
        else
            stopped_age_hours=0
        fi
        echo "  id=${cont_id} name=${cont_name} exit_code=${exit_code} stopped_age_hours=${stopped_age_hours}"
    done

# --- class=volume_unreferenced ---
echo "class=volume_unreferenced"
# Volumes not attached to any container. Docker provides this filter directly.
/usr/bin/docker volume ls --filter dangling=true --format '{{.Name}}' 2>/dev/null \
    | while read -r vol_name; do
        [ -z "$vol_name" ] && continue
        # Size: docker doesn't expose volume size cheaply. We use du on the mountpoint
        # if accessible; otherwise emit 0 (operator sees "unknown size" in UI).
        mountpoint=$(/usr/bin/docker volume inspect "$vol_name" \
            --format '{{.Mountpoint}}' 2>/dev/null || echo "")
        if [ -n "$mountpoint" ] && [ -d "$mountpoint" ]; then
            size_bytes=$(du -sb "$mountpoint" 2>/dev/null | awk '{print $1}' || echo 0)
        else
            size_bytes=0
        fi
        # Volume last-mount tracking: docker doesn't expose this. Use mountpoint
        # mtime as a proxy — last time any file inside changed.
        if [ -n "$mountpoint" ] && [ -d "$mountpoint" ]; then
            mtime_epoch=$(stat -c %Y "$mountpoint" 2>/dev/null || echo "$now_epoch")
            last_mount_days=$(( (now_epoch - mtime_epoch) / 86400 ))
        else
            last_mount_days=0
        fi
        echo "  name=${vol_name} size_bytes=${size_bytes} last_mount_days=${last_mount_days}"
    done

# --- class=build_cache ---
echo "class=build_cache"
# docker buildx du --verbose gives reclaimable bytes; fall back to system df.
reclaimable=$(/usr/bin/docker system df --format '{{.Type}}|{{.Reclaimable}}' 2>/dev/null \
    | awk -F'|' '$1 == "Build Cache" {print $2}')
if [ -n "$reclaimable" ]; then
    # "Reclaimable" comes back as a human string like "1.234GB (100%)". Strip and convert.
    reclaim_num=$(echo "$reclaimable" | sed -n 's/^\([0-9.]*\).*/\1/p')
    reclaim_unit=$(echo "$reclaimable" | sed -n 's/^[0-9.]*\([A-Za-z]*\).*/\1/p')
    case "$reclaim_unit" in
        B|"")   mult=1 ;;
        kB|KB)  mult=1000 ;;
        MB)     mult=1000000 ;;
        GB)     mult=1000000000 ;;
        TB)     mult=1000000000000 ;;
        *)      mult=1 ;;
    esac
    reclaimable_bytes=$(awk -v n="$reclaim_num" -v m="$mult" 'BEGIN {printf "%.0f", n*m}')
    [ -n "$reclaimable_bytes" ] && [ "$reclaimable_bytes" != "0" ] \
        && echo "  reclaimable_bytes=${reclaimable_bytes}"
fi

echo "docker_hygiene_end"
WRAPPER_EOF

# --- errander-docker-remove-v2 (Session 1: stub only) ---
# This wrapper is referenced by docker_hygiene.MANIFEST.required_wrappers so
# --check-targets surfaces missing installs. The real remove logic (per-object
# allowlist + re-validation + per-object audit) lands in Session 2. The stub
# returns a clear error if invoked prematurely.
cat > /usr/local/sbin/errander-docker-remove-v2 << 'WRAPPER_EOF'
#!/bin/bash
set -euo pipefail

if [ "${1:-}" = "--check" ]; then
    echo "ok"
    exit 0
fi

echo "ERROR: errander-docker-remove-v2 is a Session 1 stub." >&2
echo "       Per-object removal lands in v1.1 Session 2." >&2
exit 64
WRAPPER_EOF

chmod 755 /usr/local/sbin/errander-docker-assess-v2 \
          /usr/local/sbin/errander-docker-remove-v2
chown root:root /usr/local/sbin/errander-docker-assess-v2 \
                /usr/local/sbin/errander-docker-remove-v2

# --- sudoers entry ---
# Narrow grant: errander user can invoke only these two wrappers as root.
# Coexists with /etc/sudoers.d/errander-docker (legacy docker_prune wrappers)
# during the v1.1 transition.
cat > /etc/sudoers.d/errander-docker-hygiene << 'SUDOERS_EOF'
errander ALL=(root) NOPASSWD: \
  /usr/local/sbin/errander-docker-assess-v2, \
  /usr/local/sbin/errander-docker-remove-v2
SUDOERS_EOF
chmod 440 /etc/sudoers.d/errander-docker-hygiene

echo "Installed docker_hygiene wrappers:"
echo "  /usr/local/sbin/errander-docker-assess-v2"
echo "  /usr/local/sbin/errander-docker-remove-v2 (Session 1 stub)"
echo "  /etc/sudoers.d/errander-docker-hygiene"
