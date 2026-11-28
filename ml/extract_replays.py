"""Parallel batch wrapper around ml/replay-extractor/bin/Release/net8.0/ReplayExtractor.exe.

Runs multiple Chapter 1/2+ .replay files in parallel with a per-file timeout so any single
corrupted replay never blocks the batch.

Usage:
  python ml/extract_replays.py --in ml/data/replays_ch1 --out ml/data/replays_ch1_jsonl --workers 6
"""
import argparse
import concurrent.futures
import glob
import os
import subprocess
import time


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_dir", default="ml/data/replays_ch1")
    ap.add_argument("--out", dest="out_dir", default="ml/data/replays_ch1_jsonl")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--timeout", type=int, default=45)
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()

    exe = os.path.abspath("ml/replay-extractor/bin/Release/net8.0/ReplayExtractor.exe")
    if not os.path.isfile(exe):
        raise SystemExit(f"ReplayExtractor.exe not found at {exe}. Build with dotnet build ml/replay-extractor -c Release")

    out_dir = os.path.abspath(args.out_dir)
    os.makedirs(out_dir, exist_ok=True)
    files = sorted(glob.glob(os.path.join(args.in_dir, "*.replay")))
    print(f"Extracting {len(files)} replays from {args.in_dir} -> {out_dir} ({args.workers} workers)...")
    t0 = time.time()

    def run_one(path: str):
        name = os.path.basename(path)
        out_file = os.path.join(out_dir, os.path.splitext(name)[0] + ".jsonl")
        if not args.overwrite and os.path.isfile(out_file) and os.path.getsize(out_file) > 1024:
            return name, "cached", ""
        cmd = [exe, path, out_dir] + (["--overwrite"] if args.overwrite else [])
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=args.timeout)
            for line in (r.stdout or "").splitlines():
                if "  OK " in line:
                    return name, "ok", line.strip()
                if "  FAIL " in line or "  -- " in line:
                    return name, "skip", line.strip()
            return name, "fail", (r.stdout + r.stderr).strip()[-120:]
        except subprocess.TimeoutExpired:
            return name, "timeout", f"timeout>{args.timeout}s"
        except Exception as e:
            return name, "fail", str(e)

    ok = cached = skip = fail = timeout = s7_ok = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = [pool.submit(run_one, p) for p in files]
        for i, fut in enumerate(concurrent.futures.as_completed(futs), 1):
            name, st, msg = fut.result()
            if st == "ok":
                ok += 1
                if "[7." in msg or "_s7_" in name:
                    s7_ok += 1
            elif st == "cached":
                cached += 1
            elif st == "skip":
                skip += 1
            elif st == "timeout":
                timeout += 1
            else:
                fail += 1
            if i % 20 == 0 or i == len(files):
                print(
                    f"[{i:3d}/{len(files)}] ok={ok} (s7={s7_ok}) cached={cached} skip={skip} "
                    f"fail={fail} to={timeout} ({time.time()-t0:.0f}s) | {msg[:90]}"
                )

    print(f"Done in {time.time()-t0:.1f}s: ok={ok}, cached={cached}, skip={skip}, fail={fail}, timeout={timeout}")


if __name__ == "__main__":
    main()
