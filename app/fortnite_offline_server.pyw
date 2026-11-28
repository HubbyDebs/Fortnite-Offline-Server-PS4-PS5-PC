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
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import match_server  # noqa: E402

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
PC_BUILD = os.path.join(ROOT, "pc-3.6")
CACHE = os.path.join(ROOT, "_local")
UI = os.path.join(BUNDLED, "ui", "index.html")
EDGE_PROFILE = os.path.join(HERE, ".window")
PS4_TITLE = "CUSA07022"
# Every title the plugin is enabled for: Fortnite's own and the Echo build (fake pkg "Echo (3.6)",
# UP1477-ECHO03600_00-FORTNITETESTING1), which installs under its own title ID.
PS4_TITLES = [PS4_TITLE, "ECHO03600"]
# .echofn.org: the Echo (3.6) build's own backend (api.echofn.org, seen in fnredir.log).
HOSTS = ["fortnite-master-server.ezfn.dev", ".epicgames.com", ".echofn.org"]
FTP_PORT = 2121
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
EDGE = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"]
GH = "/data/GoldHEN"
TRAITS = ["aimAccuracy", "aimTracking", "reactionSpeed", "firingFrequency", "aggression", "pursuit", "buildFrequency", "buildSpeed",
          "heightSeeking", "defensiveBuilding", "editFrequency", "editSpeed", "strafing", "jumping", "awareness", "prediction",
          "looting", "harvesting", "healing", "rotation"]

# Echo (3.6, ECHO03600) GoldHEN game_patch.prx stretched resolution & FOV presets (FMinimalViewInfo::CalculateProjectionMatrixGivenView).
ECHO_FOV_XML_PATH = "/data/GoldHEN/patches/xml/ECHO03600.xml"
ECHO_FOV_TITLE = "Echo 3.6"
ECHO_FOV_SIG_STRETCH = "80 f2 01 f6 c2 01 0f 85 a3 03 00 00 c5 fa 10 15"
ECHO_FOV_SIG_WIDE = "0f 85 ed 00 00 00 80 fb 01 0f 84 e4 00 00 00"
# The stretch presets patch both branches so the full 16:9 width stays visible and the extra height of an A:1 view is
# squeezed in (like a real stretched res): MaintainXFOV forces the aspect to A; MaintainYFOV (what Echo uses) keeps the
# X scale (h/w) and makes the Y scale h/w*A with a vmulss on a float A from the eboot's rodata
# (4/3 at 0x7c95ed8, 1.2 at 0x7eb2b00, rip-relative from 0x5c36f1f; no 1279/1080 constant exists, so that preset uses 1.2).
ECHO_FOV_SIG_STRETCH_Y = "0f 85 ed 00 00 00 80 fb 01 0f 84 e4 00 00 00 c5 e2 2a c9 c5 e2 2a d0 c5 f2 5e ca c5 fa 11 4d b8"
ECHO_FOV_PATCHES = [
    ("1440x1080", "Stretched 1440x1080 (4:3)", "4:3 stretched res at 1080p (64 deg vertical FOV, 1.33x wide)",
     [(ECHO_FOV_SIG_STRETCH, "c745b80000803fb8abaaaa3fc5f96ec8909090909090909090", None),
      (ECHO_FOV_SIG_STRETCH_Y, "c5f2590db9ef0502", "+32")]),
    ("1279x1080", "Stretched 1279x1080 (Pro)", "1279x1080 stretched res at 1080p (70 deg vertical FOV, 1.50x wide)",
     [(ECHO_FOV_SIG_STRETCH, "c745b80000803fb8cf95973fc5f96ec8909090909090909090", None),
      (ECHO_FOV_SIG_STRETCH_Y, "c5f2590de1bb2702", "+32")]),
    ("1080x1080", "Stretched 1080x1080 (1:1)", "1:1 ultra stretched res at 1080p (80 deg vertical FOV, 1.78x wide)",
     [(ECHO_FOV_SIG_STRETCH, "c745b80000803fb80000803fc5f96ec8909090909090909090", None),
      (ECHO_FOV_SIG_STRETCH_Y, "9090909090909090", "+32")]),
    ("wide112", "Wide FOV 112 (Unstretched)", "MaintainYFOV: 80 deg vertical / 112 deg horizontal FOV, unstretched",
     [(ECHO_FOV_SIG_WIDE, "90909090909080fb01909090909090", None)]),
]
ECHO_FOV_MODES = {p[0] for p in ECHO_FOV_PATCHES}


def _goldhen_patch_hash(title, name, app_ver, xml_path, app_elf):
    raw = f"{title}{name}{app_ver}{xml_path}{app_elf}".encode("ascii")[:255]
    h = 0x1505
    for b in raw:
        h = ((h * 33) ^ b) & 0xFFFFFFFFFFFFFFFF
    return h


