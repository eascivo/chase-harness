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


def _safe_read(path: Path) -> str | None:
    """Safely read a text file, return None if missing or unreadable."""
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _safe_read_json(path: Path) -> dict | list | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _read_mission(project_path: Path) -> str | None:
    """读取项目 MISSION.md（优先项目根目录，备选 .chase/mission.md）。"""
    for candidate in [project_path / "MISSION.md", project_path / ".chase" / "mission.md"]:
        content = _safe_read(candidate)
        if content:
            return content
    return None


def _project_detail(project) -> dict:
    project_path = Path(project.path)
    sprints_meta = _read_sprints(project_path)
    cost = _project_cost(project_path)
    mission = _read_mission(project_path)
    cost_detail = _safe_read_json(project_path / ".chase" / "logs" / "cost-tracking.json")

    # Enrich sprints with file contents
    sprints_dir = project_path / ".chase" / "sprints"
    enriched = []
    for s in sprints_meta["sprints"]:
        sid = s["id"]
        entry = {**s}
        entry["contract"] = _safe_read(sprints_dir / f"{sid}-contract.md")
        entry["negotiated"] = _safe_read(sprints_dir / f"{sid}-negotiated.md")
        entry["result"] = _safe_read(sprints_dir / f"{sid}-result.md")
        entry["eval"] = _safe_read_json(sprints_dir / f"{sid}-eval.json")
        enriched.append(entry)

    return {
        **project.to_dict(),
        "mission": mission,
        "sprints": enriched,
        "sprint_completed": sprints_meta["completed"],
        "sprint_total": sprints_meta["total"],
        "current_stage": sprints_meta["current_stage"],
        "cost": round(cost, 4),
        "eval_score": sprints_meta["latest_eval"],
        "cost_detail": cost_detail,
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
.header h1{font-size:20px;color:#00d4ff;white-space:nowrap;cursor:pointer}
.header-info{display:flex;gap:20px;font-size:13px;flex-wrap:wrap;align-items:center}
.header-info span{color:#aaa}
.header-info .val{color:#00d4ff;font-weight:600}
.status-dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;vertical-align:middle}
.dot-running{background:#00ff88;box-shadow:0 0 6px #00ff88}
.dot-stopped{background:#ff4444}
/* Overview cards */
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:16px;padding:20px 24px}
.card{background:#16213e;border:1px solid #0f3460;border-radius:8px;padding:16px;transition:border-color .2s,box-shadow .2s,transform .15s;cursor:pointer}
.card:hover{border-color:#00d4ff;box-shadow:0 0 12px rgba(0,212,255,.15);transform:translateY(-1px)}
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
.card-sprint-tag{font-size:11px;color:#00d4ff;margin-left:6px}
.eval-score{font-weight:600}
.eval-green{color:#00ff88}
.eval-yellow{color:#ff9800}
.eval-red{color:#ff4444}
.empty{text-align:center;padding:60px 20px;color:#666;font-size:14px}
.empty-icon{font-size:48px;margin-bottom:12px;opacity:.3}
/* Detail view */
#detailView{display:none;padding:20px 24px;max-width:900px;margin:0 auto}
.detail-top{display:flex;align-items:center;gap:16px;margin-bottom:24px;flex-wrap:wrap}
.btn-back{background:#0f3460;border:1px solid #1a3a6e;color:#00d4ff;padding:8px 16px;border-radius:6px;cursor:pointer;font-size:13px;font-family:inherit;transition:background .2s}
.btn-back:hover{background:#1a3a6e}
.detail-title{font-size:22px;font-weight:700;color:#fff}
.detail-meta{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:24px}
.dm-item{font-size:13px;color:#aaa}
.dm-item .val{color:#e0e0e0;font-weight:600}
.detail-section{margin-bottom:28px}
.detail-section-title{font-size:15px;font-weight:600;color:#00d4ff;margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid #0f3460}
.md-content{font-size:14px;line-height:1.7;color:#ccc;padding:12px 16px;background:#16213e;border-radius:6px;border:1px solid #0f3460}
.md-content h1,.md-content h2,.md-content h3{color:#fff;margin:12px 0 6px;font-weight:600}
.md-content h1{font-size:18px;border-bottom:1px solid #0f3460;padding-bottom:4px}
.md-content h2{font-size:16px}
.md-content h3{font-size:14px}
.md-content p{margin:6px 0}
.md-content strong{color:#fff}
.md-content code{background:#0f3460;padding:1px 5px;border-radius:3px;font-size:13px;color:#00d4ff}
.md-content pre{background:#0d1b2a;border:1px solid #0f3460;border-radius:4px;padding:12px;overflow-x:auto;margin:8px 0}
.md-content pre code{background:none;padding:0;color:#ccc}
.md-content ul,.md-content ol{margin:6px 0 6px 20px}
.md-content li{margin:3px 0}
/* Sprint timeline */
.timeline{position:relative;padding-left:28px}
.timeline::before{content:'';position:absolute;left:10px;top:4px;bottom:4px;width:2px;background:#0f3460}
.sprint-node{position:relative;margin-bottom:16px}
.sprint-dot{position:absolute;left:-24px;top:14px;width:12px;height:12px;border-radius:50%;border:2px solid #0f3460;background:#1a1a2e;z-index:1}
.sprint-dot.completed{background:#00ff88;border-color:#00ff88}
.sprint-dot.running{background:#00d4ff;border-color:#00d4ff;animation:pulse 1.5s infinite}
.sprint-dot.failed{background:#ff4444;border-color:#ff4444}
.sprint-dot.pending{background:#666;border-color:#666}
.sprint-card{background:#16213e;border:1px solid #0f3460;border-radius:6px;overflow:hidden}
.sprint-header{padding:12px 16px;cursor:pointer;display:flex;justify-content:space-between;align-items:center;transition:background .15s}
.sprint-header:hover{background:#1a2744}
.sprint-header-left{display:flex;align-items:center;gap:10px}
.sprint-id{font-weight:600;color:#fff;font-size:14px}
.sprint-stage{font-size:12px;color:#aaa}
.sprint-header-right{display:flex;align-items:center;gap:12px;font-size:13px}
.sprint-eval{font-weight:600}
.sprint-cost{color:#aaa}
.sprint-toggle{color:#666;font-size:12px;transition:transform .2s}
.sprint-toggle.open{transform:rotate(180deg)}
.sprint-body{display:none;padding:0 16px 16px}
.sprint-body.open{display:block}
.sprint-subsection{margin-bottom:12px}
.sprint-subsection-title{font-size:12px;font-weight:600;color:#666;text-transform:uppercase;margin-bottom:6px;letter-spacing:.5px}
.sprint-subsection .md-content{font-size:13px;padding:10px 12px}
.result-truncate{max-height:120px;overflow:hidden;position:relative;transition:max-height .3s}
.result-truncate.expanded{max-height:none}
.result-fade{position:absolute;bottom:0;left:0;right:0;height:40px;background:linear-gradient(transparent,#16213e);pointer-events:none}
.result-expand-btn{font-size:11px;color:#00d4ff;cursor:pointer;margin-top:4px;display:inline-block}
.result-expand-btn:hover{text-decoration:underline}
.eval-card{background:#0d1b2a;border:1px solid #0f3460;border-radius:4px;padding:10px 12px;font-size:13px}
.eval-verdict{font-weight:700;font-size:14px;margin-bottom:4px}
.eval-verdict.pass{color:#00ff88}
.eval-verdict.fail{color:#ff4444}
.eval-feedback{color:#aaa;margin-top:6px;line-height:1.5}
.eval-criteria{margin-top:8px;padding-left:16px}
.eval-criteria li{margin:3px 0;font-size:12px}
.eval-criteria li.pass{color:#00ff88}
.eval-criteria li.fail{color:#ff4444}
/* Cost bar */
.cost-bar{background:#16213e;border:1px solid #0f3460;border-radius:6px;padding:14px 18px;display:flex;justify-content:space-between;align-items:center;font-size:14px}
.cost-bar .total{font-size:18px;font-weight:700;color:#00d4ff}
/* Spinner */
.spinner{display:inline-block;width:16px;height:16px;border:2px solid #0f3460;border-top-color:#00d4ff;border-radius:50%;animation:spin .6s linear infinite;vertical-align:middle}
@keyframes spin{to{transform:rotate(360deg)}}
.loading{text-align:center;padding:60px;color:#666}
@media(max-width:600px){
  .header{padding:12px 16px}
  .cards{padding:12px;gap:12px}
  .card{padding:12px}
  .header h1{font-size:17px}
  #detailView{padding:16px 12px}
  .detail-top{gap:10px}
  .detail-title{font-size:18px}
}
</style>
</head>
<body>
<div class="header">
  <h1 id="headerTitle" onclick="navigateTo('')">⚡ Chase Ray</h1>
  <div class="header-info" id="headerInfo">
    <span><span class="status-dot" id="runDot"></span><span id="runStatus">—</span></span>
    <span>Uptime: <span class="val" id="uptime">—</span></span>
    <span>Parallel: <span class="val" id="maxParallel">—</span></span>
    <span>Cost: <span class="val" id="totalCost">$0.00</span></span>
  </div>
</div>
<div id="overviewView">
  <div class="cards" id="cards"></div>
  <div class="empty" id="empty" style="display:none">
    <div class="empty-icon">📋</div>
    <div>No projects in queue</div>
  </div>
</div>
<div id="detailView"></div>
<script>
/* ---- State ---- */
var _overviewData = null;

/* ---- Utilities ---- */
function evalColor(s){if(s==null)return'';if(s>=.9)return'eval-green';if(s>=.7)return'eval-yellow';return'eval-red'}
function priCls(p){if(p<=0)return'p0';if(p===1)return'p1';if(p===2)return'p2';return'p3'}
function fmtCost(c){return'$'+(c||0).toFixed(2)}
function esc(s){var d=document.createElement('div');d.textContent=s;return d.innerHTML}
function sprintIcon(s){
  if(s==='completed')return'<span style="color:#00ff88">&#10003;</span>';
  if(s==='running'||s==='Generator'||s==='Evaluator'||s==='Negotiator')return'<span style="color:#00d4ff">&#8635;</span>';
  if(s==='failed')return'<span style="color:#ff4444">&#10007;</span>';
  return'<span style="color:#666">&#9679;</span>';
}
function sprintDotCls(hasEval,hasResult,stage){
  if(hasEval)return'completed';
  if(hasResult)return'running';
  if(stage==='failed')return'failed';
  return'pending';
}

/* ---- Simple Markdown Renderer ---- */
function renderMd(text){
  if(!text)return'';
  var html=esc(text);
  // Code blocks (```...```)
  html=html.replace(/```(\w*)\n([\s\S]*?)```/g,function(m,lang,code){
    return'<pre><code>'+code.trim()+'</code></pre>';
  });
  // Inline code
  html=html.replace(/`([^`]+)`/g,'<code>$1</code>');
  // Headers
  html=html.replace(/^### (.+)$/gm,'<h3>$1</h3>');
  html=html.replace(/^## (.+)$/gm,'<h2>$1</h2>');
  html=html.replace(/^# (.+)$/gm,'<h1>$1</h1>');
  // Bold
  html=html.replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>');
  // Ordered lists (consecutive lines)
  html=html.replace(/((?:^\d+\. .+\n?)+)/gm,function(block){
    var items=block.trim().split('\n').map(function(l){
      return'<li>'+l.replace(/^\d+\.\s+/,'')+'</li>';
    }).join('');
    return'<ol>'+items+'</ol>';
  });
  // Unordered lists
  html=html.replace(/((?:^[-*] .+\n?)+)/gm,function(block){
    var items=block.trim().split('\n').map(function(l){
      return'<li>'+l.replace(/^[-*]\s+/,'')+'</li>';
    }).join('');
    return'<ul>'+items+'</ul>';
  });
  // Paragraphs: double newline
  html=html.replace(/\n{2,}/g,'</p><p>');
  // Single newline → <br> (but not inside pre/ul/ol/h tags)
  html=html.replace(/(?<!<\/li>|<\/pre>|<\/h[123]>)\n(?!<)/g,'<br>');
  return'<p>'+html+'</p>';
}

/* ---- SPA Router ---- */
function navigateTo(route){
  if(!route||route===''){
    history.pushState(null,null,'/');
    showOverview();
  }else if(route.startsWith('project/')){
    var name=route.substring('project/'.length);
    history.pushState(null,null,'/project/'+encodeURIComponent(name));
    showDetail(name);
  }
}
window.addEventListener('popstate',function(){
  var path=decodeURIComponent(location.pathname).replace(/\/$/,'');
  if(!path||path==='/')showOverview();
  else if(path.startsWith('/project/'))showDetail(path.substring('/project/'.length));
});

/* ---- Overview ---- */
function showOverview(){
  document.getElementById('overviewView').style.display='';
  document.getElementById('detailView').style.display='none';
  document.title='Chase Ray Dashboard';
  if(_overviewData)renderOverview(_overviewData);
}

function renderOverview(data){
  _overviewData=data;
  if(document.getElementById('detailView').style.display!=='none')return;
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
    var sprintTag=(p.sprint_completed||0)<(p.sprint_total||0)?'Sprint '+(p.sprint_completed+1)+'/'+p.sprint_total:'';
    h+='<div class="card" onclick="navigateTo(\'project/'+esc(p.name)+'\')">'
      +'<div class="card-header">'
      +'<span class="card-name" title="'+esc(p.path)+'">'+esc(p.name)+'</span>'
      +'<span class="priority-badge '+priCls(p.priority)+'">P'+p.priority+'</span>'
      +'</div>'
      +'<div class="status-line">'
      +'<span class="status-indicator si-'+p.status+'"></span>'
      +'<span>'+p.status+'</span>'
      +'<span class="card-sprint-tag">'+esc(sprintTag)+'</span>'
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

/* ---- Detail View ---- */
function showDetail(name){
  document.getElementById('overviewView').style.display='none';
  document.getElementById('detailView').style.display='';
  document.getElementById('headerInfo').style.display='none';
  document.title=name+' - Chase Ray';
  var dv=document.getElementById('detailView');
  dv.innerHTML='<div class="loading"><span class="spinner"></span> Loading...</div>';

  fetch('/api/project/'+encodeURIComponent(name))
    .then(function(r){return r.json()})
    .then(function(p){renderDetail(p)})
    .catch(function(err){dv.innerHTML='<div class="loading">Failed to load: '+esc(err.message)+'</div>'});
}

function renderDetail(p){
  document.title=p.name+' - Chase Ray';
  var evl=p.eval_score!=null?p.eval_score.toFixed(2):'—';
  var ec=evalColor(p.eval_score);
  var pct=p.sprint_total>0?Math.round(p.sprint_completed/p.sprint_total*100):0;

  var html='<div class="detail-top">'
    +'<button class="btn-back" onclick="navigateTo(\'\')">&#8592; Back</button>'
    +'<span class="detail-title">'+esc(p.name)+'</span>'
    +'<span class="priority-badge '+priCls(p.priority)+'">P'+p.priority+'</span>'
    +'</div>';

  // Meta
  html+='<div class="detail-meta">'
    +'<span class="dm-item">Status: <span class="val"><span class="status-indicator si-'+p.status+'" style="display:inline-block;vertical-align:middle;width:8px;height:8px"></span> '+p.status+'</span></span>'
    +'<span class="dm-item">Stage: <span class="val">'+esc(p.current_stage||'-')+'</span></span>'
    +'<span class="dm-item">Sprints: <span class="val">'+p.sprint_completed+'/'+p.sprint_total+'</span></span>'
    +'<span class="dm-item">Eval: <span class="eval-score '+ec+'">'+evl+'</span></span>'
    +'<span class="dm-item">Cost: <span class="val">'+fmtCost(p.cost)+'</span></span>'
    +'</div>';

  // Progress
  html+='<div class="progress-bar-bg" style="margin-bottom:24px"><div class="progress-bar-fill" style="width:'+pct+'%"></div></div>';

  // MISSION
  if(p.mission){
    html+='<div class="detail-section">'
      +'<div class="detail-section-title">MISSION</div>'
      +'<div class="md-content">'+renderMd(p.mission)+'</div>'
      +'</div>';
  }

  // Sprint Timeline
  if(p.sprints&&p.sprints.length){
    html+='<div class="detail-section">'
      +'<div class="detail-section-title">Sprint Timeline</div>'
      +'<div class="timeline">';
    for(var i=0;i<p.sprints.length;i++){
      var s=p.sprints[i];
      var dotCls=sprintDotCls(s.has_eval,s.has_result,s.stage);
      var sEval=s.eval_score!=null?s.eval_score.toFixed(2):'—';
      var sEc=evalColor(s.eval_score);
      var sCost=s.eval&&s.eval.cost!=null?fmtCost(s.eval.cost):(s.cost!=null?fmtCost(s.cost):'');

      html+='<div class="sprint-node">'
        +'<div class="sprint-dot '+dotCls+'"></div>'
        +'<div class="sprint-card">'
        +'<div class="sprint-header" onclick="toggleSprint(this)">'
        +'<div class="sprint-header-left">'
        +'<span class="sprint-id">'+sprintIcon(s.stage)+' Sprint '+s.id+'</span>'
        +'<span class="sprint-stage">'+esc(s.stage)+'</span>'
        +'</div>'
        +'<div class="sprint-header-right">'
        +(sEval!=='—'?'<span class="sprint-eval '+sEc+'">'+sEval+'</span>':'')
        +(sCost?'<span class="sprint-cost">'+sCost+'</span>':'')
        +'<span class="sprint-toggle">&#9660;</span>'
        +'</div></div>'
        +'<div class="sprint-body">';

      // Contract
      if(s.contract){
        html+='<div class="sprint-subsection"><div class="sprint-subsection-title">&#128203; Contract</div><div class="md-content">'+renderMd(s.contract)+'</div></div>';
      }
      // Negotiated
      if(s.negotiated){
        html+='<div class="sprint-subsection"><div class="sprint-subsection-title">&#9989; Acceptance Criteria</div><div class="md-content">'+renderMd(s.negotiated)+'</div></div>';
      }
      // Result
      if(s.result){
        var rid='result-'+p.name+'-'+s.id;
        html+='<div class="sprint-subsection"><div class="sprint-subsection-title">&#128296; Result</div>'
          +'<div class="md-content"><div class="result-truncate" id="'+rid+'">'+renderMd(s.result)+'</div>'
          +'<div class="result-fade" id="'+rid+'-fade"></div>'
          +'<span class="result-expand-btn" onclick="expandResult(\''+rid+'\')">Show full text</span>'
          +'</div></div>';
      }
      // Eval
      if(s.eval){
        var ev=s.eval;
        var verdict=ev.verdict||(ev.score!=null&&ev.score>=0.9?'PASS':'FAIL');
        html+='<div class="sprint-subsection"><div class="sprint-subsection-title">&#128202; Evaluation</div>'
          +'<div class="eval-card">'
          +'<div class="eval-verdict '+(verdict==='PASS'?'pass':'fail')+'">'+esc(verdict)
          +(ev.score!=null?' — '+ev.score.toFixed(2):'')+'</div>';
        if(ev.feedback)html+='<div class="eval-feedback">'+esc(ev.feedback)+'</div>';
        if(ev.criteria&&ev.criteria.length){
          html+='<ul class="eval-criteria">';
          for(var j=0;j<ev.criteria.length;j++){
            var cr=ev.criteria[j];
            var cp=cr.passed!==false;
            html+='<li class="'+(cp?'pass':'fail')+'">'+esc(cr.name||cr.criterion||cr)+'</li>';
          }
          html+='</ul>';
        }
        html+='</div></div>';
      }

      html+='</div></div></div>';
    }
    html+='</div></div>';
  }

  // Cost bar
  html+='<div class="detail-section"><div class="cost-bar">'
    +'<span>Total Cost</span>'
    +'<span class="total">'+fmtCost(p.cost)+'</span>'
    +'</div></div>';

  document.getElementById('detailView').innerHTML=html;
}

function toggleSprint(headerEl){
  var body=headerEl.nextElementSibling;
  var toggle=headerEl.querySelector('.sprint-toggle');
  var isOpen=body.classList.contains('open');
  body.classList.toggle('open');
  toggle.classList.toggle('open');
}

function expandResult(rid){
  var el=document.getElementById(rid);
  var fade=document.getElementById(rid+'-fade');
  var btn=el.parentElement.querySelector('.result-expand-btn');
  if(el.classList.contains('expanded')){
    el.classList.remove('expanded');
    if(fade)fade.style.display='';
    if(btn)btn.textContent='Show full text';
  }else{
    el.classList.add('expanded');
    if(fade)fade.style.display='none';
    if(btn)btn.textContent='Collapse';
  }
}

/* ---- SSE + Init ---- */
var es=new EventSource('/api/events');
es.onmessage=function(e){try{renderOverview(JSON.parse(e.data))}catch(ex){}};
es.onerror=function(){};

// Initial route
(function(){
  var path=decodeURIComponent(location.pathname).replace(/\/$/,'')||'/';
  if(path.startsWith('/project/')){
    fetch('/api/status').then(function(r){return r.json()}).then(renderOverview).catch(function(){});
    showDetail(path.substring('/project/'.length));
  }else{
    fetch('/api/status').then(function(r){return r.json()}).then(renderOverview).catch(function(){});
  }
})();
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
