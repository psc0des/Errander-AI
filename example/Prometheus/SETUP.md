# Prometheus Stack Setup Guide

**Services:** Prometheus · Grafana  
**Prerequisite:** Docker + Docker Compose are installed and running.

---

## Overview

```
Linux Target VMs              Agent VM (runs Docker)
─────────────────             ──────────────────────────────────
node_exporter :9100  ◄──────  Prometheus :9090  (scrapes metrics)
                              Grafana    :3000  (dashboards)
```

---

## Step 1 — Install `node_exporter` on Every Target Linux VM

Repeat this on **each VM** you want to monitor.

### Option A — Package manager (Ubuntu/Debian)

```bash
sudo apt update
sudo apt install -y prometheus-node-exporter
sudo systemctl enable --now prometheus-node-exporter
sudo systemctl status prometheus-node-exporter   # should show: active (running)
```

### Option B — Binary install (RHEL/CentOS/any distro)

```bash
NODE_EXPORTER_VERSION="1.8.1"
cd /tmp
curl -LO https://github.com/prometheus/node_exporter/releases/download/v${NODE_EXPORTER_VERSION}/node_exporter-${NODE_EXPORTER_VERSION}.linux-amd64.tar.gz
tar xzf node_exporter-${NODE_EXPORTER_VERSION}.linux-amd64.tar.gz
sudo mv node_exporter-${NODE_EXPORTER_VERSION}.linux-amd64/node_exporter /usr/local/bin/
sudo useradd -rs /bin/false node_exporter
```

Create the systemd unit:

```bash
sudo tee /etc/systemd/system/node_exporter.service > /dev/null <<EOF
[Unit]
Description=Prometheus Node Exporter
After=network.target

[Service]
User=node_exporter
ExecStart=/usr/local/bin/node_exporter
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now node_exporter
sudo systemctl status node_exporter   # should show: active (running)
```

### Verify node_exporter is reachable

```bash
curl http://<VM_IP>:9100/metrics | head -20
```

You should see lines like `node_cpu_seconds_total`, `node_memory_MemFree_bytes`, etc.

### Firewall — allow port 9100 from the agent VM only

```bash
# UFW (Ubuntu/Debian)
sudo ufw allow from <AGENT_VM_IP> to any port 9100

# firewalld (RHEL/CentOS)
sudo firewall-cmd --permanent --add-rich-rule='rule family="ipv4" source address="<AGENT_VM_IP>" port protocol="tcp" port="9100" accept'
sudo firewall-cmd --reload
```

---

## Step 2 — Configure `prometheus.yml`

Edit `prometheus.yml` and replace the placeholder IPs with your actual VM IPs:

```yaml
  - job_name: "linux_nodes"
    static_configs:
      - targets:
          - "10.0.0.11:9100"   # ← your real VM IPs
          - "10.0.0.12:9100"
          - "10.0.0.13:9100"
        labels:
          env: "prod"
```

Add or remove target entries as needed. You can also add multiple label groups
to tag VMs differently (e.g., `env: staging`).

---

## Step 3 — Configure `.env`

Open `.env` and set a strong Grafana admin password:

```env
GRAFANA_ADMIN_PASSWORD=YourStrongPasswordHere!
```

> **Do not commit `.env` to git.** It is already in `.gitignore`.

---

## Step 4 — Start the Stack

```bash
cd example/Prometheus
docker compose up -d
```

Check all containers are healthy:

```bash
docker compose ps
```

Expected output:

```
NAME         STATUS                   PORTS
prometheus   Up (healthy)             127.0.0.1:9090->9090/tcp
grafana      Up (healthy)             127.0.0.1:3000->3000/tcp
```

Wait ~30 seconds for health checks to pass on first boot.

---

## Step 5 — Verify Prometheus is Scraping

Open **http://localhost:9090/targets** in your browser.

- `prometheus` job → should show **UP**
- `linux_nodes` job → each VM should show **UP**

If a target shows **DOWN**, check:
1. `node_exporter` is running on that VM (`systemctl status node_exporter`)
2. Port 9100 is reachable from the agent VM (`curl http://<VM_IP>:9100/metrics`)
3. Firewall rules allow the connection

---

## Step 6 — Access Grafana

Open **http://localhost:3000**

- **Username:** `admin`
- **Password:** value from `GRAFANA_ADMIN_PASSWORD` in `.env`

### Add Prometheus as a data source

1. Go to **Connections → Data sources → Add new data source**
2. Select **Prometheus**
3. Set URL to: `http://prometheus:9090`  
   *(use the container name — they share the `demo` network)*
4. Click **Save & test** → should show "Successfully queried the Prometheus API"

### Import a Node Exporter dashboard

1. Go to **Dashboards → Import**
2. Enter dashboard ID: **`1860`** (Node Exporter Full — community favourite)
3. Select your Prometheus data source
4. Click **Import**

You now have full CPU, memory, disk, and network dashboards for all your Linux VMs.

---

## Step 7 — Stop the Stack

```bash
docker compose down          # stops containers, keeps volumes
docker compose down -v       # stops containers AND deletes all data volumes
```

---

## Adding Alerts Later

When you're ready to wire up Alertmanager:

1. Restore the `alertmanager` service in `docker-compose.yml` (kept in `alertmanager.yml`)
2. Add the `alerting:` block back to `prometheus.yml`
3. Create `rules/alert.rules.yml` with your alert rules
4. Re-add the `rule_files:` section to `prometheus.yml`

---

## Port Reference

| Service    | Host binding          | Purpose              |
|------------|-----------------------|----------------------|
| Prometheus | `127.0.0.1:9090`      | Metrics + query UI   |
| Grafana    | `127.0.0.1:3000`      | Dashboards           |
| node_exporter | `<VM_IP>:9100`     | Per-VM metrics (on target VMs, not this host) |