def _build_echo_fov_xml():
    lines = ['<?xml version="1.0" encoding="utf-8"?>', "<Patch>", "    <TitleID>", "        <ID>ECHO03600</ID>", "    </TitleID>"]
    for _, name, note, patch_lines in ECHO_FOV_PATCHES:
        lines += [
            f'    <Metadata Title="{ECHO_FOV_TITLE}"',
            f'              Name="{name}"',
            f'              Note="{note}"',
            '              Author="Fortnite-Offline-Server"',
            '              PatchVer="1.0"',
            '              AppVer="mask"',
            '              AppElf="eboot.bin">',
            "        <PatchList>",
            *[f'            <Line Type="mask" Address="{addr}" Value="{val}"' + (f' Offset="{off}"' if off else '') + '/>'
              for addr, val, off in patch_lines],
            "        </PatchList>",
            "    </Metadata>",
        ]
    lines.append("</Patch>\n")
    return "\n".join(lines).encode("utf-8")


def apply_echo_fov_ps4(ip, enabled, mode, log):
    ftp = ftplib.FTP()
    ftp.connect(ip, FTP_PORT, timeout=6)
    ftp.login("anonymous", "")
    try:
        ftp.storbinary("STOR " + ECHO_FOV_XML_PATH, io.BytesIO(_build_echo_fov_xml()))
        active = mode if enabled and mode in ECHO_FOV_MODES else None
        for key, name, _, _ in ECHO_FOV_PATCHES:
            h = _goldhen_patch_hash(ECHO_FOV_TITLE, name, "mask", ECHO_FOV_XML_PATH, "eboot.bin")
            val = b"1\n" if key == active else b"0\n"
            ftp.storbinary(f"STOR /data/GoldHEN/patches/settings/0x{h:016x}.txt", io.BytesIO(val))
        # GoldHEN only applies patches/xml when game_patch.prx is loaded for the title in plugins.ini.
        if active:
            plugins = ftp_read(ftp, f"{GH}/plugins.ini") or ""
            m = re.search(r"^\[ECHO03600\]\s*\n((?:[^\[\n].*\n?)*)", plugins, re.M)
            if not m or "game_patch.prx" not in m.group(1):
                line = f"{GH}/plugins/game_patch.prx\n"
                if m:
                    body = m.group(1) if m.group(1).endswith("\n") else m.group(1) + "\n"
                    new = plugins[:m.start(1)] + body + line + plugins[m.end(1):]
                else:
                    new = plugins.rstrip("\n") + "\n[ECHO03600]\n" + line
                ftp_write(ftp, f"{GH}/plugins.ini", new)
                log("PS4: game_patch.prx enabled for Echo (3.6)", "ok")
        log(f"PS4 Echo 3.6 FOV: {active or 'OFF'} · close and reopen Echo (3.6) on PS4", "ok")
    finally:
        try:
            ftp.quit()
        except Exception:
            pass


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


def missing_titles(plugins):
    """The titles in PS4_TITLES whose plugins.ini section doesn't load fnredir.prx yet."""
    missing = []
    for title in PS4_TITLES:
        section = re.search(rf"^\[{re.escape(title)}\]\s*\n((?:[^\[\n].*\n?)*)", plugins, re.M)
        if not section or "fnredir.prx" not in section.group(1):
            missing.append(title)
    return missing


def game_installed(f):
    """Whether any of the Fortnite titles is installed on the PS4."""
    for title in PS4_TITLES:
        try:
            if f.nlst(f"/user/app/{title}"):
                return True
        except Exception:
            pass
    return False


