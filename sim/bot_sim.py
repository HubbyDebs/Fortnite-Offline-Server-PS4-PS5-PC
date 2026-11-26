"""Bot simulator: plays bots-only Battle Royale matches on this PC and reports how the bots played.

Each run starts a test backend of its own (port 5711, its own data folder: never the live backend on 5595), then one
match server at a time with CoreV2's testing switches (-CoreBotMatch: a match of bots alone; -CoreBotHotDrop: they all
land around one spot, so a short match has fights). At the match end CoreV2 logs one "match stats {json}" line a bot
(Fortnite-Offline-Server build); this script collects them with each bot's place and writes the run's report.

  python -I sim/bot_sim.py run --bots 25 --matches 3 --label baseline
  python -I sim/bot_sim.py run --bots 25 --materials 200 --harvest 2 --label mats
  python -I sim/bot_sim.py compare _local/sim/<run A> _local/sim/<run B>

Needs: server/settings.json's pc_build (the PC build of 3.6) and, for the match stats, a CoreV2 with LogMatchStats
(settings.json core_dir pointing at a local build, or a release that has it).
"""
import argparse
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
import match_server  # noqa: E402

SERVER = os.path.join(ROOT, "server")
CACHE = os.path.join(ROOT, "_local")
SIM_DIR = os.path.join(CACHE, "sim")
TEST_PORT = 5711
NO_WINDOW = 0x08000000
END_LINE = "Battle Royale bots: match stats done"
STATS_RE = re.compile(r"Battle Royale bots: match stats (\{.*\})\s*$")
OUT_RE = re.compile(r"Battle Royale bots: (.+?) is out, place (\d+), eliminated by (.+?) \(")
WIN_RE = re.compile(r"Battle Royale bots: (.+?) wins the match")
TIME_RE = re.compile(r"^\W*\[(\d\d):(\d\d):(\d\d)\.(\d+)\]")


def log(text):
    print(f"[{time.strftime('%H:%M:%S')}] {text}", flush=True)


def settings():
    with open(os.path.join(SERVER, "settings.json"), encoding="utf-8-sig") as f:
        return json.load(f)


def fortnite_running():
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq FortniteClient-Win64-Shipping.exe", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True, creationflags=NO_WINDOW).stdout
    return "FortniteClient" in out


# ---------------------------------------------------------------- test backend

