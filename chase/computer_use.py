"""Computer Use — browser automation via CDP (Chrome DevTools Protocol).

Zero-dependency implementation using only Python standard library.
Connects to Brave/Chrome remote debugging on localhost:9222.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import struct
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

CDP_HOST = "localhost"
CDP_PORT = 9222

# Keywords that indicate a web/UI-related sprint
WEB_KEYWORDS = ("ui", "web", "browser", "visual", "frontend", "page", "html", "css",
                "react", "vue", "svelte", "component", "render", "dom", "layout",
                "界面", "页面", "前端", "浏览器")


# ---------------------------------------------------------------------------
# Minimal WebSocket client (RFC 6455) — enough for CDP text commands
# ---------------------------------------------------------------------------

class _WebSocket:
    """Bare-minimum WebSocket client for CDP communication."""

    def __init__(self, host: str, port: int, path: str):
        self._host = host
        self._port = port
        self._path = path
        self._sock: socket.socket | None = None

    def connect(self, timeout: float = 10.0) -> None:
        sock = socket.create_connection((self._host, self._port), timeout=timeout)
        self._sock = sock

        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            f"GET {self._path} HTTP/1.1\r\n"
            f"Host: {self._host}:{self._port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"\r\n"
        )
        sock.sendall(req.encode())

        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError("WebSocket handshake failed — no response")
            resp += chunk

        status_line = resp.split(b"\r\n")[0]
        if b"101" not in status_line:
            raise ConnectionError(f"WebSocket upgrade rejected: {status_line!r}")

    def send(self, data: str) -> None:
        if self._sock is None:
            raise ConnectionError("Not connected")
        payload = data.encode("utf-8")
        mask_key = os.urandom(4)

        frame = bytearray()
        frame.append(0x81)  # FIN + text opcode

        length = len(payload)
        if length < 126:
            frame.append(0x80 | length)
        elif length < 65536:
            frame.append(0x80 | 126)
            frame.extend(struct.pack(">H", length))
        else:
            frame.append(0x80 | 127)
            frame.extend(struct.pack(">Q", length))

        frame.extend(mask_key)
        masked = bytearray(payload)
        for i in range(len(masked)):
            masked[i] ^= mask_key[i % 4]
        frame.extend(masked)

        self._sock.sendall(bytes(frame))

    def recv(self, timeout: float = 30.0) -> str:
        if self._sock is None:
            raise ConnectionError("Not connected")
        self._sock.settimeout(timeout)

        header = self._recv_exact(2)
        opcode = header[0] & 0x0F
        masked = bool(header[1] & 0x80)
        length = header[1] & 0x7F

        if length == 126:
            length = struct.unpack(">H", self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._recv_exact(8))[0]

        mask_key = self._recv_exact(4) if masked else b""
        payload = self._recv_exact(length)

        if masked:
            payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))

        # Handle control frames
        if opcode == 0x9:  # ping → pong
            self._send_raw(bytes([0x8A, len(payload)]) + payload)
            return self.recv(timeout)
        if opcode == 0x8:  # close
            raise ConnectionError("WebSocket closed by server")

        return payload.decode("utf-8")

    def close(self) -> None:
        if self._sock:
            try:
                self._sock.sendall(bytes([0x88, 0x00]))
            except Exception:
                pass
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    # -- helpers --

    def _recv_exact(self, n: int) -> bytes:
        data = b""
        while len(data) < n:
            chunk = self._sock.recv(n - len(data))
            if not chunk:
                raise ConnectionError("Connection closed")
            data += chunk
        return data

    def _send_raw(self, data: bytes) -> None:
        self._sock.sendall(data)


# ---------------------------------------------------------------------------
# CDP helpers
# ---------------------------------------------------------------------------

def _http_request(url: str, method: str = "GET", timeout: float = 5.0) -> str:
    """HTTP request via urllib, return body text."""
    req = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def _cdp_list_targets(host: str = CDP_HOST, port: int = CDP_PORT) -> list[dict]:
    """List open browser targets via CDP HTTP API."""
    try:
        body = _http_request(f"http://{host}:{port}/json")
        return json.loads(body)
    except Exception:
        return []


def _cdp_new_tab(url: str, host: str = CDP_HOST, port: int = CDP_PORT) -> dict:
    """Open a new tab and return its target info."""
    body = _http_request(f"http://{host}:{port}/json/new?{urllib.parse.quote(url, safe='')}", method="PUT")
    return json.loads(body)


def _cdp_close_tab(target_id: str, host: str = CDP_HOST, port: int = CDP_PORT) -> None:
    """Close a tab by target ID."""
    try:
        _http_request(f"http://{host}:{port}/json/close/{target_id}", method="PUT")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# BrowserSession — high-level CDP interface
# ---------------------------------------------------------------------------

class BrowserSession:
    """Manages a browser session via CDP. Use module-level functions for convenience."""

    def __init__(self, host: str = CDP_HOST, port: int = CDP_PORT):
        self._host = host
        self._port = port
        self._ws: _WebSocket | None = None
        self._target_id: str | None = None
        self._msg_id = 0
        self._browser_proc: subprocess.Popen | None = None

    @property
    def target_id(self) -> str | None:
        return self._target_id

    # -- lifecycle --

    def launch(self, url: str = "about:blank") -> "BrowserSession":
        """Start browser (if needed) and open URL. Returns self for chaining."""
        # Check if browser is already listening
        targets = _cdp_list_targets(self._host, self._port)
        if not targets:
            self._start_browser()

        # Open a new tab
        info = _cdp_new_tab(url, self._host, self._port)
        self._target_id = info.get("id")
        ws_url = info.get("webSocketDebuggerUrl")
        if not ws_url:
            raise ConnectionError("No webSocketDebuggerUrl in CDP response")

        # Parse ws://host:port/path
        ws_url = ws_url.replace("ws://", "").replace("wss://", "")
        parts = ws_url.split("/", 1)
        hp = parts[0].split(":")
        ws_host = hp[0]
        ws_port = int(hp[1]) if len(hp) > 1 else self._port
        ws_path = "/" + parts[1] if len(parts) > 1 else "/"

        self._ws = _WebSocket(ws_host, ws_port, ws_path)
        self._ws.connect()
        return self

    def close(self) -> None:
        """Close the WebSocket and browser tab."""
        if self._ws:
            self._ws.close()
            self._ws = None
        if self._target_id:
            _cdp_close_tab(self._target_id, self._host, self._port)
            self._target_id = None
        if self._browser_proc:
            self._browser_proc.terminate()
            self._browser_proc = None

    # -- CDP commands --

    def _send(self, method: str, params: dict | None = None, timeout: float = 30.0) -> dict:
        """Send a CDP command and return the result dict."""
        if not self._ws:
            raise ConnectionError("Not connected — call launch() first")

        self._msg_id += 1
        msg = {"id": self._msg_id, "method": method}
        if params:
            msg["params"] = params

        self._ws.send(json.dumps(msg))

        # Read until we get a response with matching id
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"CDP command timed out: {method}")
            raw = self._ws.recv(timeout=min(remaining, 30.0))
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue

            # Skip CDP events (no id field)
            if "id" not in data:
                continue
            if data["id"] == self._msg_id:
                if "error" in data:
                    raise RuntimeError(f"CDP error: {data['error']}")
                return data.get("result", {})

        raise TimeoutError(f"CDP command timed out: {method}")

    def navigate(self, url: str) -> dict:
        """Navigate to URL."""
        return self._send("Page.navigate", {"url": url})

    def screenshot(self, path: str | Path) -> Path:
        """Take a screenshot and save to *path*. Returns the Path."""
        result = self._send("Page.captureScreenshot", {"format": "png"})
        b64 = result.get("data", "")
        if not b64:
            raise RuntimeError("Screenshot returned no data")
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(base64.b64decode(b64))
        return p

    def click(self, selector: str) -> dict:
        """Click the first element matching *selector*."""
        # Use JS to get coordinates directly — avoids expensive DOM.getDocument(depth=-1)
        js = f"""
        (function() {{
            var el = document.querySelector({json.dumps(selector)});
            if (!el) return null;
            var rect = el.getBoundingClientRect();
            return {{x: rect.left + rect.width/2, y: rect.top + rect.height/2}};
        }})()
        """
        result = self._send("Runtime.evaluate", {
            "expression": js,
            "returnByValue": True,
        })
        coords = result.get("result", {}).get("value")
        if not coords or "x" not in coords:
            raise ValueError(f"Element not found: {selector}")

        x, y = coords["x"], coords["y"]

        # Dispatch mouse events
        for event_type in ("mousePressed", "mouseReleased"):
            self._send("Input.dispatchMouseEvent", {
                "type": event_type,
                "x": x, "y": y,
                "button": "left",
                "clickCount": 1,
            })
        return {"clicked": selector, "x": x, "y": y}

    def type_text(self, selector: str, text: str) -> dict:
        """Focus element matching *selector* and type *text* character by character."""
        # Focus
        doc = self._send("DOM.getDocument", {"depth": -1})
        root_id = doc["root"]["nodeId"]
        qr = self._send("DOM.querySelector", {"nodeId": root_id, "selector": selector})
        node_id = qr.get("nodeId", 0)
        if not node_id:
            raise ValueError(f"Element not found: {selector}")
        self._send("DOM.focus", {"nodeId": node_id})

        # Type each character
        for ch in text:
            self._send("Input.dispatchKeyEvent", {
                "type": "keyDown",
                "text": ch,
            })
            self._send("Input.dispatchKeyEvent", {
                "type": "keyUp",
                "text": ch,
            })
        return {"typed": text, "selector": selector}

    def get_page_content(self) -> str:
        """Return the visible text content of the current page."""
        result = self._send("Runtime.evaluate", {
            "expression": "document.body ? document.body.innerText : ''",
            "returnByValue": True,
        })
        return result.get("result", {}).get("value", "")

    def evaluate_js(self, script: str) -> Any:
        """Execute JavaScript and return the result value."""
        result = self._send("Runtime.evaluate", {
            "expression": script,
            "returnByValue": True,
        })
        return result.get("result", {}).get("value")

    # -- private --

    def _start_browser(self) -> None:
        """Try to launch Brave or Chrome with remote debugging enabled."""
        candidates = [
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "brave-browser",
            "google-chrome",
            "chromium",
        ]
        for cmd in candidates:
            try:
                proc = subprocess.Popen(
                    [cmd, f"--remote-debugging-port={self._port}", "--no-first-run", "--no-default-browser-check"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                # Wait for CDP to become available
                for _ in range(20):
                    time.sleep(0.5)
                    if _cdp_list_targets(self._host, self._port):
                        self._browser_proc = proc
                        return
                proc.terminate()
            except FileNotFoundError:
                continue
        raise RuntimeError("Could not start browser — install Brave or Chrome")


# ---------------------------------------------------------------------------
# Module-level convenience API
# ---------------------------------------------------------------------------

_session: BrowserSession | None = None


def launch_browser(url: str, host: str = CDP_HOST, port: int = CDP_PORT) -> BrowserSession:
    """Launch browser and navigate to *url*. Returns a BrowserSession."""
    global _session
    _session = BrowserSession(host, port).launch(url)
    return _session


def screenshot(path: str | Path) -> Path:
    """Take a screenshot of the current page."""
    if not _session:
        raise RuntimeError("No active browser session — call launch_browser() first")
    return _session.screenshot(path)


def click(selector: str) -> dict:
    """Click element matching CSS *selector*."""
    if not _session:
        raise RuntimeError("No active browser session")
    return _session.click(selector)


def type_text(selector: str, text: str) -> dict:
    """Type *text* into element matching CSS *selector*."""
    if not _session:
        raise RuntimeError("No active browser session")
    return _session.type_text(selector, text)


def get_page_content() -> str:
    """Get visible text content of the current page."""
    if not _session:
        raise RuntimeError("No active browser session")
    return _session.get_page_content()


def evaluate_js(script: str) -> Any:
    """Execute JavaScript in the browser."""
    if not _session:
        raise RuntimeError("No active browser session")
    return _session.evaluate_js(script)


def close_browser() -> None:
    """Close the current browser session."""
    global _session
    if _session:
        _session.close()
        _session = None


def is_web_sprint(contract_text: str) -> bool:
    """Check whether a sprint contract involves web/UI work."""
    lower = contract_text.lower()
    return any(kw in lower for kw in WEB_KEYWORDS)


def run_browser_verification(app_url: str, screenshot_path: str | Path,
                             host: str = CDP_HOST, port: int = CDP_PORT) -> dict:
    """High-level helper: navigate to *app_url*, screenshot, get content.

    Returns dict with keys: screenshot_path, page_content, error (if any).
    """
    result: dict[str, Any] = {"screenshot_path": None, "page_content": None, "error": None}
    try:
        session = launch_browser(app_url, host, port)
        time.sleep(2)  # Wait for page load

        result["screenshot_path"] = str(session.screenshot(screenshot_path))
        result["page_content"] = session.get_page_content()
    except Exception as exc:
        result["error"] = str(exc)
    finally:
        close_browser()
    return result


def run_interaction_test(
    steps: list[dict],
    base_url: str,
    screenshot_dir: str | Path,
    host: str = CDP_HOST,
    port: int = CDP_PORT,
) -> dict:
    """Run a multi-step interaction test in the browser.

    Args:
        steps: List of interaction steps, each a dict with:
            - "action": "navigate" | "click" | "type" | "wait" | "screenshot" | "scroll"
            - "selector": CSS selector (for click/type)
            - "value": text to type (for type), or URL (for navigate)
            - "label": human-readable step name (for logging)
            - "wait_ms": milliseconds to wait after action (default 1000)
        base_url: App base URL (e.g., "http://localhost:3000")
        screenshot_dir: Directory to save step screenshots
        host: CDP host
        port: CDP port

    Returns:
        dict with keys:
            "steps": list of step results, each with:
                "action", "label", "screenshot_path", "page_content", "error"
            "error": overall error if any
    """
    screenshot_dir = Path(screenshot_dir)
    screenshot_dir.mkdir(parents=True, exist_ok=True)

    result: dict[str, Any] = {"steps": [], "error": None}

    if not steps:
        return result

    session: BrowserSession | None = None
    try:
        # Start browser session — navigate to base_url
        first_nav = ""
        for s in steps:
            if s.get("action") == "navigate":
                first_nav = s.get("value", "")
                if first_nav and not first_nav.startswith("http"):
                    first_nav = base_url.rstrip("/") + "/" + first_nav.lstrip("/")
                break
        start_url = first_nav or base_url
        session = BrowserSession(host, port).launch(start_url)
        time.sleep(2)  # Wait for initial page load

        for i, step in enumerate(steps):
            action = step.get("action", "")
            label = step.get("label", action)
            wait_ms = step.get("wait_ms", 1000)
            step_result: dict[str, Any] = {
                "action": action,
                "label": label,
                "screenshot_path": None,
                "page_content": None,
                "error": None,
            }

            try:
                if action == "navigate":
                    url = step.get("value", "")
                    if url and not url.startswith("http"):
                        url = base_url.rstrip("/") + "/" + url.lstrip("/")
                    session.navigate(url)
                    time.sleep(wait_ms / 1000.0)

                elif action == "click":
                    selector = step.get("selector", "")
                    session.click(selector)
                    time.sleep(wait_ms / 1000.0)

                elif action == "type":
                    selector = step.get("selector", "")
                    text = step.get("value", "")
                    session.type_text(selector, text)
                    time.sleep(wait_ms / 1000.0)

                elif action == "wait":
                    ms = step.get("wait_ms", 1000)
                    time.sleep(ms / 1000.0)

                elif action == "screenshot":
                    pass  # Just capture screenshot below

                elif action == "scroll":
                    session.evaluate_js("window.scrollBy(0, window.innerHeight)")
                    time.sleep(wait_ms / 1000.0)

            except Exception as exc:
                step_result["error"] = str(exc)

            # Capture screenshot for every step
            try:
                shot_path = screenshot_dir / f"{i:02d}.png"
                session.screenshot(shot_path)
                step_result["screenshot_path"] = str(shot_path)
            except Exception as exc:
                if not step_result["error"]:
                    step_result["error"] = f"Screenshot failed: {exc}"

            # Capture page content
            try:
                step_result["page_content"] = session.get_page_content()
            except Exception:
                pass

            result["steps"].append(step_result)

    except Exception as exc:
        result["error"] = str(exc)
    finally:
        if session:
            session.close()

    return result
