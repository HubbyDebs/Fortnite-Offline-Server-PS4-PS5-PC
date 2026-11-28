"""Fights: the bots' kills by weapon and distance (a sim run's server logs) against Chapter 1's kill feed
(ml/models/ch1_events_metrics.json, D1_killfeed_weapon_distances).

  python -I ml/analyze_fights.py _local/sim/<run>
"""
import glob
import json
import os
import re
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LINE_KNOCK = re.compile(r"Battle Royale bots: \S+ knocks \S+ down with (\S+) from (-?[\d.]+) m")
LINE_ELIM = re.compile(r"Battle Royale bots: \S+ eliminates \S+ with (.+?) from (-?[\d.]+) m")
# 7.40's SMGs are WID_Pistol_AutoHeavySuppressed / WID_Pistol_AutoHeavyPDW: matched before "Pistol".
KINDS = [("Shotgun", "Shotgun"), ("Assault", "Rifle (AR)"), ("SMG", "SMG"), ("AutoHeavy", "SMG"), ("Pistol", "Pistol"),
         ("Sniper", "Sniper"), ("harvesting tool", "Melee (Pickaxe)"), ("Harvest", "Melee (Pickaxe)")]


def kind_of(weapon):
    for key, kind in KINDS:
        if key.lower() in weapon.lower():
            return kind
    return "Other"


def stats(values):
    values = sorted(values)
    if not values:
        return {"n": 0}
    q = lambda p: values[min(len(values) - 1, int(p * len(values)))]
    return {"n": len(values), "median": round(q(0.5), 1), "p25": round(q(0.25), 1), "p75": round(q(0.75), 1), "p90": round(q(0.9), 1)}


def main():
    run = sys.argv[1]
    by_kind = {}
    for log in sorted(glob.glob(os.path.join(run, "match-*.server.log"))):
        for line in open(log, encoding="utf-8", errors="replace"):
            m = LINE_ELIM.search(line) or LINE_KNOCK.search(line)
            if m and float(m.group(2)) >= 0:
                by_kind.setdefault(kind_of(m.group(1)), []).append(float(m.group(2)))
    human = json.load(open(os.path.join(ROOT, "ml", "models", "ch1_events_metrics.json"), encoding="utf-8"))
    hk = {}
    for key, v in human["D1_killfeed_weapon_distances"]["by_death_cause"].items():
        name = key.split(":", 1)[1]
        if v["distance_m"].get("n"):
            hk[name] = v
    total_bot = sum(len(v) for v in by_kind.values()) or 1
    total_h = sum(v["kills"] for k, v in hk.items() if k in {kind for _, kind in KINDS}) or 1
    print(f"{'weapon':18}{'bots share':>11}{'humans share':>14}   {'bots m (p25-p75)':>22}   {'humans m (p25-p75)':>22}")
    for kind in list(dict.fromkeys(k for _, k in KINDS)) + ["Other"]:
        b = stats(by_kind.get(kind, []))
        h = hk.get(kind)
        bs = f"{b['median']} ({b['p25']}-{b['p75']})" if b["n"] else "-"
        hs = f"{h['distance_m']['median']} ({h['distance_m']['p25']}-{h['distance_m']['p75']})" if h else "-"
        hshare = f"{100 * h['kills'] / total_h:.0f}%" if h and kind != "Other" else "-"
        print(f"{kind:18}{100 * b['n'] / total_bot:>10.0f}%{hshare:>14}   {bs:>22}   {hs:>22}")
    print(f"kills and knocks with a distance: {total_bot}")


if __name__ == "__main__":
    main()
