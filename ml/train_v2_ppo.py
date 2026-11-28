"""Stage 2 (Salto 2): KL-Anchored PPO Fine-Tuning of BotBrain V2 (Temporal GRU + Heatmap + Skill + Multi-Head).

Starts from the Stage 1 human-pretrained GRU (ml/models/bot_brain_v2_bc.pt) and runs PPO self-play
in Season 7 sim2d against a tough opponent pool (V1 MLP + Heuristic + Self-Play), while keeping a
KL-divergence anchor to the frozen human-pretrained policy so Chapter 1 human tactics are preserved
while mastering the .replay blind spots (BoxAndHeal, Harvest, Escape at low HP, endgame survival).

Exports:
  - ml/models/bot_brain_v2.json
  - ml/models/CoreAthenaBotBrainV2.h
"""
import argparse
import copy
import math
import os
import random

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.distributions import Categorical

from schema import ACTIONS, HeatmapLookup, clamp, dist2d, extract_features, extract_features_v2
from sim2d import MatchSimulator, TacticalBrain, basic_heuristic_policy, expert_policy
from train_v2_temporal import TacticalGRUV2, export_v2_cpp_header, export_v2_json


class ActorCriticV2(nn.Module):
    def __init__(self, actor: TacticalGRUV2):
        super().__init__()
        self.actor = actor
        self.critic = nn.Sequential(
            nn.Linear(actor.hidden, 32),
            nn.ReLU(),
            nn.Linear(32, 1),
        )

    def step(self, x: torch.Tensor, h: torch.Tensor | None):
        out, hn = self.actor.gru(x.unsqueeze(1), h)
        h_out = out.squeeze(1)
        z = F.relu(self.actor.fc(h_out))
        logits = self.actor.head_action(z)
        mdir = torch.tanh(self.actor.head_dir(z))
        bld = torch.sigmoid(self.actor.head_build(z).squeeze(-1))
        val = self.critic(h_out).squeeze(-1)
        return logits, mdir, bld, val, hn


