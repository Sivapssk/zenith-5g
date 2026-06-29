#!/usr/bin/env python3
"""
ZENITH-5G Demo Launcher  — IIITB Zero-Touch Network Testbed
Run once:   python3 demo_launcher.py
Open:       http://localhost:5050
"""

import os, subprocess, time, json, threading, signal
from flask import Flask, jsonify, render_template_string, send_file, Response
from datetime import datetime

app = Flask(__name__)

# ─── Paths ───────────────────────────────────────────────────────────────────
HOME      = os.path.expanduser("~")
CN5G_DIR  = os.path.join(HOME, "oai-cn5g")
BUILD_DIR = os.path.join(HOME, "openairinterface5g", "cmake_targets", "ran_build", "build")
GNB_CONF  = os.path.join(HOME, "openairinterface5g", "targets", "PROJECTS",
                         "GENERIC-NR-5GC", "CONF",
                         "gnb.sa.band78.fr1.106PRB.usrpb210.conf")
LOG_DIR   = os.path.join(HOME, "ztn_demo_logs")
ADM_PNG   = os.path.join(BUILD_DIR, "ztn_digitwin_optimal_RL_output_ADM.png")
os.makedirs(LOG_DIR, exist_ok=True)

IMG_PATHS = {
    "logo":        os.path.join(HOME, "logo.png"),
    "gif":         os.path.join(HOME, "animation_testbed.gif"),
    "core":        os.path.join(HOME, "Downloads", "cloud.png"),
    "gnb":         os.path.join(HOME, "Downloads", "radio-antenna.png"),
    "ue":          os.path.join(HOME, "Downloads", "cell-phone.png"),
    "adm_output":  ADM_PNG,
    "placeholder": os.path.join(HOME, "Downloads", "5g_arch.png"),
}

# ─── Component definitions ────────────────────────────────────────────────────
COMPONENTS = {
    "core": {
        "label": "5G Core",
        "sub":   "OAI CN5G · Docker Compose",
        "icon":  "core",
        "color": "#3b82f6",
        "cwd":   CN5G_DIR,
        "cmd":   ["docker", "compose", "up", "-d"],
        "cmd_display": "cd ~/oai-cn5g\ndocker compose up -d",
        "check": "docker ps --filter name=oai --format '{{.Names}}' | grep -q oai",
        "oneshot": True,
        "delay_after": 18,
    },
    "gnb": {
        "label": "gNB",
        "sub":   "nr-softmodem · rfsimulator",
        "icon":  "gnb",
        "color": "#10b981",
        "cwd":   BUILD_DIR,
        "cmd":   ["sudo", "./nr-softmodem", "-O", GNB_CONF,
                  r"--gNBs.[0].min_rxtxtime", "6", "--rfsim"],
        "cmd_display": "cd ~/openairinterface5g/cmake_targets/ran_build/build\nsudo ./nr-softmodem -O .../gnb.sa.band78.fr1.106PRB.usrpb210.conf \\\n  --gNBs.[0].min_rxtxtime 6 --rfsim",
        "check": "pgrep -x nr-softmodem",
        "oneshot": False,
        "delay_after": 12,
    },
    "ue": {
        "label": "UE",
        "sub":   "nr-uesoftmodem · rfsimulator",
        "icon":  "ue",
        "color": "#f59e0b",
        "cwd":   BUILD_DIR,
        "cmd":   ["sudo", "./nr-uesoftmodem", "-r", "106",
                  "--numerology", "1", "--band", "78",
                  "-C", "3619200000",
                  "--uicc0.imsi", "001010000000001", "--rfsim"],
        "cmd_display": "sudo ./nr-uesoftmodem -r 106 --numerology 1 \\\n  --band 78 -C 3619200000 \\\n  --uicc0.imsi 001010000000001 --rfsim",
        "check": "pgrep -x nr-uesoftmodem",
        "oneshot": False,
        "delay_after": 8,
    },
    "server": {
        "label": "iperf Server",
        "sub":   "server.py · UE-side UDP listener",
        "icon":  "⟡",
        "color": "#8b5cf6",
        "cwd":   BUILD_DIR,
        "cmd":   ["python3", "server.py"],
        "cmd_display": "cd ~/openairinterface5g/cmake_targets/ran_build/build\npython3 server.py",
        "check": "pgrep -f server.py",
        "oneshot": False,
        "delay_after": 3,
    },
    "adm": {
        "label": "ADM Script",
        "sub":   "ZTN: Intent → Digital Twin → RL",
        "icon":  "⬖",
        "color": "#ef4444",
        "cwd":   BUILD_DIR,
        "cmd":   ["python3", "test_4_addlat.py"],
        #"cmd_display": "cd ~/openairinterface5g/cmake_targets/ran_build/build\npython3 test_3.py",
        "cmd_display": "cd ~/openairinterface5g/cmake_targets/ran_build/build\npython3 test_4_addlat.py",
        "check": "pgrep -f test_4_addlat.py",
        "oneshot": False,
        "delay_after": 0,
    },
}

