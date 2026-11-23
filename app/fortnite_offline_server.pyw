"""Fortnite Offline Server: desktop app.

The logic lives here (Python standard library only) and the interface in ui/index.html, shown in a Microsoft Edge
window in app mode. The local API only listens on 127.0.0.1 and needs a random token from this run. Closing the window
stops the server and the app.
"""
import ftplib
import hashlib
import io
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

if getattr(sys, "frozen", False):
    # PyInstaller .exe in the project folder; the interface is bundled inside the .exe.
    ROOT = os.path.dirname(sys.executable)
    HERE = os.path.join(ROOT, "app")
    BUNDLED = getattr(sys, "_MEIPASS", HERE)
else:
    HERE = os.path.dirname(os.path.abspath(__file__))
    ROOT = os.path.dirname(HERE)
    BUNDLED = HERE
SERVER = os.path.join(ROOT, "server")
SETTINGS = os.path.join(SERVER, "settings.json")
SETTINGS_EXAMPLE = os.path.join(SERVER, "settings.example.json")
PLUGIN = os.path.join(ROOT, "plugin", "fnredir.prx")
PC_BUILD = os.path.join(ROOT, "pc-7.40")
UI = os.path.join(BUNDLED, "ui", "index.html")
EDGE_PROFILE = os.path.join(HERE, ".window")
PS4_TITLE = "CUSA07022"
HOSTS = ["fortnite-master-server.ezfn.dev", ".epicgames.com"]
FTP_PORT = 2121
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
EDGE = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"]
GH = "/data/GoldHEN"
TRAITS = ["aimAccuracy", "aimTracking", "reactionSpeed", "firingFrequency", "aggression", "pursuit", "buildFrequency", "buildSpeed",
          "heightSeeking", "defensiveBuilding", "editFrequency", "editSpeed", "strafing", "jumping", "awareness", "prediction",
          "looting", "harvesting", "healing", "rotation"]


# ---------------------------------------------------------------- settings

def read_settings():
    if not os.path.isfile(SETTINGS):
        with open(SETTINGS_EXAMPLE, encoding="utf-8") as f, open(SETTINGS, "w", encoding="utf-8") as g:
            g.write(f.read())
    with open(SETTINGS, encoding="utf-8-sig") as f:
        return json.load(f)


def write_settings(s):
    tmp = SETTINGS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, SETTINGS)


# ---------------------------------------------------------------- network

def broadcasts():
    out = {"255.255.255.255"}
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if not ip.startswith("127."):
                out.add(ip.rsplit(".", 1)[0] + ".255")
    except OSError:
        pass
    return list(out)


def find_ps4(wait=1.5):
    """PlayStation discovery protocol (UDP 987): [(ip, name, awake)]."""
    msg = b"SRCH * HTTP/1.1\ndevice-discovery-protocol-version:00020020\n"
    found = {}
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.settimeout(0.3)
        s.bind(("", 0))
        for _ in range(2):
            for dest in broadcasts():
                try:
                    s.sendto(msg, (dest, 987))
                except OSError:
                    pass
            end = time.time() + wait / 2
            while time.time() < end:
                try:
                    data, (ip, _) = s.recvfrom(2048)
                except (socket.timeout, OSError):
                    continue
                lines = data.decode("utf-8", "replace").splitlines()
                fields = dict(l.split(":", 1) for l in lines[1:] if ":" in l)
                if fields.get("host-type", "").strip().upper() in ("PS4", "PS5"):
                    found[ip] = (ip, fields.get("host-name", "PS4").strip(), bool(lines) and " 200 " in lines[0])
    finally:
        s.close()
    return list(found.values())


def pc_ip_towards(ip):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((ip, 9))
        return s.getsockname()[0]
    finally:
        s.close()


def port_open(ip, port, wait=1.0):
    try:
        with socket.create_connection((ip, port), timeout=wait):
            return True
    except OSError:
        return False


# ---------------------------------------------------------------- PS4 over FTP (GoldHEN)

def ftp_ps4(ip):
    f = ftplib.FTP()
    f.connect(ip, FTP_PORT, timeout=8)
    f.login()
    return f


def ftp_read(f, path, binary=False):
    buf = io.BytesIO()
    try:
        f.retrbinary(f"RETR {path}", buf.write)
    except ftplib.error_perm:
        return None
    return buf.getvalue() if binary else buf.getvalue().decode("utf-8", "replace")


def ftp_write(f, path, data):
    f.storbinary(f"STOR {path}", io.BytesIO(data if isinstance(data, bytes) else data.encode("utf-8")))


