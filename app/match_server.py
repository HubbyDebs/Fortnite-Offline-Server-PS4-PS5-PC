"""Match server: CoreV2 on the PC build of Fortnite 3.6 (Season 3, CL-4019403), started the way Core Launcher starts its servers.

- Downloads this project's CoreV2 build with its bots (Core.dll + CoreRedirect.dll, a "bots-N" release built from corev2/) and Core-ServerAssets' pak for the build's CL, both checked.
- Installs the pak in the build's Paks folder (pakchunkCoreServerAssets-<platform>_P.pak and its .sig).
- Lets -nullrhi through the exe's command line whitelist (ExePatcher in Core Launcher).
- Writes the servers' own Engine.ini (XMPP off) in %LOCALAPPDATA%\\FortniteGame\\Saved_CoreServer.
- Starts FortniteClient-Win64-Shipping.exe suspended, queues LoadLibraryW for each DLL as an APC, resumes it.

Usage from a terminal:  python app/match_server.py setup "E:\\3.6-CL-4019403\\Fortnite"   (download + install)
                        python app/match_server.py run   "E:\\3.6-CL-4019403\\Fortnite"   (one server, Ctrl+C stops)
"""
import ctypes
import hashlib
import json
import os
import re
import shutil
import sys
import threading
import time
import urllib.request
from ctypes import wintypes

CHANGELIST = 4019403  # the default: 3.6's (Season 3); a build folder named like "7.40-CL-5046157" gives its own (changelist_of)
# This repository's releases; the bots' DLLs are the "bots-N" ones (.github/workflows/corev2.yml builds them).
CORE_RELEASES = "https://api.github.com/repos/HubbyDebs/Fortnite-Offline-Server-PS4-PS5-PC/releases?per_page=50"
CORE_TAG_PREFIX = "bots-"
ASSETS_MANIFEST = "https://raw.githubusercontent.com/PongooDev/Core-ServerAssets/main/manifest.json"
DLLS = ["CoreRedirect.dll", "Core.dll"]  # load order: the redirect first, then the server
LOGIN = "CoreServer1"
PASSWORD = "Core"
EXE = os.path.join("FortniteGame", "Binaries", "Win64", "FortniteClient-Win64-Shipping.exe")
SAVED = os.path.join(os.environ.get("LOCALAPPDATA", ""), "FortniteGame", "Saved_CoreServer")
ENGINE_INI = "[XMPP]\r\nbEnabled=False\r\n"
CLIENT_SWITCHES = "-invitesession -invitefrom -party_joininfo_token -replay".encode("utf-16-le")
SERVER_SWITCHES = "-log -nosplash -nosound -nullrhi -useolditemcards       ".encode("utf-16-le")
UA = {"User-Agent": "fortnite-offline-server"}


def changelist_of(build):
    """The build's CL from its folder name (E:/3.6-CL-4019403/Fortnite), else 3.6's."""
    m = re.search(r"-CL-(\d+)", os.path.normpath(build or ""), re.I)
    return int(m.group(1)) if m else CHANGELIST


def exe_path(build):
    return os.path.join(build, EXE)


def check_build(build):
    """None when the folder holds a PC build of Fortnite, else what is wrong."""
    if not build:
        return "Fortnite PC 3.6 folder not set"
    if not os.path.isfile(exe_path(build)):
        return f"FortniteClient-Win64-Shipping.exe not found in {build}"
    if not os.path.isdir(paks_folder(build)):
        return "FortniteGame\\Content\\Paks is missing"
    return None


# ---------------------------------------------------------------- downloads

