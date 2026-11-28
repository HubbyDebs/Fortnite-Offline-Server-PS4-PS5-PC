"""Play back any unified JSONL match (from CoreV2, .replay extractor, or sim2d) onto the Live Tactical Map."""
import argparse
import json
import time
import urllib.request


def main():
    ap = argparse.ArgumentParser(description="Replay a JSONL match on Fortnite Offline Server's Live Map")
    ap.add_argument("file", help="Path to a .jsonl match file")
    ap.add_argument("--port", type=int, default=5595)
    ap.add_argument("--speed", type=float, default=2.0, help="Playback speed multiplier")
    ap.add_argument("--dt", type=float, default=0.5, help="Default seconds between frames if ts is absent")
    args = ap.parse_args()

    url = f"http://127.0.0.1:{args.port}/fortnite/api/game/v2/matchmakingservice/telemetry"
    frames = []
    with open(args.file, encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    frames.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    if not frames:
        raise SystemExit(f"No valid frames in {args.file}")

    print(f"Playing {len(frames)} frames from {args.file} -> {url} ({args.speed}x)")
    prev_ts = None
    for i, snap in enumerate(frames):
        snap["record"] = False
        ts = snap.get("ts")
        if prev_ts is not None and ts is not None and ts > prev_ts:
            delay = min(3.0, (ts - prev_ts) / 1000.0)
        else:
            delay = args.dt
        prev_ts = ts
        req = urllib.request.Request(url, data=json.dumps(snap).encode("utf-8"), headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=0.6):
                pass
        except Exception as e:
            if i == 0:
                print(f"Warning: could not reach backend on port {args.port} ({e}). Make sure Fortnite Offline Server is running.")
        time.sleep(max(0.05, delay / max(0.1, args.speed)))
    print("Playback finished.")


if __name__ == "__main__":
    main()