def collect_rollout_matches(
    ac: ActorCriticV2,
    ref_actor: TacticalGRUV2,
    v1_brain: TacticalBrain | None,
    heatmap: HeatmapLookup,
    n_matches: int,
    num_bots: int,
    seed_base: int,
    gamma: float = 0.985,
    gae_lambda: float = 0.95,
):
    """Run n_matches in sim2d where half the bots are learning V2 PPO agents and half are opponents."""
    ac.eval()
    ref_actor.eval()

    buf_obs, buf_h, buf_act, buf_logp, buf_ref_logits, buf_adv, buf_ret = [], [], [], [], [], [], []
    stats = {"v2_wins": 0, "opp_wins": 0, "v2_kills": [], "v2_storm": 0, "v2_n": 0, "act_counts": np.zeros(len(ACTIONS), dtype=int)}

    for m in range(n_matches):
        sim = MatchSimulator(num_bots=num_bots, policy_mode="heuristic", brain=None, seed=seed_base + m)
        # Assign half learning V2 agents, half mixed tough opponents (V1 ML + Heuristic + Expert)
        v2_ids = set()
        opp_type = {}
        skill_by_id = {}
        for idx, a in enumerate(sim.actors):
            if idx % 2 == 0:
                v2_ids.add(a.id)
                a.policy = "v2_ppo"
                skill_by_id[a.id] = random.choice([0.3, 0.5, 0.7, 0.9, 1.0])
            else:
                if v1_brain is not None and idx % 4 != 3:
                    opp_type[a.id] = "v1"
                else:
                    opp_type[a.id] = "expert"

        # Per-agent rollout memory
        h_cur = {aid: torch.zeros(1, 1, ac.actor.hidden) for aid in v2_ids}
        h_ref = {aid: torch.zeros(1, 1, ref_actor.hidden) for aid in v2_ids}
        prev_dict = {}
        trajs = {aid: {"obs": [], "h": [], "act": [], "logp": [], "ref": [], "rew": [], "val": []} for aid in v2_ids}
        prev_state = {aid: {"kills": 0, "hp": 100.0, "shield": 0.0, "mats": 30.0, "z": 400.0} for aid in v2_ids}

        dt = 1.0
        while True:
            sim.t += dt
            in_bus = sim.t < sim.bus_duration
            bus_prog = clamp(sim.t / sim.bus_duration, 0.0, 1.0)
            bus_x = sim.bus_start[0] + (sim.bus_end[0] - sim.bus_start[0]) * bus_prog
            bus_y = sim.bus_start[1] + (sim.bus_end[1] - sim.bus_start[1]) * bus_prog

            if not in_bus:
                if sim.storm_hold > 0.0:
                    sim.storm_hold = max(0.0, sim.storm_hold - dt)
                else:
                    st = sim.storm
                    delta_r = sim.storm_shrink_rate * dt
                    if st["radius"] > st["nextRadius"]:
                        frac = min(1.0, delta_r / max(1.0, st["radius"] - st["nextRadius"]))
                        st["radius"] = max(st["nextRadius"], st["radius"] - delta_r)
                        st["x"] += (st["nextX"] - st["x"]) * frac
                        st["y"] += (st["nextY"] - st["y"]) * frac
                    elif st["nextRadius"] > 2000.0:
                        st["phase"] += 1
                        st["nextRadius"] = max(1500.0, st["radius"] * 0.52)
                        shift = (st["radius"] - st["nextRadius"]) * 0.65
                        ang = sim.rng.uniform(0, math.tau)
                        st["nextX"] = st["x"] + math.cos(ang) * shift
                        st["nextY"] = st["y"] + math.sin(ang) * shift
                        sim.storm_hold = max(6.0, 18.0 - st["phase"] * 2.0)

            alive = [a for a in sim.actors if a.alive]
            if len(alive) <= 1 or sim.t > 540.0:
                for a in alive:
                    a.place = 1
                    a.survival_s = sim.t
                    if a.id in trajs and trajs[a.id]["rew"]:
                        trajs[a.id]["rew"][-1] += 2.5
                break

            snap_view = sim.snapshot()
            cur_dicts = {a.id: a.to_dict(sim.t) for a in alive}

            for a in alive:
                if a.state == "InAircraft":
                    a.x, a.y, a.yaw = bus_x, bus_y, sim.bus_yaw
                    if bus_prog >= a.jump_at:
                        a.state = "Skydiving"
                    continue
                if a.state == "Skydiving":
                    dx, dy = a.drop_x - a.x, a.drop_y - a.y
                    d = math.hypot(dx, dy)
                    spd_drop = 6500.0 * dt
                    a.z = max(400.0, a.z - 2500.0 * dt)
                    if d <= spd_drop + 600.0:
                        a.state = "Landed"
                        a.z = 400.0 + sim.rng.uniform(0.0, 650.0)
                        a.goal = "Loot"
                    else:
                        a.x += (dx / d) * spd_drop
                        a.y += (dy / d) * spd_drop
                    continue

                in_storm = (not in_bus) and (dist2d(a.x, a.y, sim.storm["x"], sim.storm["y"]) > sim.storm["radius"])
                if in_storm:
                    dps = 1.0 if sim.storm["phase"] == 1 else (2.0 if sim.storm["phase"] == 2 else min(10.0, sim.storm["phase"] * 2.0))
                    a.hp -= dps * dt
                    a.hurt_until = sim.t + 2.0
                    if a.hp <= 0.0:
                        sim._kill(a, None, "storm")
                        if a.id in trajs and trajs[a.id]["rew"]:
                            trajs[a.id]["rew"][-1] -= 1.8
                        continue

                mdir_xy = None
                if a.id in v2_ids:
                    feat26 = extract_features_v2(
                        cur_dicts[a.id], snap_view, prev_dict.get(a.id),
                        dt=dt, skill_level=skill_by_id[a.id], heatmap=heatmap,
                    )
                    obs_t = torch.tensor(feat26, dtype=torch.float32).unsqueeze(0)
                    h_in = h_cur[a.id].detach()
                    with torch.no_grad():
                        logits, mdir, _, val, h_next = ac.step(obs_t, h_in)
                        ref_logits, _, _, h_ref_next = ref_actor.step(obs_t, h_ref[a.id].detach())
                        dist = Categorical(logits=logits)
                        act_t = dist.sample()
                        logp = dist.log_prob(act_t)
                    h_cur[a.id] = h_next
                    h_ref[a.id] = h_ref_next
                    act_idx = int(act_t.item())
                    mdir_xy = (float(mdir[0, 0].item()), float(mdir[0, 1].item()))
                    stats["act_counts"][act_idx] += 1

                    # Step reward from previous transition
                    ps = prev_state[a.id]
                    r = 0.015 if not in_storm else -0.05
                    if a.kills > ps["kills"]:
                        r += 1.6 * (a.kills - ps["kills"])
                    if (a.hp + a.shield) > (ps["hp"] + ps["shield"]) + 10.0:
                        r += 0.45  # rewarded healing when needed
                    if act_idx == 2 and (a.heals == 0 or (a.hp + a.shield) >= 155.0):
                        r -= 0.08  # discourage freezing in BoxAndHeal with no heals or full HP
                    if ps["mats"] < 140.0 and a.mats > ps["mats"] + 25.0:
                        r += 0.14  # rewarded harvesting when low mats
                    if act_idx == 1 and a.z > ps["z"] + 150.0:
                        r += 0.18 * skill_by_id[a.id]  # higher-skill bots rewarded more for height
                    ps["kills"] = a.kills
                    ps["hp"] = a.hp
                    ps["shield"] = a.shield
                    ps["mats"] = a.mats
                    ps["z"] = a.z

                    tr = trajs[a.id]
                    if tr["obs"]:
                        tr["rew"].append(r)
                    tr["obs"].append(feat26)
                    tr["h"].append(h_in.squeeze(0).squeeze(0).numpy())
                    tr["act"].append(act_idx)
                    tr["logp"].append(float(logp.item()))
                    tr["ref"].append(ref_logits.squeeze(0).numpy())
                    tr["val"].append(float(val.item()))
                else:
                    feat16 = extract_features(cur_dicts[a.id], snap_view)
                    ot = opp_type[a.id]
                    if ot == "v1" and v1_brain is not None:
                        act_idx = v1_brain.predict(feat16)
                    elif ot == "expert":
                        act_idx = expert_policy(a, feat16)
                    else:
                        act_idx = basic_heuristic_policy(a, feat16)

                a.action = ACTIONS[act_idx]
                a.goal = a.action

                enemy, edist = None, 1e9
                for o in alive:
                    if o is a or not o.alive or o.state != "Landed":
                        continue
                    d = dist2d(a.x, a.y, o.x, o.y)
                    if d < edist:
                        edist, enemy = d, o

                spd = 580.0 * dt
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
                elif act_idx == 3:  # RotateStorm (blend human learned direction head!)
                    dx, dy = sim.storm["nextX"] - a.x, sim.storm["nextY"] - a.y
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
                    a.z = max(400.0, a.z - 150.0 * dt)
                elif act_idx == 4 and enemy is not None:  # Escape
                    dx, dy = a.x - enemy.x, a.y - enemy.y
                    sx, sy = sim.storm["nextX"] - a.x, sim.storm["nextY"] - a.y
                    sd = max(1.0, math.hypot(sx, sy))
                    vx = (dx / max(1.0, edist)) * 0.75 + (sx / sd) * 0.25
                    vy = (dy / max(1.0, edist)) * 0.75 + (sy / sd) * 0.25
                    vn = max(1.0, math.hypot(vx, vy))
                    a.x += (vx / vn) * spd * 1.05
                    a.y += (vy / vn) * spd * 1.05
                elif act_idx == 5:  # Loot
                    if dist2d(a.x, a.y, a.target_x, a.target_y) < 1200.0 or (a.target_x == 0.0 and a.target_y == 0.0):
                        from schema import S7_POIS
                        poi = min(S7_POIS, key=lambda p: dist2d(a.x, a.y, p["x"], p["y"]) + sim.rng.uniform(0, 35000.0))
                        a.target_x = poi["x"] + sim.rng.uniform(-6000.0, 6000.0)
                        a.target_y = poi["y"] + sim.rng.uniform(-6000.0, 6000.0)
                    dx, dy = a.target_x - a.x, a.target_y - a.y
                    d = max(1.0, math.hypot(dx, dy))
                    a.x += (dx / d) * spd * 0.85
                    a.y += (dy / d) * spd * 0.85
                    if sim.rng.random() < 0.22 * dt:
                        a.has_weapon = True
                        a.loadout = min(1.0, a.loadout + sim.rng.uniform(0.2, 0.35))
                        a.weapon_close = sim.rng.random() < 0.45
                        if sim.rng.random() < 0.5:
                            a.heals = min(6, a.heals + 1)
                        if sim.rng.random() < 0.4:
                            a.shield = min(100.0, a.shield + 25.0)
                elif act_idx == 6:  # Harvest
                    a.mats = min(999.0, a.mats + 55.0 * dt)
                elif act_idx == 0 and enemy is not None:  # Fight
                    dx, dy = enemy.x - a.x, enemy.y - a.y
                    ideal = 650.0 if a.weapon_close else 2200.0
                    if edist > ideal + 400.0:
                        a.x += (dx / edist) * spd * 0.85
                        a.y += (dy / edist) * spd * 0.85
                    elif edist < ideal - 300.0:
                        a.x -= (dx / edist) * spd * 0.5
                        a.y -= (dy / edist) * spd * 0.5
                    if a.has_weapon and edist < 5500.0:
                        height_bonus = 1.28 if (a.z - enemy.z) > 220.0 else (0.82 if (enemy.z - a.z) > 220.0 else 1.0)
                        box_mitigation = 0.45 if (enemy.action == "BoxAndHeal" and enemy.mats > 20.0) else 1.0
                        range_factor = clamp(1.15 - edist / 6500.0, 0.2, 1.0)
                        dps = (18.0 + 24.0 * a.loadout) * height_bonus * box_mitigation * range_factor
                        dmg = dps * dt * sim.rng.uniform(0.5, 1.4)
                        if a.id in trajs and trajs[a.id]["rew"]:
                            trajs[a.id]["rew"][-1] += 0.008 * dmg
                        if enemy.shield > 0.0:
                            s = min(enemy.shield, dmg)
                            enemy.shield -= s
                            enemy.hp -= (dmg - s)
                        else:
                            enemy.hp -= dmg
                        enemy.hurt_until = sim.t + 4.0
                        if enemy.hp <= 0.0:
                            sim._kill(enemy, a, "combat")
                            if enemy.id in trajs and trajs[enemy.id]["rew"]:
                                trajs[enemy.id]["rew"][-1] -= 0.6

            prev_dict = cur_dicts

        # Finalize trajectories & GAE advantages
        for a in sim.actors:
            if a.id not in v2_ids:
                if a.place == 1:
                    stats["opp_wins"] += 1
                continue
            stats["v2_n"] += 1
            stats["v2_kills"].append(a.kills)
            if a.place == 1:
                stats["v2_wins"] += 1
            if a.death_cause == "storm":
                stats["v2_storm"] += 1

            tr = trajs[a.id]
            T = len(tr["obs"])
            if T < 2:
                continue
            # Terminal reward for final recorded step
            place_bonus = max(0.0, (num_bots - a.place) / float(num_bots)) * 1.2
            tr["rew"].append(place_bonus)
            rews = np.array(tr["rew"][:T], dtype=np.float32)
            vals = np.array(tr["val"], dtype=np.float32)
            adv = np.zeros(T, dtype=np.float32)
            last_gae = 0.0
            for t_i in reversed(range(T)):
                next_v = vals[t_i + 1] if t_i + 1 < T else 0.0
                delta = rews[t_i] + gamma * next_v - vals[t_i]
                last_gae = delta + gamma * gae_lambda * last_gae
                adv[t_i] = last_gae
            ret = adv + vals

            buf_obs.extend(tr["obs"])
            buf_h.extend(tr["h"])
            buf_act.extend(tr["act"])
            buf_logp.extend(tr["logp"])
            buf_ref_logits.extend(tr["ref"])
            buf_adv.extend(adv.tolist())
            buf_ret.extend(ret.tolist())

    return (
        torch.tensor(np.array(buf_obs, dtype=np.float32)),
        torch.tensor(np.array(buf_h, dtype=np.float32)),
        torch.tensor(np.array(buf_act, dtype=np.int64)),
        torch.tensor(np.array(buf_logp, dtype=np.float32)),
        torch.tensor(np.array(buf_ref_logits, dtype=np.float32)),
        torch.tensor(np.array(buf_adv, dtype=np.float32)),
        torch.tensor(np.array(buf_ret, dtype=np.float32)),
        stats,
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", default="ml/models/bot_brain_v2_bc.pt")
    ap.add_argument("--v1-model", default="ml/models/bot_brain.json")
    ap.add_argument("--heatmap", default="server/data/heatmap-s7.json")
    ap.add_argument("--updates", type=int, default=16)
    ap.add_argument("--matches-per-update", type=int, default=12)
    ap.add_argument("--bots", type=int, default=28)
    ap.add_argument("--kl-beta", type=float, default=0.06)
    ap.add_argument("--lr", type=float, default=4e-4)
    ap.add_argument("--out-dir", default="ml/models")
    args = ap.parse_args()

    ckpt = torch.load(args.init, map_location="cpu", weights_only=False)
    actor = TacticalGRUV2()
    actor.load_state_dict(ckpt["state_dict"])
    ref_actor = copy.deepcopy(actor)
    for p in ref_actor.parameters():
        p.requires_grad_(False)

    ac = ActorCriticV2(actor)
    v1_brain = TacticalBrain(args.v1_model) if os.path.isfile(args.v1_model) else None
    heatmap = HeatmapLookup(args.heatmap)
    opt = torch.optim.AdamW(ac.parameters(), lr=args.lr, weight_decay=1e-4)

    best_winrate = -1.0
    best_actor_state = copy.deepcopy(ac.actor.state_dict())
    total_matches = 0

    print(f"Starting KL-Anchored PPO (beta={args.kl_beta}) for {args.updates} updates x {args.matches_per_update} matches...")
    for u in range(1, args.updates + 1):
        obs, h_in, act, old_logp, ref_logits, adv, ret, st = collect_rollout_matches(
            ac, ref_actor, v1_brain, heatmap,
            n_matches=args.matches_per_update, num_bots=args.bots, seed_base=9000 + u * 100,
        )
        total_matches += args.matches_per_update
        adv = (adv - adv.mean()) / (adv.std() + 1e-6)

        ac.train()
        N = len(obs)
        bs = 512
        kl_val = 0.0
        for _ in range(3):
            perm = torch.randperm(N)
            for s in range(0, N, bs):
                b = perm[s : s + bs]
                logits, _, _, val, _ = ac.step(obs[b], h_in[b].unsqueeze(0))
                dist = Categorical(logits=logits)
                new_logp = dist.log_prob(act[b])
                ratio = torch.exp(new_logp - old_logp[b])
                s1 = ratio * adv[b]
                s2 = torch.clamp(ratio, 0.8, 1.2) * adv[b]
                pi_loss = -torch.min(s1, s2).mean()
                v_loss = F.mse_loss(val, ret[b])
                ent = dist.entropy().mean()
                # KL(pi || pi_human_pretrained) anchor
                log_p = F.log_softmax(logits, dim=-1)
                log_q = F.log_softmax(ref_logits[b], dim=-1)
                kl = (torch.exp(log_p) * (log_p - log_q)).sum(dim=-1).mean()
                kl_val = float(kl.item())
                loss = pi_loss + 0.5 * v_loss - 0.015 * ent + args.kl_beta * kl
                opt.zero_grad()
                loss.backward()
                torch.nn.utils.clip_grad_norm_(ac.parameters(), 1.0)
                opt.step()

        wr = st["v2_wins"] / max(1, st["v2_wins"] + st["opp_wins"])
        storm_pct = 100.0 * st["v2_storm"] / max(1, st["v2_n"])
        avg_k = float(np.mean(st["v2_kills"])) if st["v2_kills"] else 0.0
        if wr >= best_winrate:
            best_winrate = wr
            best_actor_state = copy.deepcopy(ac.actor.state_dict())
        act_str = " ".join(f"{ACTIONS[i][:3]}={st['act_counts'][i]}" for i in range(len(ACTIONS)))
        print(f"Upd {u:2d}/{args.updates} | steps={N:5d} | V2 wins={st['v2_wins']:2d}/{args.matches_per_update} ({wr*100:4.0f}%) | kills={avg_k:.2f} | storm={storm_pct:4.1f}% | KL={kl_val:.3f} | {act_str}")

    ac.actor.load_state_dict(best_actor_state)
    meta = {
        **ckpt.get("meta", {}),
        "stage": "v2-temporal-bc+kl-ppo",
        "ppo_matches": total_matches,
        "kl_beta": args.kl_beta,
    }
    json_path = os.path.join(args.out_dir, "bot_brain_v2.json")
    export_v2_json(ac.actor, json_path, meta)
    cpp_path = os.path.join(args.out_dir, "CoreAthenaBotBrainV2.h")
    export_v2_cpp_header(ac.actor, cpp_path, meta)
    print(f"\nExported Final V2 (BC + KL-PPO):\n  - {json_path}\n  - {cpp_path}")


if __name__ == "__main__":
    main()