def plugin_ini(pc_ip, port):
    return "".join([f"target=http://{pc_ip}:{port}\n"] + [f"host={h}\n" for h in HOSTS])


def local_plugin():
    with open(PLUGIN, "rb") as p:
        return p.read()


def check_ps4(ip, pc_ip, port, log):
    """Brings the plugin and its settings up to date. Returns (state, text, game_installed)."""
    f = ftp_ps4(ip)
    try:
        plugins = ftp_read(f, f"{GH}/plugins.ini") or ""
        if not (f"[{PS4_TITLE}]" in plugins and "fnredir.prx" in plugins):
            return "missing", "Not installed", bool(f.nlst(f"/user/app/{PS4_TITLE}"))
        remote = ftp_read(f, f"{GH}/plugins/fnredir.prx", binary=True)
        if os.path.isfile(PLUGIN):
            mine = local_plugin()
            if remote is None or hashlib.sha256(remote).digest() != hashlib.sha256(mine).digest():
                ftp_write(f, f"{GH}/plugins/fnredir.prx", mine)
                ftp_write(f, f"{GH}/fnredir.ini", plugin_ini(pc_ip, port))
                log("Plugin on the PS4 updated · restart Fortnite to load it", "ok")
        ini = ftp_read(f, f"{GH}/fnredir.ini") or ""
        if ini.strip() != plugin_ini(pc_ip, port).strip():
            ftp_write(f, f"{GH}/fnredir.ini", plugin_ini(pc_ip, port))
            log(f"This PC's IP changed · plugin now points to {pc_ip}", "ok")
        game = bool(f.nlst(f"/user/app/{PS4_TITLE}"))
    finally:
        f.quit()
    return "ok", f"Ready · {pc_ip}:{port}", game


def prepare_ps4(ip, pc_ip, port, log):
    f = ftp_ps4(ip)
    try:
        ftp_write(f, f"{GH}/plugins/fnredir.prx", local_plugin())
        log("Plugin uploaded to the PS4", "ok")
        ftp_write(f, f"{GH}/fnredir.ini", plugin_ini(pc_ip, port))
        log(f"Plugin points to this PC ({pc_ip}:{port})", "ok")
        plugins = ftp_read(f, f"{GH}/plugins.ini") or ""
        if not (f"[{PS4_TITLE}]" in plugins and "fnredir.prx" in plugins):
            if plugins and ftp_read(f, f"{GH}/plugins.ini.before-fortnite") is None:
                ftp_write(f, f"{GH}/plugins.ini.before-fortnite", plugins)
            new = plugins.rstrip("\n") + ("\n" if plugins.strip() else "")
            new += f"[{PS4_TITLE}]\n{GH}/plugins/fnredir.prx\n"
            ftp_write(f, f"{GH}/plugins.ini", new)
            log("Plugin enabled for Fortnite only", "ok")
    finally:
        f.quit()


# ---------------------------------------------------------------- readable log

EVENTS = [
    (r"PS4 POST /account/api/oauth/token -> 200", "The PS4 signed in", "ok"),
    (r"PS4 POST /account/api/oauth/token -> 4\d\d", "Sign-in refused · use name@core.com", "warn"),
    (r"XMPP connected", "Chat and party connected", "ok"),
    (r"PS4 \w+ /fortnite/api/(game/v2/)?matchmaking", "Looking for a match…", "info"),
    (r"PS4 found|PC player connected|everything unlocked", None, "ok"),
    (r"REFUSED|Could not|ERROR|could not", None, "error"),
]


