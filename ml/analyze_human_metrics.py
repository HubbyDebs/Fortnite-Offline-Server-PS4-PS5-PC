"""Extract real human Chapter 1 behavioral benchmarks from extracted JSONL matches.

Directly measures the CoreV2 bot-tuning calibration metrics:
  - B1:   Storm Rotation safety margin (time_remaining - dist_to_edge / run_speed)
  - B:    Time spent in storm & % deaths in storm
  - C2:   Early-game looting window (seconds staying within 45m of landing spot)
  - D:    Fight duration, % ending in elimination, and third-party frequency
  - F:    High-ground (TakeHeight) frequency & high-ground win rate in fights
  - A:    Idle/stationary %, crouch/sprint %, distance moved per storm phase
  - End:  Late-game (phase >= 5) distance to zone center & hiding vs fighting
  - Diff: Top 10% (high-kill/survivor) vs Median player (Level 9 vs Level 3 calibration)

Usage:
  python ml/analyze_human_metrics.py --data "ml/data/replays_ch1_jsonl/*.jsonl" --out ml/models/human_metrics_ch1.json
"""
import argparse
import glob
import json
import math
import os
from collections import defaultdict

import numpy as np


SPRINT_SPEED_CH1 = 550.0  # uu/s authentic Chapter 1 / v7.40 sprint speed (5.5 m/s)


def pct(arr, q):
    return float(np.percentile(arr, q)) if len(arr) else 0.0


def summarize(arr):
    if not arr:
        return {"n": 0, "mean": 0.0, "p25": 0.0, "median": 0.0, "p75": 0.0, "p90": 0.0}
    a = np.asarray(arr, dtype=np.float64)
    return {
        "n": int(len(a)),
        "mean": round(float(a.mean()), 2),
        "p25": round(float(np.percentile(a, 25)), 2),
        "median": round(float(np.percentile(a, 50)), 2),
        "p75": round(float(np.percentile(a, 75)), 2),
        "p90": round(float(np.percentile(a, 90)), 2),
    }


