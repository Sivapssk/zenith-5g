# Scripts

| File | Purpose |
|------|---------|
| `test_4_addlat.py` | Main ZTN ADM script — congestion test, BiLSTM prediction, RL action selection, Prometheus metrics export including ping latency |
| `server.py` | iperf UDP server — runs on UE side |
| `demotest_ADM.py` | One-click demo launcher — Flask web UI at `http://localhost:5050` |

Metrics exposed on **port 8001**. Ensure prometheus.yml scrapes `localhost:8001`.
