"""Fast 2D Season 7 Athena Tactical Simulator compatible with Fortnite Offline Server Live Map & CoreV2 ML brain.

Modes:
  1) Stream live to the desktop app's Mapa en Vivo:
       python ml/sim2d.py --live --bots 50 --policy mixed --model ml/models/bot_brain.json
  2) Generate headless JSONL training matches at ~1000x real-time:
       python ml/sim2d.py --generate 25 --out ml/data/sim_matches --policy expert
  3) Evaluate ML model head-to-head against heuristic bots:
       python ml/sim2d.py --eval 40 --model ml/models/bot_brain.json
"""
import argparse
import json
import math
import os
import random
import time
import urllib.request
from dataclasses import dataclass, field

import numpy as np

from schema import ACTIONS, S7_POIS, HeatmapLookup, clamp, dist2d, extract_features, extract_features_v2


class TacticalBrain:
    """Pure NumPy evaluator matching CoreAthenaBotBrainWeights.h (V1 MLP) or CoreAthenaBotBrainV2.h (V2 GRU)."""

    def __init__(self, path: str):
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        self.arch = d.get("arch", "mlp")
        self.is_v2 = (self.arch == "gru-v2")
        if self.is_v2:
            self.hidden = int(d["hidden"])
            self.gru_w_ih = np.array(d["gru_w_ih"], dtype=np.float32)
            self.gru_w_hh = np.array(d["gru_w_hh"], dtype=np.float32)
            self.gru_b_ih = np.array(d["gru_b_ih"], dtype=np.float32)
            self.gru_b_hh = np.array(d["gru_b_hh"], dtype=np.float32)
            self.fc_w = np.array(d["fc_w"], dtype=np.float32)
            self.fc_b = np.array(d["fc_b"], dtype=np.float32)
            self.act_w = np.array(d["act_w"], dtype=np.float32)
            self.act_b = np.array(d["act_b"], dtype=np.float32)
            self.dir_w = np.array(d["dir_w"], dtype=np.float32)
            self.dir_b = np.array(d["dir_b"], dtype=np.float32)
            self.bld_w = np.array(d["bld_w"], dtype=np.float32)
            self.bld_b = np.array(d["bld_b"], dtype=np.float32)
        else:
            self.w1 = np.array(d["w1"], dtype=np.float32)
            self.b1 = np.array(d["b1"], dtype=np.float32)
            self.w2 = np.array(d["w2"], dtype=np.float32)
            self.b2 = np.array(d["b2"], dtype=np.float32)
            self.w3 = np.array(d["w3"], dtype=np.float32) if "w3" in d else None
            self.b3 = np.array(d["b3"], dtype=np.float32) if "b3" in d else None

    def init_hidden(self) -> np.ndarray | None:
        return np.zeros(self.hidden, dtype=np.float32) if self.is_v2 else None

    def predict(self, features: list[float]) -> int:
        x = np.array(features, dtype=np.float32)
        h1 = np.maximum(0.0, self.w1 @ x + self.b1)
        if self.w3 is not None:
            h2 = np.maximum(0.0, self.w2 @ h1 + self.b2)
            logits = self.w3 @ h2 + self.b3
        else:
            logits = self.w2 @ h1 + self.b2
        return int(np.argmax(logits))

    def step_v2(self, features26: list[float], h: np.ndarray) -> tuple[int, tuple[float, float], float, np.ndarray]:
        x = np.array(features26, dtype=np.float32)
        H = self.hidden
        gi = self.gru_w_ih @ x + self.gru_b_ih
        gh = self.gru_w_hh @ h + self.gru_b_hh
        r = 1.0 / (1.0 + np.exp(-np.clip(gi[:H] + gh[:H], -20.0, 20.0)))
        z = 1.0 / (1.0 + np.exp(-np.clip(gi[H : 2 * H] + gh[H : 2 * H], -20.0, 20.0)))
        n = np.tanh(gi[2 * H :] + r * gh[2 * H :])
        next_h = (1.0 - z) * n + z * h
        fc = np.maximum(0.0, self.fc_w @ next_h + self.fc_b)
        act_idx = int(np.argmax(self.act_w @ fc + self.act_b))
        mdir = np.tanh(self.dir_w @ fc + self.dir_b)
        b_logit = float((self.bld_w @ fc + self.bld_b)[0])
        b_prob = float(1.0 / (1.0 + math.exp(-max(-20.0, min(20.0, b_logit)))))
        return act_idx, (float(mdir[0]), float(mdir[1])), b_prob, next_h