def analyze_matches(paths: list[str]):
    # B1: Storm rotation lead margin (seconds) when outside next zone and starting to move inward
    rot_margins = []
    rot_time_rem = []
    rot_travel_needed = []
    rot_dist_edge_m = []

    # Fase B: Storm exposure & deaths
    total_landed_sec = 0.0
    storm_sec = 0.0
    deaths_total = 0
    deaths_in_storm = 0
    player_storm_sec_per_match = []

    # C2: Early looting proxy (time from first landed frame until leaving 45m radius of drop spot)
    loot_stay_sec = []

    # Fase A: Speed & movement
    total_move_obs = 0
    idle_obs = 0
    crouch_obs = 0
    sprint_obs = 0
    speeds_uu = []
    dist_by_phase = defaultdict(list)

    # Fase D & F: Fights (< 35m), duration, elimination %, third-party %, high-ground winrate
    fight_durations = []
    fight_ended_kill = 0
    fight_third_party = 0
    fights_total = 0
    hg_fights_resolved = 0
    hg_winner_had_height = 0
    hg_climbed_during_fight = 0

    # Endgame (storm phase >= 5)
    endgame_norm_dist = []
    endgame_idle = 0
    endgame_fighting = 0
    endgame_total = 0

    # Per-player summary for Top 10% vs Median player (Level 9 vs Level 3)
    player_profiles = []

    matches_analyzed = 0
    for path in sorted(paths):
        if not os.path.isfile(path) or os.path.getsize(path) < 1024:
            continue
        frames = []
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    frames.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        if len(frames) < 5:
            continue
        matches_analyzed += 1

        # Track per-player trajectory inside this match
        p_first_land = {}       # aid -> (t, x, y)
        p_left_drop = set()     # aid
        p_storm_sec = defaultdict(float)
        p_dist_phase = defaultdict(lambda: defaultdict(float))
        p_stats = defaultdict(lambda: {
            "kills": 0, "frames": 0, "idle": 0, "storm_frames": 0,
            "rot_margins": [], "z_gains": 0, "dist": 0.0, "last_t": 0.0, "first_t": 1e12,
        })

        # Active pair fights: (min_id, max_id) -> dict
        active_fights = {}

        prev_by_id = {}
        prev_t = None

        for snap in frames:
            t = snap.get("ts", 0) / 1000.0
            dt = (t - prev_t) if prev_t is not None else 0.0
            if dt < 0 or dt > 25.0:
                dt = 0.0
            storm = snap.get("storm") or {}
            phase = int(storm.get("phase", 0) or 0)
            cur_r = float(storm.get("radius", 0.0) or 0.0)
            cur_x = float(storm.get("x", 0.0) or 0.0)
            cur_y = float(storm.get("y", 0.0) or 0.0)
            nxt_r = float(storm.get("nextRadius", 0.0) or 0.0)
            nxt_x = float(storm.get("nextX", 0.0) or 0.0)
            nxt_y = float(storm.get("nextY", 0.0) or 0.0)
            time_rem = float(storm.get("timeRemaining", snap.get("timeRemaining", 60.0)) or 60.0)

            actors = snap.get("actors", ())
            landed = []
            cur_by_id = {}

            for a in actors:
                aid = a.get("id") or a.get("name")
                if not aid:
                    continue
                cur_by_id[aid] = a
                alive = bool(a.get("alive", True))
                state = a.get("state", "Landed")
                x, y, z = float(a.get("x", 0.0)), float(a.get("y", 0.0)), float(a.get("z", 0.0))
                in_storm = bool(a.get("inStorm", False)) or (cur_r > 0 and math.hypot(x - cur_x, y - cur_y) > cur_r)

                prev = prev_by_id.get(aid)
                if prev is not None and prev.get("alive", True) and not alive:
                    deaths_total += 1
                    if in_storm or prev.get("inStorm", False):
                        deaths_in_storm += 1

                if not alive or state != "Landed":
                    continue

                landed.append((aid, a, x, y, z))
                st = p_stats[aid]
                st["frames"] += 1
                st["kills"] = max(st["kills"], int(a.get("kills", 0) or 0))
                st["first_t"] = min(st["first_t"], t)
                st["last_t"] = max(st["last_t"], t)

                if aid not in p_first_land:
                    p_first_land[aid] = (t, x, y)
                elif aid not in p_left_drop:
                    lt, lx, ly = p_first_land[aid]
                    if math.hypot(x - lx, y - ly) > 4500.0:  # moved > 45m from drop spot
                        dur_loot = t - lt
                        if 5.0 <= dur_loot <= 360.0:
                            loot_stay_sec.append(dur_loot)
                        p_left_drop.add(aid)

                if dt > 0:
                    total_landed_sec += dt
                    if in_storm:
                        storm_sec += dt
                        p_storm_sec[aid] += dt
                        st["storm_frames"] += 1

                if prev is not None and prev.get("state") == "Landed" and dt > 0:
                    px, py, pz = float(prev.get("x", x)), float(prev.get("y", y)), float(prev.get("z", z))
                    move2d = math.hypot(x - px, y - py)
                    spd = move2d / dt
                    if spd <= 900.0:
                        total_move_obs += 1
                        speeds_uu.append(spd)
                        st["dist"] += move2d
                        if phase > 0:
                            p_dist_phase[aid][phase] += move2d
                        if spd < 60.0:
                            idle_obs += 1
                            st["idle"] += 1
                        if a.get("crouched", False):
                            crouch_obs += 1
                        if spd > 420.0:
                            sprint_obs += 1
                        if z - pz > 200.0:
                            st["z_gains"] += 1

                        # B1: Storm rotation onset (outside next safe zone and moving inward > 200 uu/s)
                        if nxt_r > 0 and phase >= 1:
                            d0 = math.hypot(px - nxt_x, py - nxt_y) - nxt_r
                            d1 = math.hypot(x - nxt_x, y - nxt_y) - nxt_r
                            if d0 > 1500.0 and (d0 - d1) / dt > 180.0 and not in_storm:
                                travel_s = d0 / SPRINT_SPEED_CH1
                                margin_s = time_rem - travel_s
                                if -90.0 <= margin_s <= 180.0:
                                    rot_margins.append(margin_s)
                                    rot_time_rem.append(time_rem)
                                    rot_travel_needed.append(travel_s)
                                    rot_dist_edge_m.append(d0 / 100.0)
                                    st["rot_margins"].append(margin_s)

            # Endgame metrics (phase >= 5)
            if phase >= 5 and cur_r > 500.0:
                for aid, a, x, y, z in landed:
                    endgame_total += 1
                    endgame_norm_dist.append(min(1.5, math.hypot(x - cur_x, y - cur_y) / cur_r))
                    prev = prev_by_id.get(aid)
                    if prev is not None and dt > 0:
                        spd = math.hypot(x - float(prev.get("x", x)), y - float(prev.get("y", y))) / dt
                        if spd < 60.0:
                            endgame_idle += 1
                    # Check if fighting (enemy within 35m)
                    t_a = a.get("team", -1)
                    near = any(
                        oid != aid and (t_a == -1 or oa.get("team", -2) != t_a)
                        and math.hypot(x - ox, y - oy) < 3500.0
                        for oid, oa, ox, oy, _ in landed
                    )
                    if near:
                        endgame_fighting += 1

            # Pairwise fights (< 30m = 3000 uu) during actual SafeZones (excludes Battle Bus / spawn island cluster)
            if snap.get("phase") != "SafeZones":
                prev_by_id = cur_by_id
                prev_t = t
                continue
            n_l = len(landed)
            seen_pairs = set()
            for i in range(n_l):
                id1, a1, x1, y1, z1 = landed[i]
                t1 = a1.get("team", -1)
                k1 = int(a1.get("kills", 0) or 0)
                for j in range(i + 1, n_l):
                    id2, a2, x2, y2, z2 = landed[j]
                    if t1 != -1 and a2.get("team", -2) == t1:
                        continue
                    k2 = int(a2.get("kills", 0) or 0)
                    d = math.hypot(x1 - x2, y1 - y2)
                    if d < 3000.0:
                        key = (id1, id2) if id1 < id2 else (id2, id1)
                        seen_pairs.add(key)
                        if key not in active_fights:
                            active_fights[key] = {
                                "start_t": t, "last_t": t,
                                "init_z1": z1, "init_z2": z2,
                                "max_z1": z1, "max_z2": z2,
                                "last_z1": z1, "last_z2": z2,
                                "init_k1": k1, "init_k2": k2,
                                "max_k1": k1, "max_k2": k2,
                                "frames": 1,
                                "third_party": False,
                            }
                        else:
                            finfo = active_fights[key]
                            finfo["last_t"] = t
                            finfo["frames"] += 1
                            finfo["max_z1"] = max(finfo["max_z1"], z1)
                            finfo["max_z2"] = max(finfo["max_z2"], z2)
                            finfo["last_z1"] = z1
                            finfo["last_z2"] = z2
                            finfo["max_k1"] = max(finfo["max_k1"], k1)
                            finfo["max_k2"] = max(finfo["max_k2"], k2)
                        # Check if a 3rd enemy is within 40m of fight midpoint
                        mx, my = (x1 + x2) * 0.5, (y1 + y2) * 0.5
                        for k in range(n_l):
                            if k == i or k == j:
                                continue
                            id3, a3, x3, y3, _ = landed[k]
                            t3 = a3.get("team", -1)
                            if (t1 != -1 and t3 == t1) or (a2.get("team", -1) != -1 and t3 == a2.get("team", -1)):
                                continue
                            if math.hypot(x3 - mx, y3 - my) < 4000.0:
                                active_fights[key]["third_party"] = True
                                break

            # Close fights that ended (kill scored, player died, or separated > 15s)
            to_close = []
            for key, finfo in active_fights.items():
                id1, id2 = key
                a1, a2 = cur_by_id.get(id1), cur_by_id.get(id2)
                dead1 = (a1 is not None and not a1.get("alive", True)) or (finfo["max_k2"] > finfo["init_k2"] and a1 is None)
                dead2 = (a2 is not None and not a2.get("alive", True)) or (finfo["max_k1"] > finfo["init_k1"] and a2 is None)
                killed = (finfo["max_k1"] > finfo["init_k1"]) or (finfo["max_k2"] > finfo["init_k2"]) or dead1 or dead2
                if killed or (t - finfo["last_t"] > 15.0):
                    dur = finfo["last_t"] - finfo["start_t"]
                    # Require either >=2 frames (real encounter, not 1-frame drive-by) or a kill
                    if (finfo["frames"] >= 2 or killed) and dur <= 180.0:
                        fights_total += 1
                        fight_durations.append(max(5.0, dur))
                        if finfo["third_party"]:
                            fight_third_party += 1
                        climbed = (finfo["max_z1"] - finfo["init_z1"] > 280.0) or (finfo["max_z2"] - finfo["init_z2"] > 280.0)
                        if climbed:
                            hg_climbed_during_fight += 1
                        if killed:
                            fight_ended_kill += 1
                            p1_won = (finfo["max_k1"] > finfo["init_k1"]) or dead2
                            p2_won = (finfo["max_k2"] > finfo["init_k2"]) or dead1
                            if p1_won ^ p2_won:
                                dz = (finfo["last_z1"] - finfo["last_z2"]) if p1_won else (finfo["last_z2"] - finfo["last_z1"])
                                if abs(dz) >= 220.0:  # clear height difference (~1 tile = 384 uu)
                                    hg_fights_resolved += 1
                                    if dz > 0:
                                        hg_winner_had_height += 1
                    to_close.append(key)
            for key in to_close:
                del active_fights[key]

            prev_by_id = cur_by_id
            prev_t = t

        for aid, sec in p_storm_sec.items():
            player_storm_sec_per_match.append(sec)
        for aid, ph_map in p_dist_phase.items():
            for ph, d_uu in ph_map.items():
                if 1 <= ph <= 7:
                    dist_by_phase[ph].append(d_uu / 100.0)  # meters
        for aid, st in p_stats.items():
            if st["frames"] >= 8:
                surv = max(0.0, st["last_t"] - st["first_t"])
                player_profiles.append({
                    "kills": st["kills"],
                    "survival_s": surv,
                    "idle_pct": 100.0 * st["idle"] / st["frames"],
                    "storm_pct": 100.0 * st["storm_frames"] / st["frames"],
                    "rot_margin_med": float(np.median(st["rot_margins"])) if st["rot_margins"] else None,
                    "z_gains": st["z_gains"],
                    "dist_m": st["dist"] / 100.0,
                })

    # Split Top 10% vs Median player (score = kills * 120 + survival_s)
    player_profiles.sort(key=lambda p: p["kills"] * 120.0 + p["survival_s"])
    n_p = len(player_profiles)
    top10 = player_profiles[int(n_p * 0.90):] if n_p >= 20 else []
    mid50 = player_profiles[int(n_p * 0.25): int(n_p * 0.75)] if n_p >= 20 else []

    def profile_summary(group):
        if not group:
            return {}
        rm = [p["rot_margin_med"] for p in group if p["rot_margin_med"] is not None]
        return {
            "players": len(group),
            "avg_kills": round(float(np.mean([p["kills"] for p in group])), 2),
            "avg_survival_s": round(float(np.mean([p["survival_s"] for p in group])), 1),
            "idle_pct": round(float(np.mean([p["idle_pct"] for p in group])), 1),
            "storm_time_pct": round(float(np.mean([p["storm_pct"] for p in group])), 1),
            "rot_margin_med_s": round(float(np.median(rm)), 1) if rm else None,
            "avg_height_gains": round(float(np.mean([p["z_gains"] for p in group])), 2),
            "avg_dist_m": round(float(np.mean([p["dist_m"] for p in group])), 1),
        }

    result = {
        "matches_analyzed": matches_analyzed,
        "B1_storm_rotation": {
            "safety_margin_sec": summarize(rot_margins),
            "time_remaining_sec": summarize(rot_time_rem),
            "travel_time_needed_sec": summarize(rot_travel_needed),
            "dist_to_next_edge_m": summarize(rot_dist_edge_m),
        },
        "Fase_B_storm_exposure": {
            "pct_time_in_storm": round(100.0 * storm_sec / max(1.0, total_landed_sec), 2),
            "storm_sec_per_player_match": summarize(player_storm_sec_per_match),
            "deaths_observed": deaths_total,
            "deaths_in_storm_pct": round(100.0 * deaths_in_storm / max(1, deaths_total), 2),
        },
        "C2_early_looting_proxy": {
            "sec_staying_within_45m_of_drop": summarize(loot_stay_sec),
        },
        "Fase_D_fights": {
            "fights_detected": fights_total,
            "duration_sec": summarize(fight_durations),
            "pct_ending_in_elim": round(100.0 * fight_ended_kill / max(1, fights_total), 2),
            "pct_third_party": round(100.0 * fight_third_party / max(1, fights_total), 2),
        },
        "Fase_F_height_advantage": {
            "pct_fights_with_climb_gt_280uu": round(100.0 * hg_climbed_during_fight / max(1, fights_total), 2),
            "resolved_fights_with_clear_height_diff": hg_fights_resolved,
            "high_ground_win_rate_pct": round(100.0 * hg_winner_had_height / max(1, hg_fights_resolved), 2),
        },
        "Fase_A_movement": {
            "samples": total_move_obs,
            "pct_idle_lt_60uus": round(100.0 * idle_obs / max(1, total_move_obs), 2),
            "pct_crouched": round(100.0 * crouch_obs / max(1, total_move_obs), 2),
            "pct_sprinting_gt_420uus": round(100.0 * sprint_obs / max(1, total_move_obs), 2),
            "speed_uus": summarize(speeds_uu),
            "dist_moved_per_phase_m": {f"phase_{k}": summarize(v) for k, v in sorted(dist_by_phase.items())},
        },
        "Endgame_phase_5_plus": {
            "samples": endgame_total,
            "norm_dist_to_center": summarize(endgame_norm_dist),
            "pct_stationary_hiding": round(100.0 * endgame_idle / max(1, endgame_total), 2),
            "pct_actively_fighting_lt_35m": round(100.0 * endgame_fighting / max(1, endgame_total), 2),
        },
        "Difficulty_Top10_vs_Median": {
            "total_player_tracks": n_p,
            "top_10_pct_lvl9": profile_summary(top10),
            "median_player_lvl3": profile_summary(mid50),
        },
    }
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", nargs="+", default=["ml/data/replays_ch1_jsonl/*.jsonl"])
    ap.add_argument("--out", default="ml/models/human_metrics_ch1.json")
    args = ap.parse_args()

    paths = sorted(set(p for pat in args.data for p in glob.glob(pat)))
    res = analyze_matches(paths)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2)
    print(json.dumps(res, indent=2))
    print(f"\nSaved -> {args.out}")


if __name__ == "__main__":
    main()
