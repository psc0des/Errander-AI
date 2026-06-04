# ELK Stack Setup Guide

**Services:** Elasticsearch · Kibana · Logstash  
**Prerequisite:** Docker + Docker Compose are installed and running.

---

## Overview

```
Linux Target VMs                   Agent VM (runs Docker)
─────────────────                  ────────────────────────────────
Filebeat (ships logs) ──► :5044 ►  Logstash   (parse & enrich)
                                        │
                                        ▼
                                   Elasticsearch :9200  (index & store)
                                   Kibana        :5601  (search & visualise)
```

---

## Step 1 — System Prerequisite: Increase `vm.max_map_count`

Elasticsearch requires a higher virtual memory map limit than Linux defaults.
Run this on the **agent VM** (the host running Docker):

```bash
# Apply immediately (resets on reboot)
sudo sysctl -w vm.max_map_count=262144

# Make permanent across reboots
echo "vm.max_map_count=262144" | sudo tee -a /etc/sysctl.conf
sudo sysctl -p
```

> **Skipping this step will cause Elasticsearch to crash** with an error like:
> `max virtual memory areas vm.max_map_count [65530] is too low`

---

## Step 2 — Configure `.env`

Open `.env` and set strong passwords. These are used at first boot to initialise
the cluster — **changing them after first boot requires manual ES API calls**.

```env
ELASTIC_PASSWORD=YourStrongElasticPassword!
KIBANA_PASSWORD=YourStrongKibanaPassword!
KIBANA_ENCRYPTION_KEY=<32-char-random-string>
```

Generate a secure encryption key:

```bash
# Linux/macOS
openssl rand -base64 32

# Or use any 32+ character random string
```

> **Do not commit `.env` to git.** It is already in `.gitignore`.

---

## Step 3 — Start the Stack

```bash
cd example/ELK
docker compose up -d
```

The startup sequence is:

1. **`elasticsearch`** starts and becomes healthy (~60 seconds)
2. **`setup`** runs once — sets the `kibana_system` user password — then exits
3. **`kibana`** and **`logstash`** start after setup completes (~60 more seconds)

Monitor startup progress:

```bash
docker compose logs -f
```

Check all containers are in the expected state:

```bash
docker compose ps
```

Expected output:

```
NAME            STATUS                    PORTS
elasticsearch   Up (healthy)              127.0.0.1:9200->9200/tcp
setup           Exited (0)                —
kibana          Up (healthy)              127.0.0.1:5601->5601/tcp
logstash        Up (healthy)              127.0.0.1:5044->5044/tcp
```

> `setup` exiting with code 0 is **normal and expected** — it's a one-shot init container.

---

## Step 4 — Verify Elasticsearch

```bash
curl -u elastic:${ELASTIC_PASSWORD} http://localhost:9200/_cluster/health?pretty
```

Expected: `"status": "green"` (or `"yellow"` on single-node — both are fine).

---

## Step 5 — Access Kibana

Open **http://localhost:5601**

- **Username:** `elastic`
- **Password:** value of `ELASTIC_PASSWORD` from `.env`

On first login you may see a setup wizard — you can skip it and go straight to **Discover**.

---

## Step 6 — Install Filebeat on Target Linux VMs

Filebeat ships logs from your Linux VMs to Logstash. Repeat on **each VM**.

### Install Filebeat

```bash
# Ubuntu/Debian
curl -fsSL https://artifacts.elastic.co/GPG-KEY-elasticsearch | sudo gpg --dearmor -o /usr/share/keyrings/elastic.gpg
echo "deb [signed-by=/usr/share/keyrings/elastic.gpg] https://artifacts.elastic.co/packages/8.x/apt stable main" | sudo tee /etc/apt/sources.list.d/elastic-8.x.list
sudo apt update && sudo apt install -y filebeat

# RHEL/CentOS
sudo rpm --import https://artifacts.elastic.co/GPG-KEY-elasticsearch
sudo tee /etc/yum.repos.d/elastic.repo > /dev/null <<EOF
[elasticsearch]
name=Elasticsearch repository for 8.x packages
baseurl=https://artifacts.elastic.co/packages/8.x/yum
gpgcheck=1
gpgkey=https://artifacts.elastic.co/GPG-KEY-elasticsearch
enabled=1
autorefresh=1
type=rpm-md
EOF
sudo yum install -y filebeat
```

### Configure Filebeat

Edit `/etc/filebeat/filebeat.yml`:

```yaml
filebeat.inputs:
  - type: log
    enabled: true
    paths:
      - /var/log/*.log
      - /var/log/syslog
    fields:
      host_env: prod        # tag your VM
    fields_under_root: true

output.logstash:
  hosts: ["<AGENT_VM_IP>:5044"]   # ← IP of the VM running Docker

# Disable Elasticsearch direct output (we use Logstash)
# output.elasticsearch is commented out by default
```

> Replace `<AGENT_VM_IP>` with the private IP of the machine running your ELK Docker stack.

### Enable and start Filebeat

```bash
sudo systemctl enable --now filebeat
sudo systemctl status filebeat   # should show: active (running)
```

### Verify Filebeat is shipping logs

```bash
sudo filebeat test output   # should show: connection successful
sudo journalctl -u filebeat -f   # watch live log output
```

---

## Step 7 — Create an Index Pattern in Kibana

1. Open Kibana → **Stack Management → Index Patterns**
2. Click **Create index pattern**
3. Name: `logstash-*`
4. Time field: `@timestamp`
5. Click **Create index pattern**

Now go to **Discover** and you should see log events flowing in.

---

## Step 8 — Stop the Stack

```bash
docker compose down          # stops containers, keeps volumes
docker compose down -v       # stops containers AND deletes all data volumes
```

> ⚠️ `docker compose down -v` will **permanently delete** all indexed log data and Kibana saved objects.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Elasticsearch crashes immediately | `vm.max_map_count` too low | Step 1 |
| `setup` container loops / never exits | Wrong `ELASTIC_PASSWORD` in `.env` | Check `.env`, recreate: `docker compose down -v && docker compose up -d` |
| Kibana shows "Kibana server is not ready" | ES still starting | Wait 2–3 minutes, refresh |
| No logs in Discover | Filebeat not running or wrong Logstash IP | `systemctl status filebeat`, check `filebeat.yml` output host |
| Logstash unhealthy | Pipeline config error in `logstash.conf` | `docker compose logs logstash` |

---

## Port Reference

| Service       | Host binding        | Purpose                          |
|---------------|---------------------|----------------------------------|
| Elasticsearch | `127.0.0.1:9200`    | REST API + data storage          |
| Kibana        | `127.0.0.1:5601`    | Web UI — search & visualise logs |
| Logstash      | `127.0.0.1:5044`    | Beats input (receives Filebeat)  |