@dataclass
class SimActor:
    id: str
    name: str
    is_bot: bool
    team: int
    policy: str  # "expert" | "heuristic" | "ml"
    style: str
    x: float
    y: float
    z: float = 32000.0
    yaw: float = 0.0
    hp: float = 100.0
    shield: float = 0.0
    mats: float = 30.0
    heals: int = 0
    has_weapon: bool = False
    weapon_close: bool = False
    loadout: float = 0.0
    alive: bool = True
    kills: int = 0
    state: str = "InAircraft"
    goal: str = "Drop"
    action: str = "Loot"
    jump_at: float = 0.5
    drop_x: float = 0.0
    drop_y: float = 0.0
    hurt_until: float = -10.0
    heal_timer: float = 0.0
    death_cause: str = ""
    place: int = 0
    survival_s: float = 0.0
    target_x: float = 0.0
    target_y: float = 0.0
    extra: dict = field(default_factory=dict)

    def to_dict(self, now: float) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "isBot": self.is_bot,
            "x": round(self.x),
            "y": round(self.y),
            "z": round(self.z),
            "yaw": round(self.yaw, 1),
            "hp": round(self.hp, 1),
            "maxHp": 100,
            "shield": round(self.shield, 1),
            "maxShield": 100,
            "mats": round(self.mats),
            "hasWeapon": self.has_weapon,
            "hasHeals": self.heals > 0,
            "weaponClose": self.weapon_close,
            "loadout": round(self.loadout, 2),
            "recentlyHurt": now < self.hurt_until,
            "alive": self.alive,
            "dbno": False,
            "kills": self.kills,
            "team": self.team,
            "state": self.state,
            "goal": self.goal,
            "action": self.action,
            "style": self.style,
        }


def expert_policy(a: SimActor, feat: list[float]) -> int:
    """High-skill tactical reference policy used for synthetic expert data & benchmarking."""
    hp = feat[0] * 100.0
    sh = feat[1] * 100.0
    mats = feat[2] * 999.0
    has_weapon = feat[3] > 0.5
    has_heals = feat[4] > 0.5
    enemy_dist = feat[5] * 15000.0
    enemy_dz = feat[6] * 1500.0
    in_storm = feat[8] > 0.5
    dist_next = feat[9] * 60000.0
    storm_time = feat[10] * 120.0
    hurt = feat[12] > 0.5
    loadout = feat[15]

    # 1. Unarmed & threatened -> Escape
    if not has_weapon and enemy_dist < 3200.0 and (hurt or enemy_dist < 1600.0):
        return 4  # Escape
    # 2. In storm or late rotation -> RotateStorm
    if in_storm or (dist_next > 3500.0 and storm_time < max(25.0, dist_next / 520.0)):
        if has_heals and hp < 45.0 and enemy_dist > 3500.0:
            return 2  # BoxAndHeal
        return 3  # RotateStorm
    # 3. Low HP/shield with heals -> BoxAndHeal
    if has_heals and (hp + sh) < 115.0 and (hurt or enemy_dist < 5000.0):
        return 2  # BoxAndHeal
    # 4. Very low HP without heals & close enemy -> Escape
    if (hp + sh) < 45.0 and not has_heals and enemy_dist < 3500.0 and hurt:
        return 4  # Escape
    # 5. Enemy above -> TakeHeight if enough mats
    if has_weapon and enemy_dist < 3200.0 and enemy_dz > 240.0 and mats >= 80.0:
        return 1  # TakeHeight
    # 6. Engage nearby enemy
    if has_weapon and enemy_dist < (5500.0 if loadout >= 0.6 else 3000.0):
        return 0  # Fight
    # 7. Need materials -> Harvest
    if has_weapon and mats < 240.0 and enemy_dist > 4500.0:
        return 6  # Harvest
    # 8. Need better loadout -> Loot
    if loadout < 0.75 or not has_heals:
        return 5  # Loot
    # 9. Rotate early when outside next circle
    if dist_next > 0.0:
        return 3  # RotateStorm
    return 0  # Fight


