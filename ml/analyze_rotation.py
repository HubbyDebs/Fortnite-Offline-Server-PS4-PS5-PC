"""Landing and rotation: Chapter 1 players (replays_ch1_jsonl) against the bots (a sim run's backend telemetry).

Same frame format on both sides, so the same measures, without POI coordinates:
- landing: where each player first stands on the ground, how many others land within 100 m (a hot drop);
- the landing area: seconds until the player is 150 m from where it landed (it leaves its spot);
- areas: the places it stays at least 30 s (a new one when it is 150 m from the last one's anchor), in its first
  8 minutes on the ground (the bots' matches end at ~8.5 min);
- distance moved a minute on the ground.

  python -I ml/analyze_rotation.py [--sim _local/sim/<run>] [--out ml/models/rotation_metrics.json]
"""
import argparse
import glob
import json
import math
import os
import statistics

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WINDOW = 480.0      # seconds on the ground looked at
LEAVE = 15000.0     # 150 m: out of the landing area / a new area
DWELL = 30.0        # seconds to count as a place it stayed
HOT = 10000.0       # 100 m: landed together


def stats(values):
    values = sorted(v for v in values if v is not None)
    if not values:
        return {"n": 0}
    q = lambda p: values[min(len(values) - 1, int(p * len(values)))]
    return {"n": len(values), "mean": round(statistics.mean(values), 2), "p25": round(q(0.25), 2), "median": round(q(0.5), 2),
            "p75": round(q(0.75), 2), "p90": round(q(0.9), 2)}


def tracks(path):
    """Per player: [(t, x, y, z)] while "Landed" and alive, a sample a second at most. A telemetry file with several
    matches (the sim's backend writes one file a run) is split where the phase goes back to the warmup."""
    out = {}
    t0 = None
    match = 0
    was_warmup = True
    for line in open(path, encoding="utf-8"):
        try:
            frame = json.loads(line)
        except ValueError:
            continue
        ts = frame.get("ts")
        if ts is None:
            continue
        warmup = frame.get("phase") == "Warmup"
        if warmup and not was_warmup:
            match += 1
        was_warmup = warmup
        t0 = ts if t0 is None else t0
        t = (ts - t0) / 1000.0
        for a in frame.get("actors") or []:
            if a.get("state") != "Landed" or not a.get("alive", True):
                continue
            key = (match, a.get("id") or a.get("name"))
            track = out.setdefault(key, {"name": a.get("name"), "bot": a.get("isBot"), "pts": []})
            if track["pts"] and t - track["pts"][-1][0] < 0.99:
                continue
            track["pts"].append((t, a["x"], a["y"], a.get("z", 0)))
    return out


def player_metrics(pts):
    # Samples can stop while the player is out of the recorder's relevancy (replays): gaps over 10 s end the track.
    clean = [pts[0]]
    for p in pts[1:]:
        if p[0] - clean[-1][0] > 10.0:
            break
        clean.append(p)
    # "Landed" starts when the glider opens in the replays: the landing is the first of 3 seconds in a row at a
    # walking pace (under 8 m/s across, under 3 m up or down).
    land = None
    for i in range(len(clean) - 3):
        if all(math.hypot(clean[j + 1][1] - clean[j][1], clean[j + 1][2] - clean[j][2]) < 800.0 and abs(clean[j + 1][3] - clean[j][3]) < 300.0
               for j in range(i, i + 3)):
            land = i
            break
    if land is None:
        return None
    pts = clean[land:]
    t_land = pts[0][0]
    pts = [p for p in pts if p[0] - t_land <= WINDOW]
    ground = pts[-1][0] - t_land
    if ground < 120.0:
        return None
    lx, ly = pts[0][1], pts[0][2]
    leave = next((p[0] - t_land for p in pts if math.hypot(p[1] - lx, p[2] - ly) > LEAVE), None)
    areas = 0
    ax, ay, at = lx, ly, t_land
    counted = False
    dist = 0.0
    for prev, p in zip(pts, pts[1:]):
        step = math.hypot(p[1] - prev[1], p[2] - prev[2])
        if step < 2000.0:  # a teleport / respawn is not walking
            dist += step
        if math.hypot(p[1] - ax, p[2] - ay) > LEAVE:
            ax, ay, at, counted = p[1], p[2], p[0], False
        if not counted and p[0] - at >= DWELL:
            areas += 1
            counted = True
    return {"ground": ground, "leave": leave, "areas_per_8min": areas * WINDOW / ground,
            "m_per_min": dist / 100.0 / (ground / 60.0), "land": (lx, ly), "t_land": t_land}


def analyze(files, want_bots, recorder_only=False):
    rows = []
    for path in files:
        tr = tracks(path)
        players = []
        for key, track in tr.items():
            if bool(track["bot"]) != want_bots or not track["pts"]:
                continue
            m = player_metrics(track["pts"])
            if m:
                m["name"] = track["name"]
                players.append(m)
        if recorder_only and players:
            # The recorder: the player seen longest in the replay.
            longest = max(tr.values(), key=lambda t: len(t["pts"]))
            players = [m for m in players if m["name"] == longest["name"]]
        for m in players:
            m["hot"] = sum(1 for o in players if o is not m and abs(o["t_land"] - m["t_land"]) < 60
                           and math.hypot(o["land"][0] - m["land"][0], o["land"][1] - m["land"][1]) < HOT)
        rows += players
    return {
        "players": len(rows),
        "seconds_until_leaving_landing_area": stats([r["leave"] if r["leave"] is not None else r["ground"] for r in rows]),
        "pct_never_leaving_landing_area": round(100 * sum(1 for r in rows if r["leave"] is None) / max(1, len(rows)), 1),
        "areas_stayed_per_8min": stats([r["areas_per_8min"] for r in rows]),
        "meters_per_minute": stats([r["m_per_min"] for r in rows]),
        "others_landing_within_100m": stats([r["hot"] for r in rows]),
        "landings": [[round(r["land"][0]), round(r["land"][1])] for r in rows],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", help="a sim run folder (its backend-data/matches telemetry)")
    ap.add_argument("--out", default=os.path.join(ROOT, "ml", "models", "rotation_metrics.json"))
    args = ap.parse_args()
    human_files = sorted(glob.glob(os.path.join(ROOT, "ml", "data", "replays_ch1_jsonl", "kuinox_*.jsonl")))
    result = {"humans_all": analyze(human_files, False), "humans_recorder": analyze(human_files, False, True)}
    if args.sim:
        sim_files = sorted(glob.glob(os.path.join(args.sim, "backend-data", "matches", "*.jsonl")))
        result["bots"] = analyze(sim_files, True)
    json.dump(result, open(args.out, "w", encoding="utf-8"), indent=1)
    keys = [k for k in result["humans_all"] if k != "landings"]
    print(f"{'':38}" + "".join(f"{side:>22}" for side in result))
    for k in keys:
        cells = []
        for side in result:
            v = result[side][k]
            cells.append(f"{v['median']} ({v['p25']}-{v['p75']})" if isinstance(v, dict) and v.get("n") else str(v.get("n", v)) if isinstance(v, dict) else str(v))
        print(f"{k:38}" + "".join(f"{c:>22}" for c in cells))
    print("->", args.out)


if __name__ == "__main__":
    main()
