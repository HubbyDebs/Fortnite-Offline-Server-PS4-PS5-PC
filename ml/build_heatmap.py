"""Aggregate recorded JSONL matches (CoreV2, .replay extractor, sim2d) into a 50x50 Season 7 Influence Map."""
import argparse
import glob
import json
import os

from schema import WORLD_MIN, WORLD_SPAN


def to_cell(x: float, y: float, grid: int = 50) -> tuple[int, int]:
    u = int((y - WORLD_MIN) / WORLD_SPAN * grid)
    v = int((1.0 - (x - WORLD_MIN) / WORLD_SPAN) * grid)
    return max(0, min(grid - 1, u)), max(0, min(grid - 1, v))


def main():
    ap = argparse.ArgumentParser(description="Build 50x50 Season 7 Influence Map from JSONL matches")
    ap.add_argument("--data", nargs="+", default=[
        "ml/data/replays_ch1_jsonl/*.jsonl",
        "server/data/matches/*.jsonl",
    ])
    ap.add_argument("--grid", type=int, default=50)
    ap.add_argument("--out", type=str, default="server/data/heatmap-s7.json")
    args = ap.parse_args()

    paths = sorted(set(p for pat in args.data for p in glob.glob(pat)))
    g = args.grid
    traffic = [[0.0] * g for _ in range(g)]
    deaths = [[0.0] * g for _ in range(g)]
    landings = [[0.0] * g for _ in range(g)]
    matches = 0

    for path in paths:
        prev_state = {}
        prev_alive = {}
        seen = False
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    snap = json.loads(line)
                except json.JSONDecodeError:
                    continue
                seen = True
                for a in snap.get("actors", ()):
                    aid = a.get("id") or a.get("name")
                    if not aid:
                        continue
                    x, y = float(a.get("x", 0.0)), float(a.get("y", 0.0))
                    u, v = to_cell(x, y, g)
                    alive = bool(a.get("alive", True))
                    state = str(a.get("state", "Landed"))
                    if alive and state == "Landed":
                        traffic[v][u] += 1.0
                        if prev_state.get(aid) in ("Skydiving", "InAircraft"):
                            landings[v][u] += 1.0
                    elif prev_alive.get(aid) is True and not alive:
                        deaths[v][u] += 1.0
                    prev_state[aid] = state
                    prev_alive[aid] = alive
        if seen:
            matches += 1

    def normalize(mat):
        m = max((val for row in mat for val in row), default=0.0)
        if m <= 0.0:
            return mat
        return [[round(val / m, 4) for val in row] for row in mat]

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({
            "grid": g,
            "worldMin": WORLD_MIN,
            "worldSpan": WORLD_SPAN,
            "matches": matches,
            "traffic": normalize(traffic),
            "deaths": normalize(deaths),
            "landings": normalize(landings),
        }, f)
    print(f"Built {g}x{g} heatmap from {matches} match(es) -> {args.out}")


if __name__ == "__main__":
    main()