def basic_heuristic_policy(a: SimActor, feat: list[float]) -> int:
    """Simpler baseline bot policy (represents current rule-based bots before ML)."""
    hp = feat[0] * 100.0
    sh = feat[1] * 100.0
    has_weapon = feat[3] > 0.5
    has_heals = feat[4] > 0.5
    enemy_dist = feat[5] * 15000.0
    in_storm = feat[8] > 0.5
    dist_next = feat[9] * 60000.0
    storm_time = feat[10] * 120.0

    if in_storm or (dist_next > 0.0 and storm_time < 12.0):
        return 3  # RotateStorm (waits late)
    if has_weapon and enemy_dist < 4800.0:
        return 0  # Fight
    if has_heals and (hp + sh) < 60.0 and enemy_dist > 2500.0:
        return 2  # BoxAndHeal
    if not has_weapon or feat[15] < 0.4:
        return 5  # Loot
    if feat[2] * 999.0 < 80.0:
        return 6  # Harvest
    return 5


class MatchSimulator:
    def __init__(
        self,
        num_bots: int = 50,
        policy_mode: str = "expert",
        brain: TacticalBrain | None = None,
        seed: int | None = None,
        opponent_brain: TacticalBrain | None = None,
        heatmap: HeatmapLookup | None = None,
        skill_level: float = 0.85,
    ):
        self.rng = random.Random(seed)
        self.brain = brain
        self.opponent_brain = opponent_brain
        self.heatmap = heatmap if heatmap is not None else HeatmapLookup("server/data/heatmap-s7.json")
        self.skill_level = skill_level
        self.h_state: dict[str, np.ndarray] = {}
        self.prev_dicts: dict[str, dict] = {}
        self.t = 0.0
        self.bus_duration = 16.0
        ang = self.rng.uniform(0, math.tau)
        self.bus_start = (math.cos(ang) * 125000.0, math.sin(ang) * 125000.0)
        self.bus_end = (-self.bus_start[0], -self.bus_start[1])
        self.bus_yaw = math.degrees(math.atan2(self.bus_end[1] - self.bus_start[1], self.bus_end[0] - self.bus_start[0]))

        self.storm = {
            "phase": 1,
            "x": 0.0, "y": 0.0, "radius": 135000.0,
            "nextX": self.rng.uniform(-42000.0, 42000.0),
            "nextY": self.rng.uniform(-42000.0, 42000.0),
            "nextRadius": 68000.0,
        }
        self.storm_hold = 26.0
        self.storm_shrink_rate = 540.0
        self.actors: list[SimActor] = []

        for i in range(num_bots):
            poi = self.rng.choice(S7_POIS)
            if policy_mode == "mixed":
                pol = "ml" if (brain is not None and i % 2 == 0) else "heuristic"
                style = "ml-brain" if pol == "ml" else "baseline"
            elif policy_mode == "ml" and brain is not None:
                pol = "ml"
                style = "ml-brain"
            elif policy_mode == "expert":
                pol = "expert"
                style = "expert"
            else:
                pol = "heuristic"
                style = "baseline"

            is_human_tag = (i == 0 and policy_mode == "expert")
            self.actors.append(SimActor(
                id=f"{'p' if is_human_tag else 'b'}{i}",
                name=f"{'ML_' if pol == 'ml' else 'Bot_'}{i:02d}",
                is_bot=not is_human_tag,
                team=i + 2,
                policy=pol,
                style=style,
                x=self.bus_start[0],
                y=self.bus_start[1],
                yaw=self.bus_yaw,
                jump_at=self.rng.uniform(0.10, 0.85),
                drop_x=poi["x"] + self.rng.uniform(-9000.0, 9000.0),
                drop_y=poi["y"] + self.rng.uniform(-9000.0, 9000.0),
            ))
        self.next_place = len(self.actors)

    def _kill(self, victim: SimActor, killer: SimActor | None, cause: str):
        if not victim.alive:
            return
        victim.alive = False
        victim.hp = 0.0
        victim.state = "Dead"
        victim.death_cause = cause
        victim.place = self.next_place
        victim.survival_s = self.t
        self.next_place -= 1
        if killer is not None:
            killer.kills += 1
            killer.mats = min(999.0, killer.mats + victim.mats * 0.6 + 60.0)
            killer.heals = min(6, killer.heals + max(1, victim.heals))
            killer.loadout = min(1.0, max(killer.loadout, victim.loadout))

    def snapshot(self, source: str = "sim2d", record: bool = False) -> dict:
        in_bus = self.t < self.bus_duration
        alive_count = sum(1 for a in self.actors if a.alive)
        phase = "EndGame" if alive_count <= 1 else ("Aircraft" if in_bus else "SafeZones")
        step = "EndGame" if alive_count <= 1 else ("BusFlying" if in_bus else ("StormHolding" if self.storm_hold > 0 else "StormShrinking"))
        bus = None
        if in_bus:
            p = clamp(self.t / self.bus_duration, 0.0, 1.0)
            bus = {
                "x": round(self.bus_start[0] + (self.bus_end[0] - self.bus_start[0]) * p),
                "y": round(self.bus_start[1] + (self.bus_end[1] - self.bus_start[1]) * p),
                "yaw": round(self.bus_yaw, 1),
                "startX": round(self.bus_start[0]), "startY": round(self.bus_start[1]),
                "endX": round(self.bus_end[0]), "endY": round(self.bus_end[1]),
            }
        time_rem = (self.bus_duration - self.t) if in_bus else (
            self.storm_hold if self.storm_hold > 0 else max(0.0, (self.storm["radius"] - self.storm["nextRadius"]) / self.storm_shrink_rate)
        )
        return {
            "ts": int(time.time() * 1000),
            "source": source,
            "record": record,
            "phase": phase,
            "phaseStep": step,
            "timeRemaining": round(time_rem, 1),
            "playersLeft": alive_count,
            "totalPlayers": len(self.actors),
            "storm": {k: round(v) if k != "phase" else v for k, v in self.storm.items()},
            "bus": bus,
            "actors": [a.to_dict(self.t) for a in self.actors],
        }

    def step(self, dt: float = 0.5) -> bool:
        """Advance simulation by dt seconds. Returns False when match ends."""
        self.t += dt
        in_bus = self.t < self.bus_duration
        bus_prog = clamp(self.t / self.bus_duration, 0.0, 1.0)
        bus_x = self.bus_start[0] + (self.bus_end[0] - self.bus_start[0]) * bus_prog
        bus_y = self.bus_start[1] + (self.bus_end[1] - self.bus_start[1]) * bus_prog

        # Update storm
        if not in_bus:
            if self.storm_hold > 0.0:
                self.storm_hold = max(0.0, self.storm_hold - dt)
            else:
                st = self.storm
                delta_r = self.storm_shrink_rate * dt
                if st["radius"] > st["nextRadius"]:
                    frac = min(1.0, delta_r / max(1.0, st["radius"] - st["nextRadius"]))
                    st["radius"] = max(st["nextRadius"], st["radius"] - delta_r)
                    st["x"] += (st["nextX"] - st["x"]) * frac
                    st["y"] += (st["nextY"] - st["y"]) * frac
                elif st["nextRadius"] > 2000.0:
                    st["phase"] += 1
                    st["nextRadius"] = max(1500.0, st["radius"] * 0.52)
                    shift = (st["radius"] - st["nextRadius"]) * 0.65
                    a = self.rng.uniform(0, math.tau)
                    st["nextX"] = st["x"] + math.cos(a) * shift
                    st["nextY"] = st["y"] + math.sin(a) * shift
                    self.storm_hold = max(6.0, 18.0 - st["phase"] * 2.0)

        alive = [a for a in self.actors if a.alive]
        if len(alive) <= 1 or self.t > 540.0:
            for a in alive:
                a.place = 1
                a.survival_s = self.t
            return False

        snap_view = self.snapshot()

        # Precompute alive actor positions for fast nearest-enemy lookup
        for a in alive:
            if a.state == "InAircraft":
                a.x, a.y, a.yaw = bus_x, bus_y, self.bus_yaw
                if bus_prog >= a.jump_at:
                    a.state = "Skydiving"
                continue
            if a.state == "Skydiving":
                dx, dy = a.drop_x - a.x, a.drop_y - a.y
                d = math.hypot(dx, dy)
                spd = 6500.0 * dt
                a.z = max(400.0, a.z - 2500.0 * dt)
                if d <= spd + 600.0:
                    a.state = "Landed"
                    a.z = 400.0 + self.rng.uniform(0.0, 650.0)
                    a.goal = "Loot"
                else:
                    a.x += (dx / d) * spd
                    a.y += (dy / d) * spd
                    a.yaw = math.degrees(math.atan2(dy, dx))
                continue

            # Storm damage
            if not in_bus and dist2d(a.x, a.y, self.storm["x"], self.storm["y"]) > self.storm["radius"]:
                dps = 1.0 if self.storm["phase"] == 1 else (2.0 if self.storm["phase"] == 2 else min(10.0, self.storm["phase"] * 2.0))
                a.hp -= dps * dt
                a.hurt_until = self.t + 2.0
                if a.hp <= 0.0:
                    self._kill(a, None, "storm")
                    continue

            # Decide tactical action via chosen policy
            a_dict = a.to_dict(self.t)
            mdir_xy = None
            if a.policy == "ml" and self.brain is not None:
                if self.brain.is_v2:
                    h = self.h_state.get(a.id)
                    if h is None:
                        h = self.brain.init_hidden()
                    f26 = extract_features_v2(
                        a_dict, snap_view, self.prev_dicts.get(a.id),
                        dt=dt, skill_level=self.skill_level, heatmap=self.heatmap,
                    )
                    act_idx, mdir_xy, _, next_h = self.brain.step_v2(f26, h)
                    self.h_state[a.id] = next_h
                else:
                    act_idx = self.brain.predict(extract_features(a_dict, snap_view))
            elif a.policy == "heuristic" and self.opponent_brain is not None:
                if self.opponent_brain.is_v2:
                    h = self.h_state.get(a.id)
                    if h is None:
                        h = self.opponent_brain.init_hidden()
                    f26 = extract_features_v2(
                        a_dict, snap_view, self.prev_dicts.get(a.id),
                        dt=dt, skill_level=self.skill_level, heatmap=self.heatmap,
                    )
                    act_idx, mdir_xy, _, next_h = self.opponent_brain.step_v2(f26, h)
                    self.h_state[a.id] = next_h
                else:
                    act_idx = self.opponent_brain.predict(extract_features(a_dict, snap_view))
            elif a.policy == "expert":
                act_idx = expert_policy(a, extract_features(a_dict, snap_view))
            else:
                act_idx = basic_heuristic_policy(a, extract_features(a_dict, snap_view))
            self.prev_dicts[a.id] = a_dict
            a.action = ACTIONS[act_idx]
            a.goal = a.action

            # Find nearest alive enemy
            enemy, edist = None, 1e9
            for o in alive:
                if o is a or not o.alive or o.state != "Landed":
                    continue
                d = dist2d(a.x, a.y, o.x, o.y)
                if d < edist:
                    edist, enemy = d, o

            # Execute action
            spd = 580.0 * dt  # Fortnite sprint speed ~5.8 m/s
            if act_idx == 2:  # BoxAndHeal
                if a.mats >= 40.0:
                    a.mats -= 10.0 * dt
                if a.heals > 0:
                    a.heal_timer += dt
                    if a.heal_timer >= 2.5:
                        a.heal_timer = 0.0
                        a.heals -= 1
                        if a.hp < 90.0:
                            a.hp = min(100.0, a.hp + 45.0)
                        else:
                            a.shield = min(100.0, a.shield + 50.0)
            else:
                a.heal_timer = 0.0

            if act_idx == 1 and enemy is not None:  # TakeHeight
                if a.mats >= 20.0:
                    a.mats -= 20.0 * dt
                    a.z = min(2400.0, a.z + 320.0 * dt)
                dx, dy = enemy.x - a.x, enemy.y - a.y
                if edist > 500.0:
                    a.x += (dx / edist) * spd * 0.75
                    a.y += (dy / edist) * spd * 0.75
                a.yaw = math.degrees(math.atan2(dy, dx))
            elif act_idx == 3:  # RotateStorm
                dx, dy = self.storm["nextX"] - a.x, self.storm["nextY"] - a.y
                d = math.hypot(dx, dy)
                if d > 300.0:
                    ux, uy = dx / d, dy / d
                    if mdir_xy is not None:
                        bx = ux * 0.75 + mdir_xy[0] * 0.25
                        by = uy * 0.75 + mdir_xy[1] * 0.25
                        bn = max(1e-4, math.hypot(bx, by))
                        ux, uy = bx / bn, by / bn
                    a.x += ux * spd
                    a.y += uy * spd
                    a.yaw = math.degrees(math.atan2(uy, ux))
                a.z = max(400.0, a.z - 150.0 * dt)
            elif act_idx == 4 and enemy is not None:  # Escape
                dx, dy = a.x - enemy.x, a.y - enemy.y
                sx, sy = self.storm["nextX"] - a.x, self.storm["nextY"] - a.y
                sd = max(1.0, math.hypot(sx, sy))
                # Blend away from enemy + toward safe zone
                vx = (dx / max(1.0, edist)) * 0.75 + (sx / sd) * 0.25
                vy = (dy / max(1.0, edist)) * 0.75 + (sy / sd) * 0.25
                vn = max(1.0, math.hypot(vx, vy))
                a.x += (vx / vn) * spd * 1.05
                a.y += (vy / vn) * spd * 1.05
                a.yaw = math.degrees(math.atan2(vy, vx))
            elif act_idx == 5:  # Loot
                if dist2d(a.x, a.y, a.target_x, a.target_y) < 1200.0 or (a.target_x == 0.0 and a.target_y == 0.0):
                    poi = min(S7_POIS, key=lambda p: dist2d(a.x, a.y, p["x"], p["y"]) + self.rng.uniform(0, 35000.0))
                    a.target_x = poi["x"] + self.rng.uniform(-6000.0, 6000.0)
                    a.target_y = poi["y"] + self.rng.uniform(-6000.0, 6000.0)
                dx, dy = a.target_x - a.x, a.target_y - a.y
                d = max(1.0, math.hypot(dx, dy))
                a.x += (dx / d) * spd * 0.85
                a.y += (dy / d) * spd * 0.85
                a.yaw = math.degrees(math.atan2(dy, dx))
                if self.rng.random() < 0.22 * dt:
                    a.has_weapon = True
                    a.loadout = min(1.0, a.loadout + self.rng.uniform(0.2, 0.35))
                    a.weapon_close = self.rng.random() < 0.45
                    if self.rng.random() < 0.5:
                        a.heals = min(6, a.heals + 1)
                    if self.rng.random() < 0.4:
                        a.shield = min(100.0, a.shield + 25.0)
            elif act_idx == 6:  # Harvest
                a.mats = min(999.0, a.mats + 55.0 * dt)
            elif act_idx == 0 and enemy is not None:  # Fight
                dx, dy = enemy.x - a.x, enemy.y - a.y
                a.yaw = math.degrees(math.atan2(dy, dx))
                ideal = 650.0 if a.weapon_close else 2200.0
                if edist > ideal + 400.0:
                    a.x += (dx / edist) * spd * 0.85
                    a.y += (dy / edist) * spd * 0.85
                elif edist < ideal - 300.0:
                    a.x -= (dx / edist) * spd * 0.5
                    a.y -= (dy / edist) * spd * 0.5
                # Combat exchange
                if a.has_weapon and edist < 5500.0:
                    height_bonus = 1.28 if (a.z - enemy.z) > 220.0 else (0.82 if (enemy.z - a.z) > 220.0 else 1.0)
                    box_mitigation = 0.45 if (enemy.action == "BoxAndHeal" and enemy.mats > 20.0) else 1.0
                    range_factor = clamp(1.15 - edist / 6500.0, 0.2, 1.0)
                    dps = (18.0 + 24.0 * a.loadout) * height_bonus * box_mitigation * range_factor
                    dmg = dps * dt * self.rng.uniform(0.5, 1.4)
                    if enemy.shield > 0.0:
                        s = min(enemy.shield, dmg)
                        enemy.shield -= s
                        enemy.hp -= (dmg - s)
                    else:
                        enemy.hp -= dmg
                    enemy.hurt_until = self.t + 4.0
                    if enemy.hp <= 0.0:
                        self._kill(enemy, a, "combat")

        return True


