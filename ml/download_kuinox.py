"""Download authentic Chapter 1 (Season 7 & Season 6) .replay files from Kuinox/FortniteReplayCollection.

Prioritizes the 126 confirmed Season 7 replays (Dec 2018 - Feb 2019 commits), then Season 6 replays
(Nov 2018), downloading the real Git LFS binaries via media.githubusercontent.com in parallel.

Usage:
  python ml/download_kuinox.py --count 100 --out ml/data/replays_ch1
  python ml/download_kuinox.py --s7-only --out ml/data/replays_ch1
"""
import argparse
import concurrent.futures
import json
import os
import urllib.request

HEADERS = {"User-Agent": "FortniteOfflineServer-ML"}
S7_COMMITS = [
    "ebc5d9ce",  # 2019-02-05 (8 replays, late Season 7)
    "ab5c4c32",  # 2018-12-09 (23 replays, Season 7)
    "d52fae87",  # 2018-12-08 (31 replays, Season 7)
    "b61d2ca9",  # 2018-12-07 (64 replays, Season 7)
]


def fetch_json(url: str):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_candidate_list(s7_only: bool) -> list[tuple[str, str]]:
    seen = set()
    ordered: list[tuple[str, str]] = []

    for sha in S7_COMMITS:
        data = fetch_json(f"https://api.github.com/repos/Kuinox/FortniteReplayCollection/commits/{sha}")
        for f in data.get("files", []):
            name = f.get("filename", "")
            if name.endswith(".replay") and name not in seen:
                seen.add(name)
                ordered.append((name, "s7"))

    if not s7_only:
        tree = fetch_json("https://api.github.com/repos/Kuinox/FortniteReplayCollection/git/trees/master?recursive=1")
        for item in tree.get("tree", []):
            name = item.get("path", "")
            if name.endswith(".replay") and name not in seen:
                seen.add(name)
                ordered.append((name, "s6"))

    return ordered


def download_one(item: tuple[str, str], out_dir: str) -> tuple[str, str, int]:
    name, tag = item
    out_name = f"kuinox_{tag}_{os.path.basename(name)}"
    dest = os.path.join(out_dir, out_name)
    if os.path.isfile(dest) and os.path.getsize(dest) > 50_000:
        return out_name, "exists", os.path.getsize(dest)

    url = f"https://media.githubusercontent.com/media/Kuinox/FortniteReplayCollection/master/{name}"
    req = urllib.request.Request(url, headers=HEADERS)
    tmp = dest + ".part"
    try:
        with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "wb") as f:
            total = 0
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                total += len(chunk)
        # Reject tiny LFS pointer files (< 1 KB)
        if total < 10_000:
            os.remove(tmp)
            return out_name, f"too_small({total})", 0
        os.replace(tmp, dest)
        return out_name, "ok", total
    except Exception as e:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        return out_name, f"err:{e}", 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=126, help="Max number of replays to download (default 126 = all S7)")
    ap.add_argument("--s7-only", action="store_true", help="Only download confirmed Season 7 commits (126 files)")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out", default="ml/data/replays_ch1")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    candidates = get_candidate_list(args.s7_only)
    targets = candidates[: args.count]
    s7_n = sum(1 for _, t in targets if t == "s7")
    s6_n = sum(1 for _, t in targets if t == "s6")
    print(f"Downloading {len(targets)} Chapter 1 replays ({s7_n} Season 7 + {s6_n} Season 6) into {args.out} ...")

    ok = exists = failed = 0
    total_bytes = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = [pool.submit(download_one, item, args.out) for item in targets]
        for idx, fut in enumerate(concurrent.futures.as_completed(futs), 1):
            name, status, sz = fut.result()
            total_bytes += sz
            if status == "ok":
                ok += 1
            elif status == "exists":
                exists += 1
            else:
                failed += 1
                print(f"  FAIL {name}: {status}")
            if idx % 20 == 0 or idx == len(targets):
                print(f"  Progress: {idx}/{len(targets)} (new={ok}, cached={exists}, fail={failed}, {total_bytes/1e6:.1f} MB)")

    print(f"Done! {ok + exists}/{len(targets)} ready ({total_bytes/1e6:.1f} MB).")


if __name__ == "__main__":
    main()
