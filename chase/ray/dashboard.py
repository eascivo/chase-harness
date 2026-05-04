"""Chase Ray Web Dashboard — 纯标准库 HTTP 服务器。

提供 REST API + SSE 实时推送 + 内嵌 HTML 页面。
零外部依赖，仅使用 Python 标准库。
"""

from __future__ import annotations

import json
import os
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn
from urllib.parse import urlparse

from chase.ray.config import (
    STATUS_PENDING,
    RayStateDir,
)


# ---------------------------------------------------------------------------
# Threaded HTTP Server
# ---------------------------------------------------------------------------

class _ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


# ---------------------------------------------------------------------------
# Request Handler
# ---------------------------------------------------------------------------

class DashboardHandler(BaseHTTPRequestHandler):
    """Dashboard HTTP 请求处理器。"""

    # 由 DashboardServer 启动时注入
    state: RayStateDir

    # ---- routing ----

    def do_GET(self):
        path = urlparse(self.path).path.rstrip("/")
        if path == "" or path == "/index.html":
            self._serve_html()
        elif path == "/api/status":
            self._api_status()
        elif path.startswith("/api/project/"):
            name = path[len("/api/project/"):]
            self._api_project(name)
        elif path == "/api/events":
            self._api_events()
        else:
            self._respond(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path.rstrip("/")
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            self._respond(400, {"error": "invalid JSON"})
            return

        if path == "/api/dispatch":
            self._api_dispatch(data)
        elif path == "/api/priority":
            self._api_priority(data)
        else:
            self._respond(404, {"error": "not found"})

    # ---- API handlers ----

    def _api_status(self):
        config = self.state.load_queue()
        pid = self.state.read_pid()
        running = pid is not None and _pid_alive(pid)
        uptime = _format_uptime(self.state) if running else ""

        total_cost = 0.0
        projects = []
        for p in config.projects:
            info = _project_summary(p)
            total_cost += info["cost"]
            projects.append(info)

        self._respond(200, {
            "running": running,
            "pid": pid,
            "uptime": uptime,
            "max_parallel": config.max_parallel,
            "total_cost": round(total_cost, 4),
            "projects": projects,
        })

    def _api_project(self, name: str):
        config = self.state.load_queue()
        project = _find_project(config, name)
        if not project:
            self._respond(404, {"error": f"project '{name}' not found"})
            return
        self._respond(200, _project_detail(project))

    def _api_events(self):
        """SSE endpoint — 每 5 秒推送状态快照。"""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            while True:
                config = self.state.load_queue()
                pid = self.state.read_pid()
                running = pid is not None and _pid_alive(pid)
                total_cost = 0.0
                projects = []
                for p in config.projects:
                    info = _project_summary(p)
                    total_cost += info["cost"]
                    projects.append(info)
                payload = json.dumps({
                    "running": running,
                    "pid": pid,
                    "uptime": _format_uptime(self.state) if running else "",
                    "max_parallel": config.max_parallel,
                    "total_cost": round(total_cost, 4),
                    "projects": projects,
                }, ensure_ascii=False)
                self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
                time.sleep(5)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _api_dispatch(self, data: dict):
        from chase.ray.config import Project

        name = data.get("name", "").strip()
        path = data.get("path", "").strip()
        if not name or not path:
            self._respond(400, {"error": "name and path are required"})
            return

        config = self.state.load_queue()
        if any(p.name == name for p in config.projects):
            self._respond(409, {"error": f"project '{name}' already exists"})
            return

        depends_on = data.get("depends_on", [])
        if isinstance(depends_on, str):
            depends_on = [d.strip() for d in depends_on.split(",") if d.strip()]
        priority = data.get("priority", 0)

        project = Project(
            name=name,
            path=str(Path(path).resolve()),
            priority=priority,
            depends_on=depends_on,
            status=STATUS_PENDING,
        )
        config.projects.append(project)
        self.state.save_queue(config)
        self._respond(201, {"ok": True, "project": project.to_dict()})

    def _api_priority(self, data: dict):
        name = data.get("name", "").strip()
        level = data.get("level")
        if not name or level is None:
            self._respond(400, {"error": "name and level are required"})
            return

        config = self.state.load_queue()
        project = _find_project(config, name)
        if not project:
            self._respond(404, {"error": f"project '{name}' not found"})
            return

        project.priority = int(level)
        self.state.save_queue(config)
        self._respond(200, {"ok": True, "priority": project.priority})

    # ---- static HTML ----

    def _serve_html(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(DASHBOARD_HTML.encode("utf-8"))

    # ---- helpers ----

    def _respond(self, code: int, data: dict):
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        """静默 — 不打印默认访问日志。"""
        pass


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def _find_project(config, name: str):
    for p in config.projects:
        if p.name == name:
            return p
    return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _format_uptime(state: RayStateDir) -> str:
    pid_file = state.pid_file
    if not pid_file.exists():
        return ""
    try:
        mtime = pid_file.stat().st_mtime
        elapsed = time.time() - mtime
        h, rem = divmod(int(elapsed), 3600)
        m, s = divmod(rem, 60)
        if h > 0:
            return f"{h}h{m:02d}m{s:02d}s"
        return f"{m}m{s:02d}s"
    except OSError:
        return ""


def _project_summary(project) -> dict:
    project_path = Path(project.path)
    sprints = _read_sprints(project_path)
    cost = _project_cost(project_path)
    return {
        **project.to_dict(),
        "sprint_completed": sprints["completed"],
        "sprint_total": sprints["total"],
        "current_stage": sprints["current_stage"],
        "cost": round(cost, 4),
        "eval_score": sprints["latest_eval"],
    }


def _project_detail(project) -> dict:
    project_path = Path(project.path)
    sprints = _read_sprints(project_path)
    cost = _project_cost(project_path)
    return {
        **project.to_dict(),
        "sprints": sprints["sprints"],
        "sprint_completed": sprints["completed"],
        "sprint_total": sprints["total"],
        "current_stage": sprints["current_stage"],
        "cost": round(cost, 4),
        "eval_score": sprints["latest_eval"],
    }


def _read_sprints(project_path: Path) -> dict:
    """读取 .chase/sprints/ 下的 sprint 状态。"""
    empty = {
        "total": 0, "completed": 0,
        "current_stage": "-", "latest_eval": None,
        "sprints": [],
    }
    sprints_dir = project_path / ".chase" / "sprints"
    if not sprints_dir.is_dir():
        return empty

    # 收集 sprint ID
    sprint_files = list(sprints_dir.glob("*-contract.md"))
    if not sprint_files:
        return empty

    sprint_ids = sorted(set(f.name.split("-")[0] for f in sprint_files))

    sprints = []
    completed = 0
    latest_eval = None

    for sid in sprint_ids:
        has_contract = (sprints_dir / f"{sid}-contract.md").exists()
        has_negotiated = (sprints_dir / f"{sid}-negotiated.md").exists()
        has_result = (sprints_dir / f"{sid}-result.md").exists()
        eval_file = sprints_dir / f"{sid}-eval.json"
        has_eval = eval_file.exists()
        eval_score = None

        if has_eval:
            try:
                eval_data = json.loads(eval_file.read_text(encoding="utf-8"))
                eval_score = eval_data.get("score")
                if eval_score is None:
                    eval_score = eval_data.get("overall_score")
            except (json.JSONDecodeError, OSError):
                pass
            if eval_score is not None:
                latest_eval = eval_score

        if has_eval:
            completed += 1
            stage = "Evaluator"
        elif has_result:
            stage = "Evaluator"
        elif has_negotiated:
            stage = "Generator"
        elif has_contract:
            stage = "Negotiator"
        else:
            stage = "Planner"

        sprints.append({
            "id": sid,
            "stage": stage,
            "has_contract": has_contract,
            "has_negotiated": has_negotiated,
            "has_result": has_result,
            "has_eval": has_eval,
            "eval_score": eval_score,
        })

    # 当前阶段 = 第一个未完成的 sprint
    current_stage = "-"
    for s in sprints:
        if not s["has_eval"]:
            current_stage = s["stage"]
            break

    return {
        "total": len(sprint_ids),
        "completed": completed,
        "current_stage": current_stage,
        "latest_eval": latest_eval,
        "sprints": sprints,
    }


def _project_cost(project_path: Path) -> float:
    """读取 .chase/logs/cost-tracking.json 的累计费用。"""
    cost_file = project_path / ".chase" / "logs" / "cost-tracking.json"
    if not cost_file.exists():
        return 0.0
    try:
        data = json.loads(cost_file.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return sum(float(e.get("cost_usd", 0)) for e in data)
        return float(
            data.get("total_cost_usd", data.get("cost_usd", 0))
        )
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------------------
# Embedded HTML
# ---------------------------------------------------------------------------

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Chase Ray Dashboard</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,monospace;background:#1a1a2e;color:#e0e0e0;min-height:100vh}
.header{background:#16213e;padding:16px 24px;border-bottom:1px solid #0f3460;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px}
.header h1{font-size:20px;color:#00d4ff;white-space:nowrap}
.header-info{display:flex;gap:20px;font-size:13px;flex-wrap:wrap;align-items:center}
.header-info span{color:#aaa}
.header-info .val{color:#00d4ff;font-weight:600}
.status-dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;vertical-align:middle}
.dot-running{background:#00ff88;box-shadow:0 0 6px #00ff88}
.dot-stopped{background:#ff4444}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:16px;padding:20px 24px}
.card{background:#16213e;border:1px solid #0f3460;border-radius:8px;padding:16px;transition:border-color .2s}
.card:hover{border-color:#00d4ff}
.card-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}
.card-name{font-size:16px;font-weight:600;color:#fff;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:70%}
.priority-badge{font-size:11px;padding:2px 8px;border-radius:10px;font-weight:600;flex-shrink:0}
.p0{background:#ff4444;color:#fff}
.p1{background:#ff9800;color:#000}
.p2{background:#4caf50;color:#000}
.p3{background:#666;color:#fff}
.status-line{display:flex;align-items:center;gap:8px;margin-bottom:10px;font-size:13px}
.status-indicator{width:10px;height:10px;border-radius:50%;flex-shrink:0}
.si-running{background:#00ff88;animation:pulse 1.5s infinite}
.si-completed{background:#00ff88}
.si-failed{background:#ff4444}
.si-pending{background:#666}
.si-paused{background:#ff9800}
.si-blocked{background:#ff9800}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
.progress-bar-bg{background:#0f3460;border-radius:4px;height:6px;margin:8px 0;overflow:hidden}
.progress-bar-fill{height:100%;background:linear-gradient(90deg,#00d4ff,#00ff88);border-radius:4px;transition:width .5s ease}
.card-stats{display:flex;justify-content:space-between;font-size:12px;color:#aaa;margin-top:8px}
.card-stats .val{color:#e0e0e0}
.eval-score{font-weight:600}
.eval-green{color:#00ff88}
.eval-yellow{color:#ff9800}
.eval-red{color:#ff4444}
.empty{text-align:center;padding:60px 20px;color:#666;font-size:14px}
.empty-icon{font-size:48px;margin-bottom:12px;opacity:.3}
@media(max-width:600px){
  .header{padding:12px 16px}
  .cards{padding:12px;gap:12px}
  .card{padding:12px}
  .header h1{font-size:17px}
}
</style>
</head>
<body>
<div class="header">
  <h1>⚡ Chase Ray</h1>
  <div class="header-info">
    <span><span class="status-dot" id="runDot"></span><span id="runStatus">—</span></span>
    <span>Uptime: <span class="val" id="uptime">—</span></span>
    <span>Parallel: <span class="val" id="maxParallel">—</span></span>
    <span>Cost: <span class="val" id="totalCost">$0.00</span></span>
  </div>
</div>
<div class="cards" id="cards"></div>
<div class="empty" id="empty" style="display:none">
  <div class="empty-icon">📋</div>
  <div>No projects in queue</div>
</div>
<script>
function evalColor(s){if(s==null)return'';if(s>=.9)return'eval-green';if(s>=.7)return'eval-yellow';return'eval-red'}
function priCls(p){if(p<=0)return'p0';if(p===1)return'p1';if(p===2)return'p2';return'p3'}
function fmtCost(c){return'$'+(c||0).toFixed(2)}
function esc(s){var d=document.createElement('div');d.textContent=s;return d.innerHTML}

function render(data){
  var dot=document.getElementById('runDot');
  dot.className='status-dot '+(data.running?'dot-running':'dot-stopped');
  document.getElementById('runStatus').textContent=data.running?'Running':'Stopped';
  document.getElementById('uptime').textContent=data.uptime||'—';
  document.getElementById('maxParallel').textContent=data.max_parallel;
  document.getElementById('totalCost').textContent=fmtCost(data.total_cost);

  var c=document.getElementById('cards');
  var e=document.getElementById('empty');
  if(!data.projects||!data.projects.length){c.innerHTML='';e.style.display='';return}
  e.style.display='none';

  var h='';
  for(var i=0;i<data.projects.length;i++){
    var p=data.projects[i];
    var pct=p.sprint_total>0?Math.round(p.sprint_completed/p.sprint_total*100):0;
    var evl=p.eval_score!=null?p.eval_score.toFixed(2):'—';
    var ec=evalColor(p.eval_score);
    h+='<div class="card">'
      +'<div class="card-header">'
      +'<span class="card-name" title="'+esc(p.path)+'">'+esc(p.name)+'</span>'
      +'<span class="priority-badge '+priCls(p.priority)+'">P'+p.priority+'</span>'
      +'</div>'
      +'<div class="status-line">'
      +'<span class="status-indicator si-'+p.status+'"></span>'
      +'<span>'+p.status+'</span>'
      +'<span style="margin-left:auto;color:#666">'+esc(p.current_stage||'-')+'</span>'
      +'</div>'
      +'<div class="progress-bar-bg"><div class="progress-bar-fill" style="width:'+pct+'%"></div></div>'
      +'<div class="card-stats">'
      +'<span>Sprints: <span class="val">'+(p.sprint_completed||0)+'/'+(p.sprint_total||0)+'</span></span>'
      +'<span>Cost: <span class="val">'+fmtCost(p.cost)+'</span></span>'
      +'<span>Eval: <span class="eval-score '+ec+'">'+evl+'</span></span>'
      +'</div></div>';
  }
  c.innerHTML=h;
}

// SSE auto-refresh
var es=new EventSource('/api/events');
es.onmessage=function(e){try{render(JSON.parse(e.data))}catch(ex){}};
es.onerror=function(){};

// Initial fetch
fetch('/api/status').then(function(r){return r.json()}).then(render).catch(function(){});
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------

class DashboardServer:
    """管理 Dashboard HTTP 服务器的生命周期。"""

    def __init__(self, state: RayStateDir, port: int = 8765):
        self.state = state
        self.port = port
        self._server: _ThreadedHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self, background: bool = False) -> None:
        DashboardHandler.state = self.state
        self._server = _ThreadedHTTPServer(("0.0.0.0", self.port), DashboardHandler)
        if background:
            self._thread = threading.Thread(
                target=self._server.serve_forever, daemon=True
            )
            self._thread.start()
        else:
            self._server.serve_forever()

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server = None


def start_dashboard(
    state: RayStateDir, port: int = 8765, background: bool = False
) -> DashboardServer:
    """创建并启动 Dashboard 服务器。"""
    server = DashboardServer(state, port)
    server.start(background=background)
    return server
