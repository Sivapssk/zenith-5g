# Troubleshooting Guide — ZENITH-5G

Common issues encountered when setting up or migrating the testbed to a new machine.

---

## 1. Grafana Dashboard Shows "No Data" / Warning Triangles

### Root Cause
The dashboard JSON contains a hardcoded Grafana datasource UID from the machine it was originally created on. Every Grafana installation assigns a different UID when you add a datasource.

### Fix

**Step 1 — Add Prometheus as a datasource in Grafana**
- Go to `Connections → Data sources → Add new data source → Prometheus`
- Set URL to `http://localhost:9090`
- Click **Save & Test** — confirm it shows green

**Step 2 — Get the new UID**
After saving, check the browser URL bar:
```
http://localhost:3000/connections/datasources/edit/XXXXXXXXXXXXXXX
```
Copy the UID (the string after `/edit/`).

**Step 3 — Update the dashboard JSON**
Run this command (replace `YOUR_NEW_UID` with what you copied):
```bash
sed -i 's/<OLD_UID>/YOUR_NEW_UID/g' grafana/dashboard.json
```
Or find and replace all occurrences of the old UID in a text editor.

**Step 4 — Re-import the updated JSON**
Dashboards → Import → upload the modified file.

---

## 2. Prometheus Not Scraping Exporter (Port Mismatch)

### Root Cause
The Python exporter and the Prometheus scrape config are using different port numbers.

### Fix
Ensure both use the same port. The standard for this project is **8001**.

**Python script (`test_4_addlat.py`):**
```python
start_http_server(8001)
```

**Prometheus config (`/etc/prometheus/prometheus.yml`):**
```yaml
scrape_configs:
  - job_name: 'ztn_adm_demo'
    scrape_interval: 5s
    static_configs:
      - targets: ['localhost:8001']
```

After editing prometheus.yml:
```bash
sudo systemctl restart prometheus
```

### Verify
```bash
# Check the exporter is serving metrics
curl localhost:8001/metrics | grep ztn_

# Check Prometheus can see the target as UP
# Open in browser:
http://localhost:9090/targets
```

---

## 3. Demo Launcher Asks for sudo Password (gNB / UE)

### Root Cause
`nr-softmodem` and `nr-uesoftmodem` require sudo, and the launcher cannot handle an interactive password prompt.

### Fix
Configure passwordless sudo for these two binaries only.

```bash
sudo visudo
```

Add at the bottom (replace `<username>` with your actual Linux username, e.g. `iiitb-101`):
```
<username> ALL=(ALL) NOPASSWD: \
  /home/<username>/openairinterface5g/cmake_targets/ran_build/build/nr-softmodem, \
  /home/<username>/openairinterface5g/cmake_targets/ran_build/build/nr-uesoftmodem
```

Save and exit. Test it:
```bash
sudo /home/<username>/openairinterface5g/cmake_targets/ran_build/build/nr-softmodem --help
# Should run without asking for password
```

---

## 4. ADM Script Cannot Find UE Tunnel IP

### Root Cause
The UE tunnel IP is extracted dynamically from `docker logs oai-smf`. If the 5G core isn't fully up yet when the script starts, the log line hasn't appeared yet.

### Fix
Always wait for the 5G core to be fully healthy before starting the ADM script. In the demo launcher this is handled automatically (18-second delay after core start). If running manually:
```bash
# Check all OAI containers are healthy
docker ps | grep oai
# Wait until all show "healthy" before running the script
```

---

## 5. Grafana Import Screen Does Not Show Datasource Dropdown

### Root Cause
The dashboard JSON uses a variable (`${DS_PROMETHEUS}`) in `__inputs`, but Grafana sometimes skips the mapping UI if the variable doesn't match any known input format in the version you're running.

### Fix
Hardcode the UID directly in the JSON before importing (see Issue 1 above). This is more reliable than relying on the import-time mapping dropdown.

---

## 6. iperf Garbage Report / "-nan" Jitter Values

### Root Cause
Occasionally iperf produces a corrupted server report line with `-nan bits/sec` and `4294966296/0 (0%)` when UDP sockets are reused between back-to-back test iterations.

### Behaviour
The ADM script has a built-in sanity check: it only records jitter/loss values when `total_count > 0` and `lost_count <= total_count`. Corrupted reports are silently skipped and the Grafana graph does not show an artificial zero dip.

---

## 7. tc qdisc Error on Script Restart

### Symptom
```
RTNETLINK answers: File exists
```

### Root Cause
A `tc qdisc` rule from the previous run was not cleaned up.

### Fix
```bash
sudo tc qdisc del dev demo-oai root 2>/dev/null; echo done
```
Run this before restarting the ADM script.

---

## Debug Commands Reference

```bash
# Check exporter is running and serving metrics
curl localhost:8001/metrics | grep ztn_

# Check which process is on port 8001
sudo ss -tulpn | grep 8001

# Check ADM script process
ps -ef | grep test_4_addlat

# Check Prometheus targets (open in browser)
http://localhost:9090/targets

# Search for ZTN metrics in Prometheus (open in browser)
http://localhost:9090  →  search: ztn_

# Check Prometheus service
sudo systemctl status prometheus

# Check Grafana service
sudo systemctl status grafana-server

# Check all OAI Docker containers
docker ps | grep oai

# Check OAI 5G core logs
docker logs oai-smf --tail 50
```

---

## Common Root Causes Checklist

When things are not working on a new machine, go through this list:

- [ ] Different hostname → datasource UID will be different
- [ ] Different username → file paths in sudoers and launcher will be wrong
- [ ] Wrong datasource UID in dashboard JSON
- [ ] Prometheus datasource not added in Grafana
- [ ] Port mismatch between exporter (`8001`) and prometheus.yml
- [ ] ADM script not running → no metrics on port 8001
- [ ] Missing Python packages → `pip3 install -r requirements.txt`
- [ ] 5G core containers not started → UE IP extraction fails
- [ ] sudoers not configured → launcher hangs on password prompt
- [ ] Leftover tc qdisc rule → iperf fails silently
