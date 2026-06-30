# ZENITH-5G
### Zero-touch Enabled Network Intelligence Testbed for 5G

**International Institute of Information Technology, Bangalore (IIITB)**

---

## Overview

ZENITH-5G is a research testbed implementing a **Zero-Touch Network (ZTN)** pipeline over a 5G NR softmodem setup using OpenAirInterface (OAI). It demonstrates closed-loop, intent-driven network automation combining:

- **Intent-Based Networking (IBN)** via NILE intent parsing
- **Digital Twin** using a BiLSTM prediction model
- **Reinforcement Learning** action selection (Q-table lookup)
- **Live Monitoring** via Prometheus + Grafana
- **One-click Demo Launcher** for visitor demonstrations

---

## System Architecture

```
User Intent (NILE)
       ↓
Bandwidth Threshold Extraction
       ↓
Apply UPF Congestion (tc qdisc)    ←──────────────────┐
       ↓                                              │
Run iperf3 (UDP)                                      │
       ↓                                              │
Measure Throughput / Jitter / Loss / Latency (ping)   │
       ↓                                              │
BiLSTM Prediction (Digital Twin)                      │
       ↓                                              │
UE State Classification                               │
       ↓                                              │
RL Action Selection (Q-table)                         │
       ↓                                              │
Optimal Bandwidth Decision ───────────────────────────┘
       ↓
Prometheus Metrics → Grafana Live Dashboard
```

---

## Repository Structure

```
zenith-5g/
├── README.md
├── requirements.txt
├── .gitignore
├── scripts/
│   ├── test_4_addlat.py
│   ├── server.py
│   └── demo_launcher_4.py
├── grafana/
│   └── dashboard.json
├── prometheus/
│   └── prometheus.yml
├── model_and_data/
│   ├── ADM_Files(test_4_addlat.py)/
│   └── Dashboard(demo_launcher)/
└── docs/
    ├── setup_guide.md
    ├── troubleshooting.md
    └── images/
```

---

## Hardware & Software Requirements

| Component | Specification |
|-----------|--------------|
| Machine | x86_64, Ubuntu 22.04 LTS |
| 5G Core | OAI CN5G (Docker Compose) |
| gNB | OAI nr-softmodem (rfsimulator) |
| UE | OAI nr-uesoftmodem (rfsimulator) |
| SDR | USRP B210 (for hardware mode) |
| Monitoring | Prometheus + Grafana |
| Python | 3.10+ |

---

> For the full installation, environment setup, and troubleshooting details, see [docs/setup_guide.md](docs/setup_guide.md).

## Quick Start

### 1. Clone the repository
```bash
git clone https://github.com/<your-username>/zenith-5g.git
cd zenith-5g
```

### 2. Install Python dependencies
```bash
pip3 install -r requirements.txt --break-system-packages
```

### 3. Configure Prometheus
```bash
sudo cp prometheus/prometheus.yml /etc/prometheus/prometheus.yml
sudo systemctl restart prometheus
```

### 4. Import Grafana Dashboard
- Open `http://localhost:3000`
- Go to **Dashboards → Import**
- Upload `grafana/dashboard.json`
- **Important:** Map the Prometheus datasource UID — see [Troubleshooting](docs/troubleshooting.md)

### 5. Configure passwordless sudo for gNB/UE
```bash
sudo visudo
```
Add these lines (replace `<username>` with your system username):
```
<username> ALL=(ALL) NOPASSWD: \
  /home/<username>/openairinterface5g/cmake_targets/ran_build/build/nr-softmodem, \
  /home/<username>/openairinterface5g/cmake_targets/ran_build/build/nr-uesoftmodem
```

### 6. Launch the demo
```bash
python3 scripts/demotest_ADM.py
```
Open `http://localhost:5050` in your browser and click **▶ Launch Full Demo**.

---

## Prometheus Metrics Exported

All metrics are exposed on port `8001` by the ADM script.

| Metric | Description |
|--------|-------------|
| `ztn_intent_bandwidth_kbps` | Target bandwidth from NILE intent (constant) |
| `ztn_applied_congestion_kbps` | Congestion ceiling applied via `tc qdisc` |
| `ztn_achieved_throughput_kbps` | UE-received bandwidth (iperf server report) |
| `ztn_jitter_ms` | UDP jitter from iperf server report |
| `ztn_packet_loss_percent` | Packet loss % from iperf server report |
| `ztn_ping_latency_ms` | Round-trip latency from ping to UE tunnel IP |
| `ztn_test_iteration` | Current iteration (1–10) |
| `ztn_predicted_bandwidth_raw_kbps` | BiLSTM next-step prediction |
| `ztn_prediction_error_kbps` | Predicted − actual bandwidth |
| `ztn_ue_state_kbps` | Discrete UE state bucket |
| `ztn_action_throughput_kbps` | Throughput of RL-selected action |
| `ztn_chosen_action_id` | ID of chosen action (a_1 … a_8) |

---

## Grafana Dashboard

The dashboard is organized into three sections visible on a single page:

**Section 1 — Intent & Network KPIs**
Four stat panels (Intent BW, UE State, Action ID, Iteration) + two gauge panels (Achieved Throughput, Action Throughput).

**Section 2 — Closed Loop**
Time series showing Intent (flat green dashed), Applied Congestion (yellow zigzag), and Achieved Throughput (blue) — the core visualization of the ZTN closed-loop behavior.

**Section 3 — Network Quality**
Jitter + Ping Latency (dual Y-axis) and Packet Loss time series.

> **Note on Datasource UID:** The dashboard JSON contains a hardcoded Grafana datasource UID. When deploying on a new machine, this UID must be updated. See [Troubleshooting](docs/troubleshooting.md#datasource-uid-mismatch).

---

## Demo Launcher

`demotest_ADM.py` serves a web-based control panel at `http://localhost:5050` that starts all components in the correct sequence:

```
5G Core (18s wait) → gNB (12s wait) → UE (8s wait) → iperf Server (3s wait) → ADM Script
```

Each component card shows live log output and status. A direct link to the Grafana dashboard is included in the header.

---

## Known Issues & Fixes

See [docs/troubleshooting.md](docs/troubleshooting.md) for the full list. Key issues:

- Grafana datasource UID mismatch after moving to a new machine
- Prometheus scrape port mismatch (exporter vs config)
- sudo password prompt blocking demo launcher

---

## Project Context

This testbed was developed as part of a research project at IIITB on Zero-Touch Network automation for 5G/O-RAN systems. The ADM (Adaptive Decision Making) pipeline implements the ZTN framework combining intent translation, digital twin prediction, and reinforcement learning for autonomous network control.

---

## Team

- IIITB 5G/O-RAN Research Lab