def _get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        return r.read()


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(url, dest, log, sha256=None, size=None):
    if os.path.isfile(dest) and (sha256 is None or _sha256(dest) == sha256):
        return dest
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    part = dest + ".part"
    name = os.path.basename(dest)
    log(f"Downloading {name}{f' ({size // 1048576} MB)' if size else ''}…", "info")
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r, open(part, "wb") as f:
        total = size or int(r.headers.get("Content-Length") or 0)
        got, shown = 0, 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if total and got * 10 // total > shown:
                shown = got * 10 // total
                log(f"{name}: {shown * 10}%", "tech")
    if sha256 and _sha256(part) != sha256:
        os.remove(part)
        raise RuntimeError(f"{name}: the download does not match its SHA-256")
    os.replace(part, dest)
    return dest


def local_core(cache, local_dir, log):
    """A CoreV2 built on this PC (settings.json core_dir, e.g. ...\\CoreV2\\x64\\Release): [paths in load order], copied
    first so the running server never locks the build's own output. None when the folder doesn't hold both DLLs."""
    sources = [os.path.join(local_dir, d) for d in DLLS]
    if not all(os.path.isfile(p) for p in sources):
        return None
    digest = hashlib.sha256()
    for p in sources:
        digest.update(_sha256(p).encode())
    dest = os.path.join(cache, "corev2", "local-" + digest.hexdigest()[:12])
    out = []
    for p in sources:
        target = os.path.join(dest, os.path.basename(p))
        if not os.path.isfile(target):
            os.makedirs(dest, exist_ok=True)
            shutil.copyfile(p, target)
        out.append(target)
    log(f"Using the local CoreV2 build in {local_dir} ({os.path.basename(dest)})", "info")
    return out


def fetch_core(cache, log, local_dir=None):
    """This project's CoreV2 build: the local one when local_dir has it, else the latest release. [paths in load order]"""
    if local_dir:
        found = local_core(cache, local_dir, log)
        if found:
            return found
        log(f"core_dir {local_dir} has no Core.dll and CoreRedirect.dll; using the latest release", "warn")
    folder = os.path.join(cache, "corev2")
    try:
        releases = [r for r in json.loads(_get(CORE_RELEASES))
                    if r["tag_name"].startswith(CORE_TAG_PREFIX) and not r.get("draft") and not r.get("prerelease")]
        if not releases:
            raise RuntimeError("no bots release published yet")
        release = max(releases, key=lambda r: r.get("published_at") or "")
        tag = release["tag_name"]
        assets = {a["name"]: a for a in release["assets"]}
        out = []
        for dll in DLLS:
            a = assets[dll]
            dest = os.path.join(folder, tag, dll)
            if not (os.path.isfile(dest) and os.path.getsize(dest) == a["size"]):
                _download(a["browser_download_url"], dest, log, size=a["size"])
            out.append(dest)
        with open(os.path.join(folder, "current.txt"), "w") as f:
            f.write(tag)
        return out
    except Exception as e:
        # Offline: use the last good download.
        try:
            with open(os.path.join(folder, "current.txt")) as f:
                tag = f.read().strip()
            out = [os.path.join(folder, tag, d) for d in DLLS]
            if all(os.path.isfile(p) for p in out):
                log(f"Could not check CoreV2 updates ({e}); using {tag}", "warn")
                return out
        except OSError:
            pass
        raise RuntimeError(f"Could not download CoreV2: {e}")


def fetch_assets(cache, log, changelist=CHANGELIST):
    """Core-ServerAssets' pak (and sig) for the build's CL, downloaded and checked: (pak, sig or None)."""
    folder = os.path.join(cache, "server-assets")
    manifest_file = os.path.join(folder, "manifest.json")
    try:
        manifest = json.loads(_get(ASSETS_MANIFEST))
        os.makedirs(folder, exist_ok=True)
        with open(manifest_file, "w", encoding="utf-8") as f:
            json.dump(manifest, f)
    except Exception:
        with open(manifest_file, encoding="utf-8") as f:
            manifest = json.load(f)
    entry = manifest["builds"].get(str(changelist))
    if not entry:
        raise RuntimeError(f"Core-ServerAssets has no server assets for CL-{changelist}")
    pak = _download(entry["url"], os.path.join(folder, entry["pak"]), log, entry["sha256"], entry["size"])
    sig = None
    if entry.get("sig"):
        s = entry["sig"]
        sig = _download(s["url"], os.path.join(folder, s["file"]), log, s["sha256"], s["size"])
    return pak, sig


