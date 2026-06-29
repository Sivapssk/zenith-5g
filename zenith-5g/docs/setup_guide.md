# Setup Guide — ZENITH-5G

Step-by-step instructions for deploying the ZENITH-5G testbed on a fresh Ubuntu 22.04 machine.

---

## Prerequisites

- Ubuntu 22.04 LTS (x86_64)
- OpenAirInterface 5G NR already built at `~/openairinterface5g/`
- OAI CN5G Docker Compose setup at `~/oai-cn5g/`
- Docker and Docker Compose installed
- Python 3.10+
- Prometheus installed (`sudo apt install prometheus`)
- Grafana installed (see https://grafana.com/docs/grafana/latest/setup-grafana/installation/debian/)

---

## Step 1 — Clone the Repository

```bash
git clone https://github.com/<your-username>/zenith-5g.git
cd zenith-5g
```

---

## Step 2 — Install Python Dependencies

```bash
pip3 install -r requirements.txt --break-system-packages
```

---

## Step 3 — Configure Prometheus

Copy the scrape config:
```bash
sudo cp prometheus/prometheus.yml /etc/prometheus/prometheus.yml
sudo systemctl restart prometheus
sudo systemctl enable prometheus
```

Verify Prometheus is running:
```bash
sudo systemctl status prometheus
# Open http://localhost:9090 in browser
```

---

## Step 4 — Configure Grafana

**4a. Start Grafana**
```bash
sudo systemctl start grafana-server
sudo systemctl enable grafana-server
```
Open `http://localhost:3000` — default login is `admin / admin`.

**4b. Add Prometheus as a datasource**
- Go to `Connections → Data sources → Add new data source`
- Select **Prometheus**
- Set URL: `http://localhost:9090`
- Click **Save & Test** — confirm green checkmark

**4c. Get the datasource UID**
After saving, check the browser URL:
```
http://localhost:3000/connections/datasources/edit/XXXXXXXXXXXXXXX
```
Copy that UID string.

**4d. Update dashboard JSON with your UID**
```bash
# Find what UID is currently in the dashboard file
grep -o '"uid":"[^"]*"' grafana/dashboard.json | head -1

# Replace with your new UID (run from repo root)
sed -i 's/<OLD_UID_FROM_ABOVE>/<YOUR_NEW_UID>/g' grafana/dashboard.json
```

**4e. Import the dashboard**
- Go to `Dashboards → Import`
- Upload `grafana/dashboard.json`
- Click **Import**

---

## Step 5 — Configure Passwordless sudo

The demo launcher needs to start `nr-softmodem` and `nr-uesoftmodem` without a password prompt.

```bash
sudo visudo
```

Add at the bottom (replace `<username>` with your Linux username):
```
<username> ALL=(ALL) NOPASSWD: \
  /home/<username>/openairinterface5g/cmake_targets/ran_build/build/nr-softmodem, \
  /home/<username>/openairinterface5g/cmake_targets/ran_build/build/nr-uesoftmodem
```

Test:
```bash
sudo /home/<username>/openairinterface5g/cmake_targets/ran_build/build/nr-softmodem --help
# Must not ask for password
```

---

## Step 6 — Update File Paths in Scripts

Open `scripts/demotest_ADM.py` and verify these paths match your machine:
```python
HOME        = os.path.expanduser("~")           # auto-detected, no change needed
CN5G_DIR    = os.path.join(HOME, "oai-cn5g")    # change if your CN5G is elsewhere
BUILD_DIR   = os.path.join(HOME, "openairinterface5g", "cmake_targets", "ran_build", "build")
```

Open `scripts/test_4_addlat.py` and verify:
```python
# Path to your Poisson congestion CSV and RL lookup CSVs
# Path to your trained BiLSTM model (.h5 file)
# These must exist on the machine before running
```

---

## Step 7 — Run the Demo

**Option A — One-click launcher (recommended for demos)**
```bash
python3 scripts/demotest_ADM.py
```
Open `http://localhost:5050` and click **▶ Launch Full Demo**.

**Option B — Manual startup (for development/debugging)**

Open 5 separate terminals:

```bash
# Terminal 1 — 5G Core
cd ~/oai-cn5g
docker compose up -d

# Terminal 2 — gNB (wait ~18s after core is up)
cd ~/openairinterface5g/cmake_targets/ran_build/build
sudo ./nr-softmodem -O ../../../targets/PROJECTS/GENERIC-NR-5GC/CONF/gnb.sa.band78.fr1.106PRB.usrpb210.conf --gNBs.[0].min_rxtxtime 6 --rfsim

# Terminal 3 — UE (wait ~12s after gNB is up)
cd ~/openairinterface5g/cmake_targets/ran_build/build
sudo ./nr-uesoftmodem -r 106 --numerology 1 --band 78 -C 3619200000 --uicc0.imsi 001010000000001 --rfsim

# Terminal 4 — iperf server (wait ~8s after UE connects)
cd ~/openairinterface5g/cmake_targets/ran_build/build
python3 scripts/server.py

# Terminal 5 — ADM script
cd ~/openairinterface5g/cmake_targets/ran_build/build
python3 scripts/test_4_addlat.py
```

---

## Step 8 — Verify Everything is Working

```bash
# 1. Check Prometheus targets are UP
# Open: http://localhost:9090/targets
# All three jobs (adm, node, prometheus) should show health: up

# 2. Check ZTN metrics are being exported
curl localhost:8001/metrics | grep ztn_

# 3. Open Grafana dashboard
# Open: http://localhost:3000
# Navigate to your imported dashboard
# Panels should show live data within 30 seconds of the ADM script starting
```

---

## Startup Order Summary

```
Prometheus + Grafana  (already running as services)
        ↓
5G Core (docker compose up -d)   wait 18s
        ↓
gNB (nr-softmodem --rfsim)       wait 12s
        ↓
UE  (nr-uesoftmodem --rfsim)     wait 8s
        ↓
iperf server (server.py)         wait 3s
        ↓
ADM script (test_4_addlat.py)
        ↓
Grafana panels fill with live data
```

---

## Notes for USRP B210 Hardware Mode

When switching from rfsimulator to real USRP B210 hardware:

1. Remove `--rfsim` flag from gNB and UE commands
2. Add `--usrp-args "type=b200,serial=<YOUR_SERIAL>"` to both commands
3. The UE tunnel IP will still be auto-extracted from SMF logs (no change needed)
4. Ping latency values will increase from ~0.03 ms to ~10–50 ms (real over-the-air)
5. Jitter and packet loss patterns will differ from rfsimulator