LAUNCH_ORDER = ["core", "gnb", "ue", "server", "adm"]

# ─── State ────────────────────────────────────────────────────────────────────
procs = {}
logs  = {k: [] for k in COMPONENTS}
state = {k: "idle" for k in COMPONENTS}
launch_lock = threading.Lock()

def log(key, line):
    ts    = datetime.now().strftime("%H:%M:%S")
    entry = f"[{ts}] {line}"
    logs[key].append(entry)
    if len(logs[key]) > 400:
        logs[key] = logs[key][-400:]

def check_running(key):
    r = subprocess.run(COMPONENTS[key]["check"], shell=True, capture_output=True)
    return r.returncode == 0

def stream_output(key, proc):
    def _read(stream):
        for line in iter(stream.readline, b""):
            log(key, line.decode(errors="replace").rstrip())
    t1 = threading.Thread(target=_read, args=(proc.stdout,), daemon=True)
    t2 = threading.Thread(target=_read, args=(proc.stderr,), daemon=True)
    t1.start(); t2.start()
    proc.wait()
    state[key] = "stopped" if proc.returncode == 0 else "error"
    log(key, f"Process exited (code {proc.returncode})")

def start_component(key):
    cfg = COMPONENTS[key]
    log(key, f"$ {cfg['cmd_display'].replace(chr(10), ' ')}")
    state[key] = "starting"
    try:
        proc = subprocess.Popen(cfg["cmd"], cwd=cfg["cwd"],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                preexec_fn=os.setsid)
        if cfg["oneshot"]:
            proc.wait(timeout=30)
            state[key] = "running"
            log(key, "✅ Services started successfully.")
        else:
            procs[key] = proc
            state[key] = "running"
            threading.Thread(target=stream_output, args=(key, proc), daemon=True).start()
    except Exception as e:
        state[key] = "error"
        log(key, f"ERROR: {e}")

def launch_all_sequence():
    for key in LAUNCH_ORDER:
        start_component(key)
        delay = COMPONENTS[key]["delay_after"]
        if delay > 0:
            log(key, f"⏳ Waiting {delay}s before next component...")
            time.sleep(delay)

# ─── Routes ───────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template_string(HTML_PAGE)

@app.route("/img/<name>")
def serve_img(name):
    path = IMG_PATHS.get(name)
    if path and os.path.exists(path):
        return send_file(path)
    return "", 404

@app.route("/api/status")
def api_status():
    result = {}
    for key in COMPONENTS:
        alive = check_running(key) if state[key] in ("running", "starting") else False
        result[key] = {"state": state[key], "alive": alive,
                       "label": COMPONENTS[key]["label"],
                       "color": COMPONENTS[key]["color"]}
    return jsonify(result)

@app.route("/api/logs/<key>")
def api_logs(key):
    if key not in COMPONENTS:
        return jsonify([])
    return jsonify(logs[key][-120:])