# ---------------------------------------------------------------- the build

def paks_folder(build):
    return os.path.join(build, "FortniteGame", "Content", "Paks")


def _platform(paks):
    names = sorted((os.path.splitext(n)[0] for n in os.listdir(paks)
                    if re.fullmatch(r"pakchunk.*-.*\.pak", n, re.I) and not n.lower().startswith("pakchunkcore")), key=str.lower)
    if names:
        suffix = names[0].rsplit("-", 1)[1]
        suffix = suffix[:-2] if suffix.upper().endswith("_P") else suffix
        if suffix.isalnum():
            return suffix
    return "WindowsClient"


def install_assets(build, pak, sig, log):
    paks = paks_folder(build)
    dest = os.path.join(paks, f"pakchunkCoreServerAssets-{_platform(paks)}_P.pak")
    if not (os.path.isfile(dest) and os.path.getsize(dest) == os.path.getsize(pak)):
        shutil.copyfile(pak, dest + ".part")
        os.replace(dest + ".part", dest)
        log(f"Server assets installed: {os.path.basename(dest)}", "ok")
    signs = any(re.fullmatch(r"pakchunk0.*\.sig", n, re.I) for n in os.listdir(paks))
    if sig and signs:
        dsig = os.path.splitext(dest)[0] + ".sig"
        if not os.path.isfile(dsig):
            shutil.copyfile(sig, dsig)


def patch_whitelist(exe, log):
    with open(exe, "rb") as f:
        data = f.read()
    at = data.find(CLIENT_SWITCHES)
    if at < 0:
        return
    try:
        with open(exe, "r+b") as f:
            f.seek(at)
            f.write(SERVER_SWITCHES)
        log("Patched the exe's command line whitelist (-nullrhi, -nosplash, -nosound, -log)", "tech")
    except OSError as e:
        log(f"Could not patch the exe ({e}); Core does it in memory instead", "tech")


def write_engine_ini():
    path = os.path.join(SAVED, "Config", "WindowsClient", "Engine.ini")
    try:
        with open(path, encoding="utf-8") as f:
            if f.read() == ENGINE_INI:
                return
    except OSError:
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(ENGINE_INI)


def arguments(port):
    backend = f"127.0.0.1:{port}"
    return " ".join([
        "-epicapp=Fortnite", "-epicenv=Prod", "-epicportal", "-SkipPatchCheck", "-nullrhi", "-nosplash", "-nosound",
        "-McpRegion=NA", f"-httpproxy={backend}", f"-CoreBackend=http://{backend}",
        f"-AUTH_LOGIN={LOGIN}", f"-AUTH_PASSWORD={PASSWORD}", "-AUTH_TYPE=epic",
        "-unattended", "-saveddirsuffix=CoreServer", "-CoreMode=BattleRoyale",
    ])


# ---------------------------------------------------------------- Windows: start suspended, queue the DLLs, resume