def post_telemetry(url: str, payload: dict):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=0.6):
            pass
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser(description="Season 7 2D Tactical Simulator")
    ap.add_argument("--bots", type=int, default=50)
    ap.add_argument("--policy", choices=["expert", "heuristic", "ml", "mixed"], default="expert")
    ap.add_argument("--model", type=str, default=None, help="Path to bot_brain.json")
    ap.add_argument("--live", action="store_true", help="Stream live to Fortnite Offline Server Live Map")
    ap.add_argument("--port", type=int, default=5595)
    ap.add_argument("--speed", type=float, default=2.0, help="Time multiplier for --live mode")
    ap.add_argument("--generate", type=int, default=0, help="Generate N headless matches as JSONL")
    ap.add_argument("--out", type=str, default="ml/data/sim_matches")
    ap.add_argument("--eval", type=int, default=0, help="Run N head-to-head matches (ML vs baseline or --opponent)")
    ap.add_argument("--opponent", type=str, default=None, help="Optional second model JSON to play against in --eval")
    ap.add_argument("--skill", type=float, default=0.85, help="Skill level 0.1..1.0 for V2 GRU models")
    args = ap.parse_args()

    brain = TacticalBrain(args.model) if (args.model and os.path.isfile(args.model)) else None
    opp_brain = TacticalBrain(args.opponent) if (args.opponent and os.path.isfile(args.opponent)) else None

    if args.live:
        url = f"http://127.0.0.1:{args.port}/fortnite/api/game/v2/matchmakingservice/telemetry"
        sim = MatchSimulator(args.bots, args.policy, brain, opponent_brain=opp_brain, skill_level=args.skill)
        print(f"Streaming live 2D match ({args.bots} bots, policy={args.policy}) -> {url}")
        while sim.step(0.5):
            post_telemetry(url, sim.snapshot(source="sim2d-live", record=False))
            time.sleep(0.5 / max(0.2, args.speed))
        post_telemetry(url, sim.snapshot(source="sim2d-live", record=False))
        winner = next((a for a in sim.actors if a.alive), None)
        print(f"Match ended at {sim.t:.0f}s. Winner: {winner.name if winner else 'none'} ({winner.style if winner else '-'}, {winner.kills if winner else 0} kills)")
        return

    if args.generate > 0:
        os.makedirs(args.out, exist_ok=True)
        total_frames = 0
        for m in range(args.generate):
            sim = MatchSimulator(args.bots, args.policy, brain, seed=1000 + m, skill_level=args.skill)
            out_path = os.path.join(args.out, f"sim_{args.policy}_{m:03d}.jsonl")
            with open(out_path, "w", encoding="utf-8") as f:
                while sim.step(1.0):
                    if sim.t >= sim.bus_duration:
                        f.write(json.dumps(sim.snapshot(source=f"sim-{args.policy}", record=False)) + "\n")
                        total_frames += 1
        print(f"Generated {args.generate} matches ({total_frames} frames) in {args.out}")
        return

    if args.eval > 0:
        if brain is None:
            raise SystemExit("ERROR: --eval requires --model <path/to/bot_brain.json>")
        hm = HeatmapLookup("server/data/heatmap-s7.json")
        opp_label = os.path.basename(args.opponent) if opp_brain is not None else "heuristic"
        stats = {"ml": {"wins": 0, "place": [], "kills": [], "storm": 0, "n": 0},
                 "heuristic": {"wins": 0, "place": [], "kills": [], "storm": 0, "n": 0}}
        for m in range(args.eval):
            sim = MatchSimulator(
                args.bots, "mixed", brain, seed=5000 + m,
                opponent_brain=opp_brain, heatmap=hm, skill_level=args.skill,
            )
            while sim.step(1.0):
                pass
            for a in sim.actors:
                s = stats[a.policy]
                s["n"] += 1
                s["place"].append(a.place)
                s["kills"].append(a.kills)
                if a.place == 1:
                    s["wins"] += 1
                if a.death_cause == "storm":
                    s["storm"] += 1
        print(f"=== Head-to-Head Evaluation ({args.eval} matches, {args.bots} bots/match) ===")
        for k, s in stats.items():
            label = os.path.basename(args.model) if k == "ml" else opp_label
            avg_p = sum(s["place"]) / max(1, s["n"])
            avg_k = sum(s["kills"]) / max(1, s["n"])
            storm_pct = 100.0 * s["storm"] / max(1, s["n"])
            print(f"  {label:22s} | Wins: {s['wins']:2d}/{args.eval} | Avg Place: {avg_p:5.1f} | Avg Kills: {avg_k:.2f} | Storm Deaths: {storm_pct:4.1f}%")
        return

    ap.print_help()


if __name__ == "__main__":
    main()