def check_ps4(ip, pc_ip, port, log):
    """Brings the plugin and its settings up to date. Returns (state, text, game_installed)."""
    f = ftp_ps4(ip)
    try:
        plugins = ftp_read(f, f"{GH}/plugins.ini") or ""
        if "fnredir.prx" not in plugins or missing_titles(plugins):
            return "missing", "Not installed", game_installed(f)
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
        game = game_installed(f)
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
        missing = missing_titles(plugins)
        if missing:
            if plugins and ftp_read(f, f"{GH}/plugins.ini.before-fortnite") is None:
                ftp_write(f, f"{GH}/plugins.ini.before-fortnite", plugins)
            new = plugins.rstrip("\n") + ("\n" if plugins.strip() else "")
            for title in missing:
                new += f"[{title}]\n{GH}/plugins/fnredir.prx\n"
            ftp_write(f, f"{GH}/plugins.ini", new)
            log(f"Plugin enabled for Fortnite only ({', '.join(missing)})", "ok")
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
        self.match = None
        # The build the match server runs now: settings.json's pc_build until a console asks for another version.
        self.current_build = None

    def pc_build(self):
        """The PC build the match server runs: the one a console's version picked, else settings.json's pc_build, else
        the pc-3.6 folder next to the app."""
        return self.current_build or self.settings.get("pc_build") or PC_BUILD

    def known_builds(self):
        """{version: build folder}: settings.json's pc_builds ({"3.6": "E:\\\\...\\\\Fortnite"}), and every folder named like
        "3.6-CL-4019403" holding a Fortnite PC build next to pc_build's (E:\\\\7.40-CL-5046157 and E:\\\\3.6-CL-4019403)."""
        cached = getattr(self, "_builds_cache", None)
        if cached and time.time() - cached[0] < 10:
            return dict(cached[1])
        builds = {}
        base = self.settings.get("pc_build") or PC_BUILD
        parent = os.path.dirname(os.path.dirname(os.path.normpath(base)))
        try:
            for name in os.listdir(parent):
                m = re.fullmatch(r"(\d+\.\d+)-CL-\d+", name, re.I)
                folder = os.path.join(parent, name, "Fortnite")
                if m and match_server.check_build(folder) is None:
                    builds[m.group(1)] = folder
        except OSError:
            pass
        for version, folder in (self.settings.get("pc_builds") or {}).items():
            if isinstance(folder, str) and match_server.check_build(folder) is None:
                builds[str(version)] = folder
        # settings.json's hidden_versions: builds left on disk that the app doesn't offer or switch to (["7.40"]).
        for version in self.settings.get("hidden_versions") or []:
            builds.pop(str(version), None)
        self._builds_cache = (time.time(), dict(builds))
        return builds

    def server_version(self):
        """The version of the build the match server runs ("7.40"), from its folder's name, else None."""
        m = re.search(r"(\d+\.\d+)-CL-\d+", os.path.normpath(self.pc_build() or ""), re.I)
        return m.group(1) if m else None

    def set_version(self, version):
        """The app's Version buttons: "auto" follows the console's game; a version starts its server now and stays on it."""
        builds = self.known_builds()
        s = read_settings()
        if version == "auto":
            s["version_auto"] = True
            write_settings(s)
            self.settings = s
            self.log("Version: automatic (the server follows the game the PS4 opens)", "ok")
            return None
        build = builds.get(str(version))
        if not build:
            return f"There is no Fortnite {version} build on this PC"
        running_build = self.pc_build()
        s["version_auto"] = False
        s["pc_build"] = build
        write_settings(s)
        self.settings = s
        self._pinned_warned = None
        if os.path.normcase(os.path.normpath(build)) != os.path.normcase(os.path.normpath(running_build)):
            self.current_build = build
            if self.match:
                self.match.stop()
                self.match = None
            self.log(f"Version: Fortnite {version} (its match server starts now)", "ok")
            if self.process:
                self.start_match()
        else:
            self.current_build = build
            self.log(f"Version: Fortnite {version}", "ok")
        return None

    def follow_console_version(self, line):
        """A console's game version (its user-agent, "Fortnite/++Fortnite+Release-3.6-CL-4016789 PS4/"): when the
        match server runs another version's build and this PC has that version's, it is started again on it."""
        m = re.search(r"Fortnite/\+\+Fortnite\+Release-(\d+\.\d+)", line)
        if not m or self.settings.get("match_server", True) is False or os.name != "nt":
            return
        version = m.group(1)
        if self.settings.get("version_auto", True) is False:
            # Pinned from the app's version buttons: the console's version is only noted.
            if version != self.server_version() and version != getattr(self, "_pinned_warned", None):
                self._pinned_warned = version
                hint = (f"press {version} or Auto in Version to play it" if version in self.known_builds()
                        else f"open Fortnite {self.server_version()} on the PS4 to play")
                self.log(f"The PS4 is playing Fortnite {version}, but the server is set to {self.server_version()}: {hint}", "warn")
            return
        build = self.known_builds().get(version)
        if not build or os.path.normcase(os.path.normpath(build)) == os.path.normcase(os.path.normpath(self.pc_build())):
            return
        self.log(f"The PS4 is playing Fortnite {version}: starting the {version} match server", "ok")
        self.current_build = build
        if self.match:
            self.match.stop()
            self.match = None
        self.start_match()

    def start_match(self):
        if self.settings.get("match_server", True) is False or os.name != "nt":
            return
        if self.match and self.match.wanted:
            return
        self.match = match_server.MatchServer(self.pc_build(), self.port(), CACHE, self.log,
                                                self.settings.get("core_dir") or None)
        self.match.start()

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
        if self.match:
            self.match.stop()
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
                self.start_match()
            elif line.startswith("ERROR"):
                self.backend = ("error", line[6:].strip()[:70])
            if "PS4 POST /account/api/oauth/token -> 200" in line:
                self.signins += 1
            if line.startswith("PS4 user-agent: Fortnite/") or "PS4 user-agent: Fortnite/" in line:
                self.follow_console_version(line)
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
            # Building settings that only settings.json has (no control in the app yet): kept as they are.
            for key in ("materials", "harvest_multiplier"):
                if key in s.get("bots", {}):
                    bots[key] = s["bots"][key]
            s["bots"] = bots
        if "lan" in changes:
            s["lan"] = bool(changes["lan"])
        if "zero_build" in changes:
            s["zero_build"] = bool(changes["zero_build"])
        fov_changed = False
        if "ps4_stretched_fov" in changes:
            s["ps4_stretched_fov"] = bool(changes["ps4_stretched_fov"])
            fov_changed = True
        if "ps4_fov_mode" in changes:
            mode = str(changes["ps4_fov_mode"])
            if mode in ECHO_FOV_MODES:
                s["ps4_fov_mode"] = mode
                fov_changed = True
        if "unlock_all" in changes:
            names = [str(n).strip() for n in changes["unlock_all"]]
            s["unlock_all"] = [n for n in dict.fromkeys(names) if re.fullmatch(r"[A-Za-z0-9_]{3,16}", n)]
        write_settings(s)
        self.settings = s
        if fov_changed and self.ps4:
            ip = self.ps4[0]
            en = bool(s.get("ps4_stretched_fov", False))
            md = s.get("ps4_fov_mode") if s.get("ps4_fov_mode") in ECHO_FOV_MODES else "1440x1080"
            def _push_fov():
                try:
                    if port_open(ip, FTP_PORT):
                        apply_echo_fov_ps4(ip, en, md, self.log)
                    else:
                        self.log("PS4 FOV saved · turn on GoldHEN FTP to sync to PS4", "warn")
                except Exception as exc:
                    self.log(f"Could not send PS4 FOV patch: {exc}", "error")
            threading.Thread(target=_push_fov, daemon=True).start()

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
        problem = match_server.check_build(self.pc_build())
        if problem:
            match = ("error", "Fortnite PC build missing" if "not found" in problem else problem)
        elif self.match:
            match = self.match.state
        else:
            match = ("pending", "Starts with the server")
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
                         "zero_build": bool(s.get("zero_build", False)),
                         "port": s.get("port", 5595),
                         "ps4_stretched_fov": bool(s.get("ps4_stretched_fov", False)),
                         "ps4_fov_mode": s.get("ps4_fov_mode") if s.get("ps4_fov_mode") in ECHO_FOV_MODES else "1440x1080"},
            "versions": sorted(self.known_builds(), key=lambda v: [int(x) for x in v.split(".")]),
            "version": self.server_version(),
            "version_auto": s.get("version_auto", True) is not False,
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
        if route in ("/bg.jpg", "/bg.png", "/map-s7.jpg", "/map-s7.png"):
            img_path = os.path.join(BUNDLED, "ui", route.lstrip("/"))
            if not os.path.isfile(img_path):
                img_path = os.path.join(HERE, "ui", route.lstrip("/"))
            if os.path.isfile(img_path):
                with open(img_path, "rb") as f:
                    return self.send(200, f.read(), "image/jpeg" if route.endswith(".jpg") else "image/png")
        if route == "/api/state" and self.authorized():
            LAST_PING[0] = time.time()
            m = re.search(r"since=(\d+)", self.path)
            return self.send(200, STATE.snapshot(int(m.group(1)) if m else 0))
        if route == "/api/map" and self.authorized():
            LAST_PING[0] = time.time()
            if not STATE.process:
                return self.send(200, {"active": False, "stale": False, "telemetry": None, "events": []})
            try:
                req = urllib.request.Request(f"http://127.0.0.1:{STATE.port()}/admin/api/match/live")
                with urllib.request.urlopen(req, timeout=0.6) as resp:
                    return self.send(200, resp.read(), "application/json")
            except (urllib.error.URLError, TimeoutError, OSError):
                return self.send(200, {"active": False, "stale": False, "telemetry": None, "events": []})
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
            elif route == "/api/map/teleport":
                target = str(body.get("target", ""))[:64]
                req = urllib.request.Request(f"http://127.0.0.1:{STATE.port()}/admin/api/match/teleport", method="POST",
                                             data=json.dumps({"target": target}).encode("utf-8"),
                                             headers={"Content-Type": "application/json"})
                try:
                    with urllib.request.urlopen(req, timeout=2):
                        pass
                except urllib.error.HTTPError as e:
                    return self.send(e.code, {"error": "No hay partida en curso" if e.code == 409 else "No se pudo enviar"})
                except (urllib.error.URLError, OSError):
                    return self.send(503, {"error": "El servidor no está encendido"})
            elif route == "/api/settings":
                STATE.save(body)
            elif route == "/api/version":
                error = STATE.set_version(str(body.get("version", ""))[:16])
                if error:
                    return self.send(400, {"error": error})
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