def classify(line):
    """(text, kind); kind 'tech' is only shown with Details on."""
    body = re.sub(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d ", "", line)
    for pattern, text, kind in EVENTS:
        if re.search(pattern, body):
            return text or body, kind
    if body == "READY":
        return "Server ready", "ok"
    if re.match(r"PS4 (GET|POST|PUT|DELETE|PATCH) |PS4 user-agent|\[|Address for the consoles|Admin panel:|Backend on port|Only the PS4", body):
        return body, "tech"
    return body, "info"


# ---------------------------------------------------------------- app state

class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.process = None
        self.started = None
        self.backend = ("stopped", "Stopped")
        self.ps4 = None
        self.plugin = ("pending", "Not checked")
        self.game = None
        self.logs = []
        self.n = 0
        self.last_check = 0
        self.signins = 0
        self.settings = read_settings()

    def log(self, text, kind="info"):
        with self.lock:
            self.n += 1
            self.logs.append({"id": self.n, "t": time.strftime("%H:%M:%S"), "text": text, "kind": kind})
            del self.logs[:-800]

    def port(self):
        return self.settings.get("port", 5595)

    # -- the backend (Node)
    def start(self, changes=None):
        if self.process:
            return
        if changes:
            self.save(changes)
        try:
            p = subprocess.Popen(["node", "start.mjs"], cwd=SERVER, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
        except FileNotFoundError:
            self.log("Node.js was not found: install it and try again", "error")
            return
        self.process = p
        self.started = time.time()
        self.backend = ("starting", "Starting…")
        b = self.settings["bots"]
        skill = "custom traits" if b.get("traits") else f"level {b['level']}"
        self.log(f"Starting · {b['count']} bots, {skill}{', mixed personalities' if b.get('mixed') else ''}", "info")
        self.last_check = 0
        threading.Thread(target=self.read, args=(p,), daemon=True).start()

    def stop(self):
        p, self.process = self.process, None
        if not p:
            return
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True, creationflags=NO_WINDOW)
        else:
            p.terminate()
        self.started = None
        self.backend = ("stopped", "Stopped")
        self.log("Server stopped", "info")

    def read(self, p):
        for raw in p.stdout:
            line = raw.decode("utf-8", "replace").rstrip()
            if not line:
                continue
            if line == "READY":
                self.backend = ("ok", f"Running · port {self.port()}")
            elif line.startswith("ERROR"):
                self.backend = ("error", line[6:].strip()[:70])
            if "PS4 POST /account/api/oauth/token -> 200" in line:
                self.signins += 1
            text, kind = classify(line)
            self.log(text, kind)
        code = p.wait()
        if self.process is p:
            self.process = None
            self.started = None
            self.backend = ("error", f"Stopped by itself (code {code})")
            self.log("The server stopped by itself. Turn on Details and start it again to see why.", "error")

    def save(self, changes):
        s = read_settings()
        s["ps4_ip"] = "auto"
        if "bots" in changes:
            b = changes["bots"]
            clamp = lambda v, lo, hi: max(lo, min(hi, int(v)))
            bots = {"count": clamp(b["count"], 0, 99), "level": clamp(b["level"], 1, 10),
                    "fight_chance": clamp(b["fight_chance"], 0, 100), "mixed": bool(b.get("mixed", False))}
            traits = b.get("traits")
            if isinstance(traits, dict):
                bots["traits"] = {k: clamp(traits[k], 0, 100) for k in TRAITS if k in traits}
            s["bots"] = bots
        if "lan" in changes:
            s["lan"] = bool(changes["lan"])
        if "unlock_all" in changes:
            names = [str(n).strip() for n in changes["unlock_all"]]
            s["unlock_all"] = [n for n in dict.fromkeys(names) if re.fullmatch(r"[A-Za-z0-9_]{3,16}", n)]
        write_settings(s)
        self.settings = s

    # -- PS4
    def prepare(self):
        if not self.ps4:
            return "No PS4 on the network yet"
        if not os.path.isfile(PLUGIN):
            return "The plugin is not built (plugin/fnredir.prx)"
        ip = self.ps4[0]
        if not port_open(ip, FTP_PORT):
            return "Turn on GoldHEN and its FTP server on the PS4"

        def task():
            try:
                prepare_ps4(ip, pc_ip_towards(ip), self.port(), self.log)
                self.log("PS4 ready · close and open Fortnite", "ok")
            except Exception as e:
                self.log(f"Could not prepare the PS4: {e}", "error")
            self.last_check = 0

        threading.Thread(target=task, daemon=True).start()
        return None

    def watch(self):
        while True:
            try:
                seen = find_ps4()
            except OSError:
                seen = []
            before = self.ps4
            self.ps4 = seen[0] if seen else None
            if self.ps4 and (not before or before[0] != self.ps4[0]):
                self.log(f"PS4 found: {self.ps4[1]} at {self.ps4[0]}", "ok")
                self.last_check = 0
            if self.ps4 and time.time() - self.last_check > 30:
                self.last_check = time.time()
                ip = self.ps4[0]
                if port_open(ip, FTP_PORT):
                    try:
                        state, text, game = check_ps4(ip, pc_ip_towards(ip), self.port(), self.log)
                        self.plugin = (state, text)
                        self.game = game
                    except Exception as e:
                        self.plugin = ("error", f"FTP error ({e.__class__.__name__})")
                else:
                    self.plugin = ("warn", "GoldHEN FTP is off")
                    self.game = None
            time.sleep(6)

    def snapshot(self, since):
        s = self.settings
        with self.lock:
            logs = [l for l in self.logs if l["id"] > since]
        pc_ip = None
        try:
            pc_ip = pc_ip_towards(self.ps4[0] if self.ps4 else "192.168.1.1")
        except OSError:
            pass
        match = ("warn", "CoreV2 not set up yet") if os.path.isdir(PC_BUILD) else ("error", "Fortnite PC 7.40 missing")
        return {
            "running": self.process is not None,
            "uptime": int(time.time() - self.started) if self.started else 0,
            "backend": self.backend,
            "ps4": {"ip": self.ps4[0], "name": self.ps4[1], "awake": self.ps4[2]} if self.ps4 else None,
            "plugin": self.plugin,
            "game": self.game,
            "match": match,
            "signins": self.signins,
            "pc_ip": pc_ip,
            "settings": {"bots": s.get("bots", {}), "unlock_all": s.get("unlock_all", []), "lan": s.get("lan", False),
                         "port": s.get("port", 5595)},
            "logs": logs,
        }


# ---------------------------------------------------------------- local API

TOKEN = secrets.token_urlsafe(24)
STATE = None
# The page polls the state every 0.7 s: that is the sign the window is still open. Edge may relaunch itself in another
# process when the window opens, so waiting for the launched process to end does not work.
LAST_PING = [0.0]


class Api(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, kind="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def authorized(self):
        return secrets.compare_digest(self.headers.get("X-Token", ""), TOKEN)

    def do_GET(self):
        route = self.path.split("?")[0]
        if route == "/":
            with open(UI, "rb") as f:
                return self.send(200, f.read(), "text/html; charset=utf-8")
        if route == "/icon.png":
            with open(os.path.join(BUNDLED, "icon.png"), "rb") as f:
                return self.send(200, f.read(), "image/png")
        if route == "/api/state" and self.authorized():
            LAST_PING[0] = time.time()
            m = re.search(r"since=(\d+)", self.path)
            return self.send(200, STATE.snapshot(int(m.group(1)) if m else 0))
        self.send(404, {"error": "not found"})

    def do_POST(self):
        if not self.authorized():
            return self.send(403, {"error": "token"})
        length = min(int(self.headers.get("Content-Length") or 0), 65536)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            body = {}
        route = self.path.split("?")[0]
        try:
            if route == "/api/start":
                STATE.start(body)
            elif route == "/api/stop":
                STATE.stop()
            elif route == "/api/restart":
                STATE.stop()
                time.sleep(0.6)
                STATE.start(body)
            elif route == "/api/settings":
                STATE.save(body)
            elif route == "/api/prepare":
                error = STATE.prepare()
                if error:
                    return self.send(400, {"error": error})
            elif route == "/api/open":
                if body.get("what") == "panel":
                    os.startfile(f"http://127.0.0.1:{STATE.port()}/admin")
                elif body.get("what") == "folder":
                    os.startfile(SERVER)
            else:
                return self.send(404, {"error": "not found"})
        except Exception as e:
            return self.send(500, {"error": str(e)})
        self.send(200, {"ok": True})


def open_window(url):
    edge = next((e for e in EDGE if os.path.isfile(e)), None)
    if not edge:
        os.startfile(url)
        return
    subprocess.Popen([edge, f"--app={url}", f"--user-data-dir={EDGE_PROFILE}", "--window-size=1180,820",
                      "--no-first-run", "--no-default-browser-check", "--disable-features=Translate",
                      "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
                      "--disable-backgrounding-occluded-windows"],
                     creationflags=NO_WINDOW)


def main():
    global STATE
    STATE = State()
    http = ThreadingHTTPServer(("127.0.0.1", 0), Api)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    threading.Thread(target=STATE.watch, daemon=True).start()
    STATE.log("Ready. Press the power button to start the server.", "info")
    open_window(f"http://127.0.0.1:{http.server_address[1]}/#{TOKEN}")
    begin = time.time()
    try:
        while True:
            time.sleep(1)
            last = LAST_PING[0]
            if not last and time.time() - begin > 90:
                break  # the window never opened
            if last and time.time() - last > 20:
                break  # window closed
    finally:
        STATE.stop()
        http.shutdown()


if __name__ == "__main__":
    if not os.path.isfile(SETTINGS) and not os.path.isfile(SETTINGS_EXAMPLE):
        sys.exit(f"{SETTINGS_EXAMPLE} not found")
    main()