if os.name == "nt":
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class STARTUPINFOW(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR), ("lpDesktop", wintypes.LPWSTR),
                    ("lpTitle", wintypes.LPWSTR), ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                    ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD), ("dwXCountChars", wintypes.DWORD),
                    ("dwYCountChars", wintypes.DWORD), ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                    ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD), ("lpReserved2", ctypes.c_void_p),
                    ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE)]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                    ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD),
                    ("IoInfo", ctypes.c_uint64 * 6), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k32.CreateProcessW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, wintypes.BOOL,
                                   wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(STARTUPINFOW),
                                   ctypes.POINTER(PROCESS_INFORMATION)]
    k32.VirtualAllocEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD, wintypes.DWORD]
    k32.VirtualAllocEx.restype = ctypes.c_void_p
    k32.WriteProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                       ctypes.POINTER(ctypes.c_size_t)]
    k32.QueueUserAPC.argtypes = [ctypes.c_void_p, wintypes.HANDLE, ctypes.c_void_p]
    k32.GetModuleHandleW.restype = wintypes.HMODULE
    k32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    k32.GetProcAddress.restype = ctypes.c_void_p
    k32.GetProcAddress.argtypes = [wintypes.HMODULE, ctypes.c_char_p]
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k32.ResumeThread.argtypes = [wintypes.HANDLE]
    k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]

    _JOB = None

    def _job():
        """Every server is in one job that dies with this app (JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)."""
        global _JOB
        if _JOB is None:
            job = k32.CreateJobObjectW(None, None)
            info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.LimitFlags = 0x2000
            k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
            _JOB = job
        return _JOB

    def _fail(what):
        raise ctypes.WinError(ctypes.get_last_error(), what)

    def start_injected(exe, args, dlls, minimized=True):
        """(process handle, pid)."""
        si = STARTUPINFOW()
        si.cb = ctypes.sizeof(si)
        if minimized:
            si.dwFlags = 0x1          # STARTF_USESHOWWINDOW
            si.wShowWindow = 7        # SW_SHOWMINNOACTIVE
        pi = PROCESS_INFORMATION()
        line = ctypes.create_unicode_buffer(f'"{exe}" {args}')
        if not k32.CreateProcessW(exe, line, None, None, False, 0x4, None, os.path.dirname(exe),
                                  ctypes.byref(si), ctypes.byref(pi)):
            _fail(f"CreateProcessW {exe}")
        try:
            k32.AssignProcessToJobObject(_job(), pi.hProcess)
            load = k32.GetProcAddress(k32.GetModuleHandleW("kernel32.dll"), b"LoadLibraryW")
            for dll in dlls:
                data = ctypes.create_unicode_buffer(os.path.abspath(dll))
                size = ctypes.sizeof(data)
                remote = k32.VirtualAllocEx(pi.hProcess, None, size, 0x3000, 0x04)
                if not remote:
                    _fail("VirtualAllocEx")
                if not k32.WriteProcessMemory(pi.hProcess, remote, data, size, None):
                    _fail("WriteProcessMemory")
                if not k32.QueueUserAPC(load, pi.hThread, remote):
                    _fail(f"QueueUserAPC {dll}")
            if k32.ResumeThread(pi.hThread) == 0xFFFFFFFF:
                _fail("ResumeThread")
        except Exception:
            k32.TerminateProcess(pi.hProcess, 1)
            k32.CloseHandle(pi.hProcess)
            raise
        finally:
            k32.CloseHandle(pi.hThread)
        return pi.hProcess, pi.dwProcessId


# ---------------------------------------------------------------- one server, restarted after each match

