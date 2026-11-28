"""Download all publicly archived Fortnite Season 3 (v3.60 & v3.50) .replay files from EZFNDEV/FortniteReplayArchive.

Fortnite's Replay system launched in v3.50 (April 11, 2018) and v3.60 lasted 7 days (April 24 - May 1, 2018),
so publicly archived Season 3 replays total 20 matches (1 from v3.60 CL-4019403 + 19 from v3.50 CL-4008490,
both on the exact Season 3 island).

Usage:
  python ml/download_s3.py --out ml/data/replays_s3
"""
import argparse
import concurrent.futures
import os
import urllib.parse
import urllib.request

HEADERS = {"User-Agent": "FortniteOfflineServer-ML"}

S3_FILES = [
    # Exact v3.60 (CL-4019403)
    ("3.60", "UnsavedReplay-2018.04.24-09.12.30.replay"),
    # v3.50 (CL-4008490, identical Season 3 island)
    ("3.50", "2.replay"),
    ("3.50", "3.replay"),
    ("3.50", "Teamers.replay"),
    ("3.50", "UnsavedReplay-2018.04.19-02.04.04.replay"),
    ("3.50", "UnsavedReplay-2018.04.20-17.05.33.replay"),
    ("3.50", "UnsavedReplay-2018.04.20-17.09.14.replay"),
    ("3.50", "UnsavedReplay-2018.04.20-17.11.48.replay"),
    ("3.50", "UnsavedReplay-2018.04.20-17.16.03.replay"),
    ("3.50", "UnsavedReplay-2018.04.20-17.20.18.replay"),
    ("3.50", "UnsavedReplay-2018.04.20-17.30.00.replay"),
    ("3.50", "UnsavedReplay-2018.04.20-17.42.02.replay"),
    ("3.50", "UnsavedReplay-2018.04.20-20.05.00.replay"),
    ("3.50", "UnsavedReplay-2018.04.20-20.10.37.replay"),
    ("3.50", "UnsavedReplay-2018.04.20-20.17.19.replay"),
    ("3.50", "UnsavedReplay-2018.04.20-22.43.39.replay"),
    ("3.50", "UnsavedReplay-2018.04.20-23.19.48.replay"),
    ("3.50", "UnsavedReplay-2018.04.20-23.33.13.replay"),
    ("3.50", "dancenodance.replay"),
    ("3.50", "test3.replay"),
]


def download_one(item: tuple[str, str], out_dir: str) -> tuple[str, str, int]:
    ver, fname = item
    tag = ver.replace(".", "")
    out_name = f"s3_{tag}_{fname}"
    dest = os.path.join(out_dir, out_name)
    if os.path.isfile(dest) and os.path.getsize(dest) > 50_000:
        return out_name, "exists", os.path.getsize(dest)

    folder = urllib.parse.quote(f"++Fortnite+Release-{ver}", safe="")
    enc_name = urllib.parse.quote(fname, safe="")
    urls = [
        f"https://media.githubusercontent.com/media/EZFNDEV/FortniteReplayArchive/main/{folder}/{enc_name}",
        f"https://raw.githubusercontent.com/EZFNDEV/FortniteReplayArchive/main/{folder}/{enc_name}",
    ]
    tmp = dest + ".part"
    last_err = ""
    for url in urls:
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "wb") as f:
                total = 0
                head = b""
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    if len(head) < 64:
                        head += chunk[: 64 - len(head)]
                    f.write(chunk)
                    total += len(chunk)
            if total < 10_000 or head.startswith(b"version https://git-lfs"):
                os.remove(tmp)
                last_err = f"pointer_or_small({total})"
                continue
            os.replace(tmp, dest)
            return out_name, "ok", total
        except Exception as e:
            last_err = str(e)
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
    return out_name, f"err:{last_err}", 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="ml/data/replays_s3")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    print(f"Downloading {len(S3_FILES)} Season 3 replays (1x v3.60 + 19x v3.50) into {args.out} ...")

    ok = exists = failed = 0
    total_bytes = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = [pool.submit(download_one, item, args.out) for item in S3_FILES]
        for fut in concurrent.futures.as_completed(futs):
            name, st, sz = fut.result()
            total_bytes += sz
            if st == "ok":
                ok += 1
                print(f"  OK {name} ({sz/1e6:.2f} MB)")
            elif st == "exists":
                exists += 1
                print(f"  CACHED {name} ({sz/1e6:.2f} MB)")
            else:
                failed += 1
                print(f"  FAIL {name}: {st}")

    print(f"Done: {ok + exists}/{len(S3_FILES)} ready (new={ok}, cached={exists}, fail={failed}, {total_bytes/1e6:.1f} MB).")


if __name__ == "__main__":
    main()