@app.route("/api/docker_ps")
def api_docker_ps():
    try:
        r = subprocess.run(
            ["docker", "ps", "--format",
             "{{.Names}}|||{{.Status}}|||{{.Ports}}"],
            capture_output=True, text=True, timeout=5)
        lines = [l for l in r.stdout.strip().split("\n") if l]
        parsed = []
        for l in lines:
            parts = l.split("|||")
            if len(parts) == 3:
                name, status, ports = parts
                healthy = "healthy" in status.lower() or "up" in status.lower()
                parsed.append({"name": name.strip(),
                                "status": status.strip(),
                                "healthy": healthy})
        return jsonify(parsed)
    except Exception as e:
        return jsonify([])

@app.route("/api/adm_image_ready")
def api_adm_image_ready():
    exists = os.path.exists(ADM_PNG)
    return jsonify({
        "ready": exists,
        "ts":    os.path.getmtime(ADM_PNG) if exists else 0,
        "server_now": time.time()   # send server clock so JS can compare fairly
    })

@app.route("/api/launch_all", methods=["POST"])
def api_launch_all():
    with launch_lock:
        threading.Thread(target=launch_all_sequence, daemon=True).start()
    return jsonify({"ok": True})

@app.route("/api/start/<key>", methods=["POST"])
def api_start(key):
    if key not in COMPONENTS:
        return jsonify({"error": "unknown"}), 400
    threading.Thread(target=start_component, args=(key,), daemon=True).start()
    return jsonify({"ok": True})

@app.route("/api/stop/<key>", methods=["POST"])
def api_stop(key):
    if key not in procs:
        return jsonify({"error": "not running"}), 400
    try:
        os.killpg(os.getpgid(procs[key].pid), signal.SIGTERM)
        state[key] = "stopped"
        log(key, "Stopped by user.")
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"ok": True})

@app.route("/api/stop_all", methods=["POST"])
def api_stop_all():
    for key in list(procs.keys()):
        try:
            os.killpg(os.getpgid(procs[key].pid), signal.SIGTERM)
            state[key] = "stopped"
            log(key, "Stopped (stop all).")
        except:
            pass
    subprocess.run(["docker", "compose", "down"], cwd=CN5G_DIR, capture_output=True)
    state["core"] = "stopped"
    log("core", "Docker services stopped.")
    return jsonify({"ok": True})

# ─── HTML Page ────────────────────────────────────────────────────────────────
HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>ZENITH-5G · IIITB Testbed Launcher</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');
:root{
  --bg:#0d1117;--surf:#161b22;--surf2:#1c2128;--border:#21262d;
  --text:#e6edf3;--muted:#7d8590;
  --blue:#3b82f6;--green:#10b981;--amber:#f59e0b;--purple:#8b5cf6;--red:#ef4444;
  --r:8px;
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0;}
html,body{height:100%;background:var(--bg);color:var(--text);
  font-family:'Inter',sans-serif;font-size:12px;overflow-x:hidden;}

