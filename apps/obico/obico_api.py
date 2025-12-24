#!/usr/bin/env python3
"""Obico HTTP API for Rinkhals - provides link status and control endpoints."""

import json, os, re, subprocess, sys, threading, time
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Dict, Any, Optional

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
RINKHALS_HOME = os.environ.get("RINKHALS_HOME", "/useremain/home/rinkhals")
OBICO_CFG = f"{RINKHALS_HOME}/printer_data/config/moonraker-obico.cfg"
LINK_SH = f"{APP_ROOT}/link.sh"
LINK_LOG = f"{RINKHALS_HOME}/printer_data/logs/obico-link.log"
API_PORT = 7136

current_link_code: str = ""
link_process: Optional[subprocess.Popen] = None


def log(msg: str) -> None:
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}: {msg}", flush=True)


def set_app_property(prop: str, value: str) -> None:
    try:
        subprocess.run(
            [
                "sh",
                "-c",
                f". /useremain/rinkhals/.current/tools.sh && set_app_property obico {prop} '{value}'",
            ],
            timeout=5,
            capture_output=True,
        )
    except Exception as e:
        log(f"set_app_property error: {e}")


def read_config() -> Dict[str, Dict[str, str]]:
    if not os.path.exists(OBICO_CFG):
        return {}
    config: Dict[str, Dict[str, str]] = {}
    section = ""
    with open(OBICO_CFG) as f:
        for line in f:
            line = line.strip()
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1]
                config[section] = {}
            elif "=" in line and section:
                k, v = line.split("=", 1)
                config[section][k.strip()] = v.strip()
    return config


def get_status() -> Dict[str, Any]:
    server = read_config().get("server", {})
    is_linked = bool(server.get("auth_token", ""))
    return {
        "is_linked": is_linked,
        "link_code": "" if is_linked else current_link_code,
        "server_url": server.get("url", "https://app.obico.io"),
    }


def strip_ansi(text: str) -> str:
    text = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", text)
    text = re.sub(r"\[[\d;]*m", "", text)
    return text


def parse_link_code(text: str) -> str:
    match = re.search(r"manual linking and enter:\s+(\w{4,6})", strip_ansi(text))
    return match.group(1) if match else ""


def is_link_running() -> bool:
    return link_process is not None and link_process.poll() is None


def run_link_background(relink: bool = False) -> None:
    global current_link_code, link_process
    if is_link_running():
        return

    status = get_status()
    if status["is_linked"] and not relink:
        set_app_property("is_linked", "Linked")
        set_app_property("link_code", "-")
        return

    if relink and os.path.exists(OBICO_CFG):
        with open(OBICO_CFG) as f:
            lines = [l for l in f if "auth_token" not in l]
        with open(OBICO_CFG, "w") as f:
            f.writelines(lines)

    current_link_code = ""
    set_app_property("is_linked", "Linking...")
    set_app_property("link_code", "Starting...")

    def monitor():
        global current_link_code, link_process
        try:
            cmd = ["sh", LINK_SH] + (["--relink"] if relink else [])
            log_file = open(LINK_LOG, "w")
            link_process = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=log_file, stderr=log_file
            )

            while link_process.poll() is None:
                time.sleep(1)
                if os.path.exists(LINK_LOG):
                    with open(LINK_LOG) as f:
                        code = parse_link_code(f.read())
                    if code and code != current_link_code:
                        current_link_code = code
                        set_app_property("link_code", code)
                        set_app_property("is_linked", "Use code")

            status = get_status()
            if status["is_linked"]:
                current_link_code = ""
                set_app_property("is_linked", "Linked")
                set_app_property("link_code", "-")
            elif current_link_code:
                set_app_property("is_linked", "Use code")
                set_app_property("link_code", current_link_code)
            else:
                set_app_property("is_linked", "Not linked")
        except Exception as e:
            log(f"Link error: {e}")
            set_app_property("is_linked", "Error")
        finally:
            if link_process and link_process.stdin:
                try:
                    link_process.stdin.close()
                except:
                    pass
            try:
                log_file.close()
            except:
                pass
            link_process = None

    threading.Thread(target=monitor, daemon=True).start()


def init_link_status() -> None:
    if not os.path.exists(OBICO_CFG):
        set_app_property("is_linked", "No config")
        return
    status = get_status()
    if status["is_linked"]:
        set_app_property("is_linked", "Linked")
        set_app_property("link_code", "-")
    elif not is_link_running():
        run_link_background()


class APIHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_json(self, data: Dict, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def do_OPTIONS(self) -> None:
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path in ("/", "/status"):
            self.send_json(get_status())
        elif path == "/health":
            self.send_json({"status": "ok"})
        else:
            self.send_json({"error": "Not found"}, 404)

    def do_POST(self) -> None:
        path = self.path.split("?")[0]
        if path == "/link":
            if not is_link_running():
                run_link_background()
            self.send_json({"started": True, **get_status()})
        elif path == "/relink":
            run_link_background(relink=True)
            self.send_json({"started": True, **get_status()})
        else:
            self.send_json({"error": "Not found"}, 404)


class ReusableHTTPServer(HTTPServer):
    allow_reuse_address = True


if __name__ == "__main__":
    log("=" * 50)
    log(f"Obico API starting")
    log(f"Python: {sys.version}")
    log(f"Port: {API_PORT}")
    log(f"Endpoints: GET /status, POST /link, POST /relink")
    log("=" * 50)
    init_link_status()
    log("Starting HTTP server...")
    ReusableHTTPServer(("0.0.0.0", API_PORT), APIHandler).serve_forever()