class MatchServer:
    """Keeps one Battle Royale server running for the backend on this PC; restarts it when its match ends."""

    def __init__(self, build, port, cache, log, core_dir=None):
        self.build, self.port, self.cache, self.log = build, port, cache, log
        self.core_dir = core_dir
        self.state = ("pending", "Not started")
        self.handle = None
        self.pid = None
        self.wanted = False
        self.thread = None

    def start(self):
        if self.wanted:
            return
        self.wanted = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        self.wanted = False
        h = self.handle
        if h:
            k32.TerminateProcess(h, 1)
        self.state = ("stopped", "Stopped")

    def _prepare(self):
        problem = check_build(self.build)
        if problem:
            raise RuntimeError(problem)
        self.state = ("starting", "Downloading CoreV2…")
        dlls = fetch_core(self.cache, self.log, self.core_dir)
        self.state = ("starting", "Downloading server assets (615 MB)…")
        pak, sig = fetch_assets(self.cache, self.log, changelist_of(self.build))
        install_assets(self.build, pak, sig, self.log)
        patch_whitelist(exe_path(self.build), self.log)
        write_engine_ini()
        return dlls

    def _run(self):
        try:
            dlls = self._prepare()
        except Exception as e:
            self.state = ("error", str(e)[:80])
            self.log(f"Match server: {e}", "error")
            self.wanted = False
            return
        failures = 0
        while self.wanted:
            started = time.time()
            try:
                self.handle, self.pid = start_injected(exe_path(self.build), arguments(self.port), dlls)
            except OSError as e:
                self.state = ("error", f"Could not start: {e}"[:80])
                self.log(f"Match server could not start: {e}", "error")
                self.wanted = False
                return
            self.state = ("starting", "Loading the map…")
            self.log(f"Match server starting (pid {self.pid})", "info")
            self._follow()
            code = wintypes.DWORD()
            k32.GetExitCodeProcess(self.handle, ctypes.byref(code))
            k32.CloseHandle(self.handle)
            self.handle = None
            if not self.wanted:
                break
            failures = failures + 1 if time.time() - started < 90 else 0
            if failures >= 3:
                self.state = ("error", f"Crashes at start (0x{code.value:08X})")
                self.log("The match server closed three times while starting. Turn on Details to see its log.", "error")
                self.wanted = False
                break
            self.log(f"Match server ended (0x{code.value:08X}) · starting a new one", "info")
            time.sleep(3)

    def _follow(self):
        """Shows Core's log (Server_log_<pid>.txt next to the exe) until the process ends."""
        folder = os.path.dirname(exe_path(self.build))
        path = os.path.join(folder, f"Server_log_{self.pid}.txt")
        pos, pending = 0, b""
        while k32.WaitForSingleObject(self.handle, 300) == 0x102:  # WAIT_TIMEOUT
            pos, pending = self._tail(path, pos, pending)
        self._tail(path, pos, pending)
        for name in (f"Server_log_{self.pid}.txt", f"Redirect_log_{self.pid}.txt"):
            src = os.path.join(folder, name)
            if os.path.isfile(src):
                try:
                    os.makedirs(os.path.join(self.cache, "logs"), exist_ok=True)
                    os.replace(src, os.path.join(self.cache, "logs", name))
                except OSError:
                    pass

    def _tail(self, path, pos, pending):
        try:
            with open(path, "rb") as f:
                f.seek(pos)
                data = f.read()
        except OSError:
            return pos, pending
        pos += len(data)
        lines = (pending + data).split(b"\n")
        for raw in lines[:-1]:
            line = raw.decode("utf-8", "replace").rstrip()
            if not line:
                continue
            if "Server ready" in line:
                self.state = ("ok", "Ready · waiting for players")
                self.log("Match server ready · start a match on the PS4", "ok")
            elif re.search(r"\b(error|fatal|crash)", line, re.I):
                self.log(f"[match] {line}", "warn")
                continue
            self.log(f"[match] {line}", "tech")
        return pos, lines[-1]


def _cli():
    if len(sys.argv) < 3 or sys.argv[1] not in ("setup", "run"):
        sys.exit(__doc__)
    build = sys.argv[2]
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cache = os.path.join(root, "_local")
    log = lambda text, kind="info": print(f"[{kind}] {text}", flush=True)
    if sys.argv[1] == "setup":
        dlls = fetch_core(cache, log)
        pak, sig = fetch_assets(cache, log, changelist_of(build))
        problem = check_build(build)
        if problem:
            log(f"Downloads ready; not installed yet: {problem}", "warn")
            return
        install_assets(build, pak, sig, log)
        patch_whitelist(exe_path(build), log)
        write_engine_ini()
        log(f"Ready: {', '.join(os.path.basename(d) for d in dlls)} + server assets", "ok")
        return
    with open(os.path.join(root, "server", "settings.json"), encoding="utf-8-sig") as f:
        port = json.load(f).get("port", 5595)
    m = MatchServer(build, port, cache, log)
    m.start()
    try:
        while m.wanted:
            time.sleep(1)
    except KeyboardInterrupt:
        m.stop()


if __name__ == "__main__":
    _cli()