class TestBackend:
    """start.mjs on the test port, PC only, with the run's own settings and data folder."""

    def __init__(self, run_dir, args, live):
        self.dir = run_dir
        self.settings_file = os.path.join(run_dir, "backend-settings.json")
        bots = {"count": args.bots, "level": args.level, "fight_chance": args.fights, "mixed": args.mixed,
                "materials": args.materials, "harvest_multiplier": args.harvest}
        if args.traits_from_settings and live.get("bots", {}).get("traits"):
            bots["traits"] = live["bots"]["traits"]
        self.port = args.port
        cfg = {"ps4_ip": "auto", "port": self.port, "psn_account": "SimPlayer", "bots": bots, "unlock_all": [],
               "lan": False, "warmup_seconds": args.warmup}
        with open(self.settings_file, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        self.proc = None
        self.lines = []

    def start(self):
        self.proc = subprocess.Popen(
            ["node", "start.mjs", "--pc-only", "--settings", self.settings_file, "--data", os.path.join(self.dir, "backend-data")],
            cwd=SERVER, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
        ready = threading.Event()

        def read():
            with open(os.path.join(self.dir, "backend.log"), "w", encoding="utf-8") as out:
                for raw in self.proc.stdout:
                    line = raw.decode("utf-8", "replace").rstrip()
                    out.write(line + "\n")
                    out.flush()
                    if line == "READY":
                        ready.set()
                    elif line.startswith("ERROR"):
                        log(f"backend: {line}")
            ready.set()

        threading.Thread(target=read, daemon=True).start()
        if not ready.wait(60) or self.proc.poll() is not None:
            raise RuntimeError(f"the test backend did not start (see {self.dir}\\backend.log)")
        log(f"Test backend ready on port {self.port}")

    def stop(self):
        if self.proc and self.proc.poll() is None:
            subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"], capture_output=True, creationflags=NO_WINDOW)


# ---------------------------------------------------------------- one match

def play_match(build, dlls, args, run_dir, number):
    exe = match_server.exe_path(build)
    extra = " -CoreBotMatch" + (" -CoreBotHotDrop" if args.hotdrop else "")
    handle, pid = match_server.start_injected(exe, match_server.arguments(args.port) + extra, dlls)
    log(f"Match {number}: server pid {pid}")
    folder = os.path.dirname(exe)
    log_path = os.path.join(folder, f"Server_log_{pid}.txt")
    start = time.time()
    pos, pending, ended, ready = 0, b"", False, False
    deadline = start + args.timeout * 60
    while time.time() < deadline:
        if match_server.k32.WaitForSingleObject(handle, 1000) != 0x102:
            log(f"Match {number}: the server exited by itself")
            break
        try:
            with open(log_path, "rb") as f:
                f.seek(pos)
                data = f.read()
        except OSError:
            continue
        pos += len(data)
        chunk = pending + data
        lines = chunk.split(b"\n")
        pending = lines[-1]
        for raw in lines[:-1]:
            line = raw.decode("utf-8", "replace")
            if not ready and "Server ready" in line:
                ready = True
                log(f"Match {number}: server ready after {time.time() - start:.0f} s")
            elif "-CoreBotMatch: starting a match of bots only" in line:
                log(f"Match {number}: bots-only match started")
            elif WIN_RE.search(line):
                log(f"Match {number}: {WIN_RE.search(line).group(1)} wins")
            elif END_LINE in line:
                ended = True
        if ended:
            time.sleep(2)
            break
    else:
        log(f"Match {number}: timed out after {args.timeout} min")
    match_server.k32.TerminateProcess(handle, 0)
    match_server.k32.WaitForSingleObject(handle, 10000)
    match_server.k32.CloseHandle(handle)
    kept = os.path.join(run_dir, f"match-{number}.server.log")
    for name, dest in ((f"Server_log_{pid}.txt", kept), (f"Redirect_log_{pid}.txt", None)):
        src = os.path.join(folder, name)
        if os.path.isfile(src):
            try:
                if dest:
                    shutil.move(src, dest)
                else:
                    os.remove(src)
            except OSError:
                pass
    result = parse_match(kept) if os.path.isfile(kept) else {"bots": [], "ended": False}
    result["ended"] = ended
    result["wallSeconds"] = round(time.time() - start)
    with open(os.path.join(run_dir, f"match-{number}.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    return result


def parse_match(path):
    bots, places, killers, winner = {}, {}, {}, None
    first = last = None
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        for line in f:
            t = TIME_RE.match(line)
            if t:
                secs = int(t.group(1)) * 3600 + int(t.group(2)) * 60 + int(t.group(3))
                if "lands at" in line and first is None:
                    first = secs
                last = secs
            m = STATS_RE.search(line)
            if m:
                try:
                    s = json.loads(m.group(1))
                    bots[s["bot"]] = s
                except ValueError:
                    pass
                continue
            m = OUT_RE.search(line)
            if m:
                places[m.group(1)] = int(m.group(2))
                killers[m.group(1)] = m.group(3).strip()
            m = WIN_RE.search(line)
            if m:
                winner = m.group(1)
    for name, s in bots.items():
        s["place"] = 1 if name == winner else places.get(name)
        s["eliminatedBy"] = killers.get(name)
    return {"bots": list(bots.values()), "winner": winner,
            "matchSeconds": (last - first) % 86400 if first is not None and last is not None else None}


# ---------------------------------------------------------------- report

def summarize(matches):
    bots = [b for m in matches for b in m["bots"]]
    if not bots:
        return {"bots": 0}
    landed = [b for b in bots if b.get("landedSeconds", 0) > 5]
    mean = lambda xs: round(statistics.mean(xs), 2) if xs else None
    pieces = {}
    steer = {}
    for b in bots:
        for k, v in (b.get("pieces") or {}).items():
            pieces[k] = pieces.get(k, 0) + v
        for k, v in (b.get("steerSeconds") or {}).items():
            steer[k] = steer.get(k, 0) + v
    ground = sum(b["landedSeconds"] for b in landed) or 1
    weapons = [b["firstWeaponSeconds"] for b in landed if b.get("firstWeaponSeconds", -1) >= 0]
    return {
        "matches": len(matches),
        "matchesEnded": sum(1 for m in matches if m.get("ended")),
        "bots": len(bots),
        "matchMinutes": mean([m["matchSeconds"] / 60 for m in matches if m.get("matchSeconds")]),
        "buildsStartedPerBot": mean([b["buildsStarted"] for b in bots]),
        "piecesPerBot": mean([b["piecesBuilt"] for b in bots]),
        "botsThatBuilt%": round(100 * sum(1 for b in bots if b["piecesBuilt"] > 0) / len(bots), 1),
        "buildsUnaffordablePerBot": mean([b["buildsUnaffordable"] for b in bots]),
        "piecesPerMinuteLanded": mean([60 * b["piecesBuilt"] / b["landedSeconds"] for b in landed]),
        "onPath%": mean([100 * b["pathSeconds"] / b["landedSeconds"] for b in landed if "pathSeconds" in b]),
        "offNavmesh%": mean([100 * sum(v for k, v in b["steerSeconds"].items() if "navmesh" in k or k == "no navigation") / b["landedSeconds"]
                             for b in landed if "steerSeconds" in b]),
        "still%": mean([100 * b["stillSeconds"] / b["landedSeconds"] for b in landed]),
        "pickaxe%": mean([100 * b["pickaxeSeconds"] / b["landedSeconds"] for b in landed]),
        "firstWeaponSeconds": mean(weapons),
        "botsWithoutWeapon%": round(100 * (len(landed) - len(weapons)) / len(landed), 1) if landed else None,
        # Deaths by cause (CoreV2's "death": fall / storm / other); older logs: unattributed eliminations as a whole.
        "stormDeaths%": round(100 * sum(1 for b in bots if b.get("death") == "storm") / max(1, sum(1 for b in bots if b.get("death"))), 1)
                        if any("death" in b for b in bots) else None,
        "fallDeaths%": round(100 * sum(1 for b in bots if b.get("death") == "fall") / max(1, sum(1 for b in bots if b.get("death"))), 1)
                       if any("death" in b for b in bots) else None,
        "unattributedDeaths%": round(100 * sum(1 for b in bots if b.get("eliminatedBy") == "nobody") / max(1, sum(1 for b in bots if b.get("eliminatedBy"))), 1),
        "ledgeTurnsPerBot": mean([b["ledgeTurns"] for b in bots if "ledgeTurns" in b]),
        "sidestepsPerBot": mean([b["sidesteps"] for b in bots if "sidesteps" in b]),
        "trappedPerBot": mean([b["trapped"] for b in bots if "trapped" in b]),
        "rescuesPerBot": mean([b["rescues"] for b in bots if "rescues" in b]),
        "stormSecondsPerBot": mean([b["stormSeconds"] for b in bots if "stormSeconds" in b]),
        "inStorm%": mean([100 * b["stormSeconds"] / b["landedSeconds"] for b in landed if "stormSeconds" in b]),
        "chestsPerBot": mean([b["searched"] for b in bots]),
        "shotsPerBot": mean([b["shots"] for b in bots]),
        "killsPerBot": mean([b["kills"] for b in bots]),
        "poisLeftPerBot": mean([b["poisLeft"] for b in bots if "poisLeft" in b]),
        "poisReachedPerBot": mean([b["poisReached"] for b in bots]),
        "huntsPerBot": mean([b["hunts"] for b in bots if "hunts" in b]),
        "gatheredPerBot": mean([b["gathered"] for b in bots if "gathered" in b]),
        "swingsPerBot": mean([b["swings"] for b in bots if "swings" in b]),
        "breakThroughsPerBot": mean([b.get("breakThroughs", 0) for b in bots]),
        "materialsAtEnd": mean([sum(b["materials"]) for b in bots]),
        "piecesByReason": dict(sorted(pieces.items(), key=lambda kv: -kv[1])),
        "steer%ByReason": {k: round(100 * v / ground, 1) for k, v in sorted(steer.items(), key=lambda kv: -kv[1])},
    }


def print_summary(title, s):
    log(f"=== {title}")
    for k, v in s.items():
        if not isinstance(v, dict):
            print(f"  {k:28} {v}")
    for k, v in (s.get("steer%ByReason") or {}).items():
        print(f"    steering {k:39} {v}% of ground time")
    for k, v in (s.get("piecesByReason") or {}).items():
        print(f"    built for {k:40} {v}")


HUMANS = os.path.join(ROOT, "ml", "models", "human_metrics_ch1.json")


def compare_human(run_dir):
    """The run's bots against Chapter 1 players (ml/models/human_metrics_ch1.json, from replays)."""
    b = json.load(open(os.path.join(run_dir, "summary.json"), encoding="utf-8"))
    h = json.load(open(HUMANS, encoding="utf-8"))
    rows = [
        ("still% (humans: idle < 0.6 m/s)", b.get("still%"), h["Fase_A_movement"]["pct_idle_lt_60uus"]),
        ("inStorm% of ground time", b.get("inStorm%"), h["Fase_B_storm_exposure"]["pct_time_in_storm"]),
        ("stormDeaths%", b.get("stormDeaths%"), h["Fase_B_storm_exposure"]["deaths_in_storm_pct"]),
        ("fallDeaths% (humans: fall kills)", b.get("fallDeaths%"), 2.18),
        ("killsPerBot (humans: median player)", b.get("killsPerBot"), h["Difficulty_Top10_vs_Median"]["median_player_lvl3"]["avg_kills"]),
        ("piecesPerBot (humans: median player)", b.get("piecesPerBot"), 4),
    ]
    print(f"{'metric':40} {'bots':>8} {'humans':>8}")
    for name, bv, hv in rows:
        print(f"{name:40} {str(bv):>8} {str(hv):>8}")


def compare(a_dir, b_dir):
    a = json.load(open(os.path.join(a_dir, "summary.json"), encoding="utf-8"))
    b = json.load(open(os.path.join(b_dir, "summary.json"), encoding="utf-8"))
    print(f"{'metric':28} {os.path.basename(a_dir)[:22]:>22} {os.path.basename(b_dir)[:22]:>22}  change")
    for k in a:
        if not isinstance(a.get(k), (int, float)) or not isinstance(b.get(k), (int, float)):
            continue
        d = b[k] - a[k]
        pct = f"{100 * d / a[k]:+.0f}%" if a[k] else ""
        print(f"{k:28} {a[k]:>22} {b[k]:>22}  {d:+.2f} {pct}")


# ---------------------------------------------------------------- main

def run(args):
    live = settings()
    if args.port == live.get("port", 5595) or args.port == 5595:
        sys.exit(f"Port {args.port} is the live backend's: pick a test port (5711, 5712...).")
    build = args.build or live.get("pc_build") or os.path.join(ROOT, "pc-3.6")
    problem = match_server.check_build(build)
    if problem:
        sys.exit(problem)
    if fortnite_running() and not args.alongside:
        sys.exit("A FortniteClient is running (the app's match server or the game): stop it first, or pass --alongside "
                 "(the sim's server then runs next to it, on its own backend port and the next free game port).")
    label = re.sub(r"[^A-Za-z0-9_-]", "_", args.label)
    run_dir = os.path.join(SIM_DIR, time.strftime("%Y%m%d-%H%M%S") + "-" + label)
    os.makedirs(run_dir)
    say = lambda text, kind="info": log(text)
    dlls = match_server.fetch_core(CACHE, say, args.core_dir or live.get("core_dir") or None)
    pak, sig = match_server.fetch_assets(CACHE, say, match_server.changelist_of(build))
    match_server.install_assets(build, pak, sig, say)
    match_server.patch_whitelist(match_server.exe_path(build), say)
    match_server.write_engine_ini()
    with open(os.path.join(run_dir, "run.json"), "w", encoding="utf-8") as f:
        json.dump({"args": vars(args), "build": build, "dlls": dlls}, f, indent=2)
    backend = TestBackend(run_dir, args, live)
    matches = []
    try:
        backend.start()
        for n in range(1, args.matches + 1):
            result = play_match(build, dlls, args, run_dir, n)
            matches.append(result)
            if not result["bots"]:
                log(f"Match {n}: no match stats lines (is this a CoreV2 with LogMatchStats?)")
            print_summary(f"match {n}", summarize([result]))
    except KeyboardInterrupt:
        log("Stopped")
    finally:
        backend.stop()
    summary = summarize(matches)
    with open(os.path.join(run_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print_summary(f"run {os.path.basename(run_dir)}", summary)
    log(f"Report: {run_dir}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="play bots-only matches and report")
    r.add_argument("--label", default="run")
    r.add_argument("--matches", type=int, default=1)
    r.add_argument("--bots", type=int, default=25)
    r.add_argument("--level", type=int, default=7)
    r.add_argument("--fights", type=int, default=50, help="bot vs bot fight chance %%")
    r.add_argument("--mixed", action="store_true", help="each bot its own skill tier and play style")
    r.add_argument("--materials", type=int, default=0, help="wood, stone and metal each bot leaves the bus with")
    r.add_argument("--harvest", type=int, default=1, help="harvest multiplier")
    r.add_argument("--warmup", type=int, default=30, help="warmup seconds")
    r.add_argument("--no-hotdrop", dest="hotdrop", action="store_false")
    r.add_argument("--traits-from-settings", action="store_true", help="use settings.json's bots.traits")
    r.add_argument("--timeout", type=float, default=30, help="minutes a match may last")
    r.add_argument("--build", help="PC build folder (default: settings.json pc_build)")
    r.add_argument("--port", type=int, default=TEST_PORT,
                   help="test backend port (default 5711; another one, e.g. 5712 with --alongside, runs a second simulation at once)")
    r.add_argument("--alongside", action="store_true", help="run next to another Fortnite server (e.g. the app's while playing)")
    r.add_argument("--core-dir", help="local CoreV2 x64\\Release folder (default: settings.json core_dir)")
    c = sub.add_parser("compare", help="compare two runs' summaries")
    c.add_argument("a")
    c.add_argument("b")
    hc = sub.add_parser("humans", help="compare a run's bots with Chapter 1 players")
    hc.add_argument("run")
    args = p.parse_args()
    if args.cmd == "run":
        run(args)
    elif args.cmd == "humans":
        compare_human(args.run)
    else:
        compare(args.a, args.b)


if __name__ == "__main__":
    main()