/* ── HEADER ─────────────────────────────────────────── */
header{
  display:flex;align-items:center;gap:14px;
  padding:8px 20px;background:var(--surf);
  border-bottom:1px solid var(--border);
  flex-shrink:0;flex-wrap:wrap;
}
.hdr-logo{height:44px;width:auto;object-fit:contain;flex-shrink:0;}
.hdr-title h1{
  font-size:16px;font-weight:700;
  background:linear-gradient(90deg,#60a5fa,#a78bfa);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;
  white-space:nowrap;
}
.hdr-title p{font-size:10px;color:var(--muted);}
.hdr-badge{
  display:inline-block;margin-top:3px;padding:1px 8px;
  background:linear-gradient(90deg,rgba(59,130,246,.15),rgba(167,139,250,.15));
  border:1px solid rgba(99,102,241,.3);border-radius:20px;
  font-size:9px;color:#a78bfa;font-weight:500;letter-spacing:.05em;white-space:nowrap;
}
.hdr-divider{width:1px;height:36px;background:var(--border);flex-shrink:0;}
/* launch controls inline in header */
.hdr-controls{display:flex;align-items:center;gap:8px;flex-shrink:0;}
.btn-launch{
  padding:7px 22px;border-radius:var(--r);
  background:linear-gradient(135deg,var(--blue),var(--purple));
  border:none;color:#fff;font-size:12px;font-weight:600;
  font-family:inherit;cursor:pointer;letter-spacing:.03em;
  box-shadow:0 0 16px rgba(59,130,246,.3);
  transition:opacity .2s,transform .1s;white-space:nowrap;
}
.btn-launch:hover{opacity:.88;transform:translateY(-1px);}
.btn-launch:disabled{opacity:.4;cursor:not-allowed;transform:none;}
.btn-stop-all{
  padding:7px 14px;border-radius:var(--r);
  background:transparent;border:1px solid #3d1515;
  color:var(--red);font-size:11px;font-weight:500;
  font-family:inherit;cursor:pointer;white-space:nowrap;
  transition:background .2s;
}
.btn-stop-all:hover{background:#120505;}
.seq-info{
  font-size:10px;color:var(--muted);white-space:nowrap;
  border:1px solid var(--border);border-radius:6px;
  padding:5px 10px;background:var(--surf2);line-height:1.5;
}
.seq-info span{color:#4d6080;}
/* progress bar */
#pw{position:fixed;top:0;left:0;right:0;height:2px;z-index:100;display:none;}
#pb{height:100%;width:0%;background:linear-gradient(90deg,var(--blue),var(--purple));transition:width 1s linear;}
/* grafana button pushed to right */
.hdr-spacer{flex:1;}
.btn-grafana{
  display:flex;align-items:center;gap:6px;padding:7px 14px;
  border-radius:var(--r);background:#1a1f2e;border:1px solid #2d3748;
  color:var(--text);font-size:11px;font-family:inherit;
  text-decoration:none;cursor:pointer;white-space:nowrap;
  transition:border-color .2s,background .2s;flex-shrink:0;
}
.btn-grafana:hover{border-color:var(--blue);background:#1e2535;}
.g-dot{width:8px;height:8px;border-radius:50%;background:#f46800;flex-shrink:0;}

/* ── MAIN LAYOUT ─────────────────────────────────────── */
.main{
  display:grid;
  grid-template-columns:1fr 1fr;
  grid-template-rows:1fr;
  gap:12px;padding:12px;
  height:calc(100vh - 62px);
  box-sizing:border-box;
}

/* LEFT: ADM takes full height, split into top-card + bottom-graph */
.col-left{
  display:flex;flex-direction:column;gap:10px;
  overflow:hidden;
  height:100%;
}
/* ADM script card takes exactly 50% of left column */
#card-adm{
  flex:1;
  min-height:0;
  overflow:hidden;
}
/* ADM card log box fills available space in card */
#card-adm .log-box{
  flex:1;
  min-height:0;
  height:auto;
}
/* RIGHT: 2×2 grid */
.col-right{
  display:grid;
  grid-template-columns:1fr 1fr;
  grid-template-rows:1fr 1fr;
  gap:10px;
  height:100%;
  min-height:0;
}
/* all right-side cards fill their grid cell fully */
.col-right .card{
  min-height:0;
  overflow:hidden;
}
.col-right .card .log-box{
  flex:1;
  min-height:0;
  height:auto;
}

/* ── CARD ────────────────────────────────────────────── */
.card{
  background:var(--surf);border:1px solid var(--border);
  border-radius:var(--r);padding:12px 14px;
  display:flex;flex-direction:column;gap:7px;
  min-height:0;overflow:hidden;
  transition:border-color .25s;
}
.card.running{border-color:var(--cc);}
.card-hdr{display:flex;align-items:flex-start;justify-content:space-between;flex-shrink:0;}
.card-title{display:flex;align-items:center;gap:8px;}
.card-icon-wrap{
  width:32px;height:32px;border-radius:7px;flex-shrink:0;
  background:color-mix(in srgb,var(--cc) 12%,transparent);
  border:1px solid color-mix(in srgb,var(--cc) 25%,transparent);
  display:flex;align-items:center;justify-content:center;overflow:hidden;
}
.card-icon-wrap img{width:20px;height:20px;object-fit:contain;filter:drop-shadow(0 0 3px var(--cc));}
.card-icon-text{font-size:17px;color:var(--cc);}
.card-lbl{font-size:13px;font-weight:600;}
.card-sub{font-size:10px;color:var(--muted);}

/* pill */
.pill{padding:2px 8px;border-radius:20px;font-size:10px;font-weight:500;
  display:flex;align-items:center;gap:4px;border:1px solid transparent;white-space:nowrap;}
.pdot{width:5px;height:5px;border-radius:50%;flex-shrink:0;}
.pill.idle    {color:var(--muted);border-color:var(--border);}
.pill.idle .pdot{background:var(--muted);}
.pill.starting{color:var(--amber);border-color:#3d2e0a;background:#100d00;}
.pill.starting .pdot{background:var(--amber);animation:blink 1s infinite;}
.pill.running {color:var(--green);border-color:#0d2e18;background:#04110a;}
.pill.running .pdot{background:var(--green);}
.pill.stopped {color:var(--muted);border-color:var(--border);}
.pill.stopped .pdot{background:var(--muted);}
.pill.error   {color:var(--red);border-color:#3d1212;background:#0f0404;}
.pill.error .pdot{background:var(--red);}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.2}}

/* cmd box */
.cmd-box{
  background:#080c12;border:1px solid #1a2030;border-radius:5px;
  padding:5px 10px;font-family:'JetBrains Mono',monospace;font-size:10px;
  color:#58a6ff;line-height:1.6;white-space:pre;overflow-x:auto;flex-shrink:0;
}

/* log box — flex-grow so it fills remaining card space */
.log-box{
  background:#07090e;border:1px solid #1a2030;border-radius:5px;
  padding:8px 10px;font-family:'JetBrains Mono',monospace;font-size:10px;
  color:#8b949e;overflow-y:auto;line-height:1.6;flex:1;min-height:0;
}
.log-box::-webkit-scrollbar{width:3px;}
.log-box::-webkit-scrollbar-thumb{background:var(--border);border-radius:2px;}
.lt{color:#2d3d50;}

/* docker table inside core card */
.docker-table{width:100%;border-collapse:collapse;font-family:'JetBrains Mono',monospace;font-size:9.5px;}
.docker-table th{text-align:left;padding:3px 6px;color:var(--muted);border-bottom:1px solid var(--border);font-weight:500;}
.docker-table td{padding:3px 6px;border-bottom:1px solid #161c24;}
.docker-table tr:last-child td{border-bottom:none;}
.dh-ok{color:var(--green);}
.dh-up{color:var(--amber);}

/* card actions */
.card-actions{display:flex;gap:6px;flex-shrink:0;}
.bsm{
  padding:4px 10px;border-radius:4px;font-size:10px;font-weight:500;
  font-family:inherit;cursor:pointer;border:1px solid var(--border);
  background:transparent;color:var(--text);transition:background .15s,border-color .15s;
}
.bsm:hover{background:var(--border);}
.bsm.go{border-color:var(--cc);color:var(--cc);}
.bsm.go:hover{background:color-mix(in srgb,var(--cc) 12%,transparent);}
.bsm.halt{border-color:#3d1212;color:var(--red);}
.bsm.halt:hover{background:#0f0404;}

/* ── ADM OUTPUT GRAPH PANEL ──────────────────────────── */
.adm-graph-panel{
  background:var(--surf);border:1px solid var(--border);border-radius:var(--r);
  display:flex;flex-direction:column;
  flex:1;           /* takes exactly 50% of col-left alongside ADM card */
  min-height:0;
  overflow:hidden;
}
.adm-graph-label{
  display:flex;align-items:center;gap:6px;
  padding:7px 12px;font-size:10px;color:var(--muted);
  background:#0a0d14;border-bottom:1px solid var(--border);flex-shrink:0;
}
.dot-adm{width:6px;height:6px;border-radius:50%;background:var(--red);flex-shrink:0;}
.dot-adm.ready{background:var(--green);}
.adm-graph-panel img{
  width:100%;
  flex:1;
  min-height:0;
  object-fit:contain;
  padding:8px;
  display:block;
}

/* ── TOAST ───────────────────────────────────────────── */
#toast{
  position:fixed;bottom:16px;right:16px;
  background:var(--surf);border:1px solid var(--border);
  padding:8px 14px;border-radius:7px;font-size:11px;color:var(--text);
  opacity:0;transform:translateY(6px);transition:opacity .3s,transform .3s;
  pointer-events:none;z-index:99;
}
#toast.show{opacity:1;transform:translateY(0);}
</style>
</head>
<body>

<div id="pw"><div id="pb"></div></div>

<header>
  <img class="hdr-logo" src="/img/logo" alt="IIITB" onerror="this.style.display='none'">
  <div class="hdr-title">
    <h1>ZENITH-5G Testbed</h1>
    <p>International Institute of Information Technology, Bangalore</p>
    <span class="hdr-badge">Zero-touch Enabled Network Intelligence Testbed for 5G</span>
  </div>
  <div class="hdr-divider"></div>
  <div class="hdr-controls">
    <button class="btn-launch" id="btn-launch" onclick="launchAll()">▶ &nbsp;Launch Full Demo</button>
    <button class="btn-stop-all" onclick="stopAll()">■ &nbsp;Stop All</button>
    <div class="seq-info">
      Core → gNB → UE → Server → ADM<br>
      <span>auto-waits between steps</span>
    </div>
  </div>
  <div class="hdr-spacer"></div>
  <a class="btn-grafana" href="http://localhost:3000/d/ztn-adm-live-v3_3/8f53643?orgId=1&from=now-5m&to=now&timezone=browser&refresh=auto" target="_blank">
    <span class="g-dot"></span>Open Grafana Dashboard
  </a>
</header>

<div class="main">
  <!-- LEFT COLUMN: ADM card + graph -->
  <div class="col-left" id="col-left">
    <!-- ADM card injected by JS -->
    <!-- ADM graph panel -->
    <div class="adm-graph-panel" id="adm-graph-panel">
      <div class="adm-graph-label">
        <span class="dot-adm" id="adm-dot"></span>
        <span id="adm-img-lbl">Waiting for ADM output graph…</span>
      </div>
      <img id="adm-img" src="/img/placeholder" onerror="this.src='/img/placeholder'" style="opacity:0.85;">
    </div>
  </div>

  <!-- RIGHT COLUMN: 2×2 grid of core/gnb/ue/server -->
  <div class="col-right" id="col-right"></div>
</div>

<div id="toast"></div>

<script>
const C = {
  adm:   {label:"ADM Script",  sub:"ZTN: Intent → Digital Twin → RL", icon:"txt", iconSrc:"⬖", color:"#ef4444",
          cmd:"$ python3 test_4_addlat.py"},
  core:  {label:"5G Core",     sub:"OAI CN5G · Docker Compose",        icon:"img", iconSrc:"/img/core", color:"#3b82f6",
          cmd:"$ cd ~/oai-cn5g\n$ docker compose up -d"},
  gnb:   {label:"gNB",         sub:"nr-softmodem · rfsimulator",        icon:"img", iconSrc:"/img/gnb",  color:"#10b981",
          cmd:"$ sudo ./nr-softmodem -O gnb.sa.band78.fr1.106PRB.usrpb210.conf \\\n    --gNBs.[0].min_rxtxtime 6 --rfsim"},
  ue:    {label:"UE",          sub:"nr-uesoftmodem · rfsimulator",      icon:"img", iconSrc:"/img/ue",   color:"#f59e0b",
          cmd:"$ sudo ./nr-uesoftmodem -r 106 --numerology 1 --band 78 \\\n    -C 3619200000 --uicc0.imsi 001010000000001 --rfsim"},
  server:{label:"iperf Server", sub:"server.py · UE-side UDP listener", icon:"txt", iconSrc:"⟡",        color:"#8b5cf6",
          cmd:"$ python3 server.py"},
};
const LEFT_CARDS  = ["adm"];
const RIGHT_CARDS = ["core","gnb","ue","server"];

function makeIcon(c){
  return c.icon==="img"
    ? `<img src="${c.iconSrc}" onerror="this.parentElement.innerHTML='<span class=card-icon-text>${c.label[0]}</span>'">`
    : `<span class="card-icon-text">${c.iconSrc}</span>`;
}

function buildCard(key){
  const c = C[key];
  const dockerExtra = key==="core" ? `
    <div style="flex-shrink:0;margin-top:2px;">
      <table class="docker-table">
        <thead><tr><th>Container</th><th>Status</th></tr></thead>
        <tbody id="docker-tbody"><tr><td colspan="2" style="color:var(--muted)">— docker ps —</td></tr></tbody>
      </table>
    </div>` : "";

  return `
  <div class="card" id="card-${key}" style="--cc:${c.color}">
    <div class="card-hdr">
      <div class="card-title">
        <div class="card-icon-wrap">${makeIcon(c)}</div>
        <div><div class="card-lbl">${c.label}</div><div class="card-sub">${c.sub}</div></div>
      </div>
      <div class="pill idle" id="pill-${key}">
        <div class="pdot"></div><span id="ptxt-${key}">Idle</span>
      </div>
    </div>
    <div class="cmd-box">${escHtml(c.cmd)}</div>
    ${dockerExtra}
    <div class="log-box" id="log-${key}"><span style="color:#2d3d50">— waiting —</span></div>
    <div class="card-actions">
      <button class="bsm go"   onclick="startOne('${key}')">Start</button>
      <button class="bsm halt" onclick="stopOne('${key}')">Stop</button>
    </div>
  </div>`;
}

// Build ADM card into left column (before the graph panel)
document.getElementById("col-left").insertAdjacentHTML("afterbegin", buildCard("adm"));

// Build right cards
const rightGrid = document.getElementById("col-right");
RIGHT_CARDS.forEach(k => rightGrid.insertAdjacentHTML("beforeend", buildCard(k)));

// ── Polling ──────────────────────────────────────────────────────────
const ALL_KEYS = ["adm","core","gnb","ue","server"];

function pollStatus(){
  fetch("/api/status").then(r=>r.json()).then(data=>{
    ALL_KEYS.forEach(key=>{
      const s=data[key];
      const pill=document.getElementById(`pill-${key}`);
      const ptxt=document.getElementById(`ptxt-${key}`);
      const card=document.getElementById(`card-${key}`);
      if(!pill) return;
      pill.className=`pill ${s.state}`;
      ptxt.textContent=s.alive?"Running":
        s.state==="starting"?"Starting…":
        s.state.charAt(0).toUpperCase()+s.state.slice(1);
      s.state==="running"&&s.alive
        ? card.classList.add("running")
        : card.classList.remove("running");
    });
  }).catch(()=>{});
}

function pollLogs(){
  ALL_KEYS.forEach(key=>{
    fetch(`/api/logs/${key}`).then(r=>r.json()).then(lines=>{
      if(!lines.length) return;
      const box=document.getElementById(`log-${key}`);
      if(!box) return;
      box.innerHTML=lines.map(l=>{
        const m=l.match(/^\[(\d{2}:\d{2}:\d{2})\] (.+)$/);
        return m?`<div><span class="lt">${m[1]}</span> ${escHtml(m[2])}</div>`
                :`<div>${escHtml(l)}</div>`;
      }).join("");
      box.scrollTop=box.scrollHeight;
    }).catch(()=>{});
  });
}

function pollDocker(){
  fetch("/api/docker_ps").then(r=>r.json()).then(rows=>{
    const tbody=document.getElementById("docker-tbody");
    if(!tbody) return;
    if(!rows.length){
      tbody.innerHTML=`<tr><td colspan="2" style="color:var(--muted)">No running containers</td></tr>`;
      return;
    }
    tbody.innerHTML=rows.map(r=>`
      <tr>
        <td style="color:#e6edf3">${escHtml(r.name)}</td>
        <td class="${r.healthy?"dh-ok":"dh-up"}">${escHtml(r.status)}</td>
      </tr>`).join("");
  }).catch(()=>{});
}

let admReady=false, admStartServerTime=null;

function pollAdmImg(){
  if(admReady) return;
  fetch("/api/adm_image_ready").then(r=>r.json()).then(d=>{
    const img=document.getElementById("adm-img");
    const dot=document.getElementById("adm-dot");
    const lbl=document.getElementById("adm-img-lbl");
    if(!img) return;

    // If no session started yet, just keep showing placeholder
    if(!admStartServerTime) return;

    // Image file exists AND was modified AFTER this session started (server-side comparison)
    if(d.ready && d.ts >= admStartServerTime){
      admReady=true;
      // Force reload by busting cache with timestamp
      const newSrc="/img/adm_output?t="+d.ts;
      img.onerror=function(){
        // If image fails to load, retry next poll cycle
        admReady=false;
        img.src="/img/placeholder";
        img.style.opacity="0.85";
        lbl.textContent="ADM graph generating… retrying";
      };
      img.onload=function(){
        img.style.opacity="1";
        dot.classList.add("ready");
        lbl.textContent="ADM Output: Digital Twin Bandwidth Graph";
      };
      img.src=newSrc;
    }
  }).catch(()=>{});
}

setInterval(pollStatus, 2500);
setInterval(pollLogs,   3000);
setInterval(pollDocker, 5000);
setInterval(pollAdmImg, 4000);
pollStatus(); pollLogs(); pollDocker();

// ── Actions ──────────────────────────────────────────────────────────
function resetAdm(){
  admReady=false; admStartServerTime=null;
  const img=document.getElementById("adm-img");
  const dot=document.getElementById("adm-dot");
  const lbl=document.getElementById("adm-img-lbl");
  if(img){ img.onerror=null; img.onload=null; img.src="/img/placeholder"; img.style.opacity="0.85"; }
  if(dot){ dot.classList.remove("ready"); }
  if(lbl){ lbl.textContent="Waiting for ADM output graph…"; }
}

function launchAll(){
  const btn=document.getElementById("btn-launch");
  btn.disabled=true; btn.textContent="Launching…";
  const pw=document.getElementById("pw"), pb=document.getElementById("pb");
  pw.style.display="block"; pb.style.width="0%";
  setTimeout(()=>{ pb.style.width="95%"; },100);
  setTimeout(()=>{
    pb.style.width="100%";
    setTimeout(()=>{ pw.style.display="none"; },800);
    btn.disabled=false; btn.innerHTML="▶ &nbsp;Launch Full Demo";
  },65000);
  resetAdm();
  // Grab server's current time so comparison is clock-skew safe
  fetch("/api/adm_image_ready").then(r=>r.json()).then(d=>{
    admStartServerTime = d.server_now;
  });
  fetch("/api/launch_all",{method:"POST"})
    .then(()=>toast("Demo sequence started — components launching in order"))
    .catch(()=>toast("Error contacting launcher"));
}

function stopAll(){
  resetAdm();
  fetch("/api/stop_all",{method:"POST"}).then(()=>toast("All components stopped"));
}

function startOne(k){
  if(k==="adm"){
    resetAdm();
    fetch("/api/adm_image_ready").then(r=>r.json()).then(d=>{
      admStartServerTime = d.server_now;
    });
  }
  fetch(`/api/start/${k}`,{method:"POST"}).then(()=>toast(`Starting ${C[k].label}…`));
}

function stopOne(k){
  fetch(`/api/stop/${k}`,{method:"POST"}).then(()=>toast(`Stopped ${C[k].label}`));
}

function toast(msg){
  const t=document.getElementById("toast");
  t.textContent=msg; t.classList.add("show");
  setTimeout(()=>t.classList.remove("show"),3000);
}

function escHtml(s){
  return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}
</script>
</body>
</html>"""


if __name__ == "__main__":
    print("\n" + "─"*52)
    print("  ZENITH-5G Demo Launcher — IIITB")
    print("  Open   →  http://localhost:5050")
    print("  Grafana→  http://localhost:3000")
    print("─"*52 + "\n")
    app.run(host="0.0.0.0", port=5050, debug=False)
