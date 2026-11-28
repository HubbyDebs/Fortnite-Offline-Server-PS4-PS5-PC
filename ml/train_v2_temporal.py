"""Stage 1 (Salto 1): Train BotBrain V2 — Temporal GRU (26 -> 48 -> 32) + Multi-Head + Skill-Conditioned + Ch1 Heatmap.

Trains on 100% real pre-sliding Fortnite replays (Chapter 1 S6-S10 + Chapter 2 S11-S17):
  - Temporal memory: GRUCell(26 -> 48) over sequences of T=8 steps (O(1) hidden state in C++20!)
  - Skill conditioning: continuous skill_level (0.1..1.0) inferred from each player's match rank
  - Multi-Head outputs:
      1) action_logits (7): Fight, TakeHeight, BoxAndHeal, RotateStorm, Escape, Loot, Harvest
      2) move_dir (2, tanh): learned human rotation/movement direction (dx, dy)
      3) build_prob (1, sigmoid): probability of building / claiming high ground

Exports:
  - ml/models/bot_brain_v2_bc.pt        (PyTorch checkpoint for Stage 2 KL-PPO)
  - ml/models/bot_brain_v2_bc.json      (for ml/sim2d.py)
  - ml/models/CoreAthenaBotBrainV2.h    (pure C++20 header, zero dependencies, ~15 us/bot)
"""
import argparse
import glob
import json
import math
import os
from collections import defaultdict

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from schema import (
    ACTIONS,
    FEATURE_NAMES_V2,
    HeatmapLookup,
    clamp,
    extract_features_v2,
    infer_action_from_transition,
)


class TacticalGRUV2(nn.Module):
    def __init__(self, in_dim: int = 26, hidden: int = 48, fc_dim: int = 32, num_actions: int = 7):
        super().__init__()
        self.in_dim = in_dim
        self.hidden = hidden
        self.fc_dim = fc_dim
        self.num_actions = num_actions
        self.gru = nn.GRU(in_dim, hidden, batch_first=True)
        self.fc = nn.Linear(hidden, fc_dim)
        self.head_action = nn.Linear(fc_dim, num_actions)
        self.head_dir = nn.Linear(fc_dim, 2)
        self.head_build = nn.Linear(fc_dim, 1)

    def forward(self, x: torch.Tensor, h0: torch.Tensor | None = None):
        # x: (B, T, in_dim)
        out, hn = self.gru(x, h0)
        z = F.relu(self.fc(out))
        return self.head_action(z), torch.tanh(self.head_dir(z)), self.head_build(z).squeeze(-1), hn

    def step(self, x_step: torch.Tensor, h: torch.Tensor | None = None):
        # x_step: (B, in_dim)
        act, mdir, bld, hn = self.forward(x_step.unsqueeze(1), h)
        return act.squeeze(1), mdir.squeeze(1), bld.squeeze(1), hn


def export_v2_json(model: TacticalGRUV2, out_path: str, meta: dict):
    sd = model.state_dict()
    payload = {
        "arch": "gru-v2",
        "in_dim": model.in_dim,
        "hidden": model.hidden,
        "fc_dim": model.fc_dim,
        "features": FEATURE_NAMES_V2,
        "actions": ACTIONS,
        **meta,
        "gru_w_ih": sd["gru.weight_ih_l0"].detach().cpu().numpy().round(6).tolist(),
        "gru_w_hh": sd["gru.weight_hh_l0"].detach().cpu().numpy().round(6).tolist(),
        "gru_b_ih": sd["gru.bias_ih_l0"].detach().cpu().numpy().round(6).tolist(),
        "gru_b_hh": sd["gru.bias_hh_l0"].detach().cpu().numpy().round(6).tolist(),
        "fc_w": sd["fc.weight"].detach().cpu().numpy().round(6).tolist(),
        "fc_b": sd["fc.bias"].detach().cpu().numpy().round(6).tolist(),
        "act_w": sd["head_action.weight"].detach().cpu().numpy().round(6).tolist(),
        "act_b": sd["head_action.bias"].detach().cpu().numpy().round(6).tolist(),
        "dir_w": sd["head_dir.weight"].detach().cpu().numpy().round(6).tolist(),
        "dir_b": sd["head_dir.bias"].detach().cpu().numpy().round(6).tolist(),
        "bld_w": sd["head_build.weight"].detach().cpu().numpy().round(6).tolist(),
        "bld_b": sd["head_build.bias"].detach().cpu().numpy().round(6).tolist(),
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f)


def export_v2_cpp_header(model: TacticalGRUV2, out_path: str, meta: dict):
    sd = {k: v.detach().cpu().numpy() for k, v in model.state_dict().items()}
    H = model.hidden
    D = model.in_dim
    FC = model.fc_dim
    A = model.num_actions

    def fmt(arr):
        return ", ".join(f"{float(v):.6f}f" for v in arr.ravel())

    lines = [
        "#pragma once",
        "#include <cmath>",
        "#include <cstdint>",
        "// Auto-generated BotBrain V2 (Temporal GRU + Multi-Head + Skill-Conditioned + Ch1 Heatmap).",
        f"// Stage: {meta.get('stage', 'v2')} | Matches: {meta.get('matches', 0)} | Val Acc: {meta.get('val_accuracy', 0)*100:.2f}%",
        f"// Architecture: GRU({D}->{H}) -> FC({H}->{FC}) -> [Action({A}), MoveDir(2), BuildProb(1)] (~15 us/bot, zero deps).",
        "",
        "namespace CoreBotBrainV2",
        "{",
        f"    inline constexpr int32_t NumFeatures = {D};",
        f"    inline constexpr int32_t NumHidden = {H};",
        f"    inline constexpr int32_t NumFC = {FC};",
        f"    inline constexpr int32_t NumActions = {A};",
        "",
        "    enum class ETacticalAction : int32_t",
        "    {",
        *[f"        {name} = {i}," for i, name in enumerate(ACTIONS)],
        "    };",
        "",
        "    struct FDecision",
        "    {",
        "        ETacticalAction Action = ETacticalAction::Loot;",
        "        float MoveDirX = 0.0f;   // Learned human rotation/flank unit-like direction [-1, 1]",
        "        float MoveDirY = 0.0f;",
        "        float BuildProb = 0.0f;  // Probability [0, 1] of building / taking height now",
        "    };",
        "",
        "    // Feature order: " + ", ".join(FEATURE_NAMES_V2),
        f"    inline constexpr float GruWih[{sd['gru.weight_ih_l0'].size}] = {{ {fmt(sd['gru.weight_ih_l0'])} }};",
        f"    inline constexpr float GruWhh[{sd['gru.weight_hh_l0'].size}] = {{ {fmt(sd['gru.weight_hh_l0'])} }};",
        f"    inline constexpr float GruBih[{sd['gru.bias_ih_l0'].size}] = {{ {fmt(sd['gru.bias_ih_l0'])} }};",
        f"    inline constexpr float GruBhh[{sd['gru.bias_hh_l0'].size}] = {{ {fmt(sd['gru.bias_hh_l0'])} }};",
        f"    inline constexpr float FcW[{sd['fc.weight'].size}] = {{ {fmt(sd['fc.weight'])} }};",
        f"    inline constexpr float FcB[{sd['fc.bias'].size}] = {{ {fmt(sd['fc.bias'])} }};",
        f"    inline constexpr float ActW[{sd['head_action.weight'].size}] = {{ {fmt(sd['head_action.weight'])} }};",
        f"    inline constexpr float ActB[{sd['head_action.bias'].size}] = {{ {fmt(sd['head_action.bias'])} }};",
        f"    inline constexpr float DirW[{sd['head_dir.weight'].size}] = {{ {fmt(sd['head_dir.weight'])} }};",
        f"    inline constexpr float DirB[{sd['head_dir.bias'].size}] = {{ {fmt(sd['head_dir.bias'])} }};",
        f"    inline constexpr float BldW[{sd['head_build.weight'].size}] = {{ {fmt(sd['head_build.weight'])} }};",
        f"    inline constexpr float BldB[{sd['head_build.bias'].size}] = {{ {fmt(sd['head_build.bias'])} }};",
        "",
        "    inline float Sigmoid(float x) { return 1.0f / (1.0f + std::exp(-x)); }",
        "",
        "    // Call once per AI tick (0.5-1.0s). Keep `float Hidden[NumHidden] = {};` per bot controller.",
        "    inline FDecision Step(const float Features[NumFeatures], float Hidden[NumHidden])",
        "    {",
        "        // PyTorch GRU gate order: r (0..H-1), z (H..2H-1), n (2H..3H-1)",
        "        float gi[3 * NumHidden];",
        "        float gh[3 * NumHidden];",
        "        for (int32_t i = 0; i < 3 * NumHidden; ++i)",
        "        {",
        "            float si = GruBih[i];",
        "            const int32_t rowI = i * NumFeatures;",
        "            for (int32_t j = 0; j < NumFeatures; ++j) si += GruWih[rowI + j] * Features[j];",
        "            gi[i] = si;",
        "",
        "            float sh = GruBhh[i];",
        "            const int32_t rowH = i * NumHidden;",
        "            for (int32_t j = 0; j < NumHidden; ++j) sh += GruWhh[rowH + j] * Hidden[j];",
        "            gh[i] = sh;",
        "        }",
        "        float NextH[NumHidden];",
        "        for (int32_t i = 0; i < NumHidden; ++i)",
        "        {",
        "            const float r = Sigmoid(gi[i] + gh[i]);",
        "            const float z = Sigmoid(gi[NumHidden + i] + gh[NumHidden + i]);",
        "            const float n = std::tanh(gi[2 * NumHidden + i] + r * gh[2 * NumHidden + i]);",
        "            NextH[i] = (1.0f - z) * n + z * Hidden[i];",
        "        }",
        "        for (int32_t i = 0; i < NumHidden; ++i) Hidden[i] = NextH[i];",
        "",
        "        float fc[NumFC];",
        "        for (int32_t i = 0; i < NumFC; ++i)",
        "        {",
        "            float s = FcB[i];",
        "            const int32_t row = i * NumHidden;",
        "            for (int32_t j = 0; j < NumHidden; ++j) s += FcW[row + j] * NextH[j];",
        "            fc[i] = s > 0.0f ? s : 0.0f;",
        "        }",
        "",
        "        FDecision out;",
        "        int32_t bestAct = 0;",
        "        float bestScore = -1e30f;",
        "        for (int32_t o = 0; o < NumActions; ++o)",
        "        {",
        "            float s = ActB[o];",
        "            const int32_t row = o * NumFC;",
        "            for (int32_t j = 0; j < NumFC; ++j) s += ActW[row + j] * fc[j];",
        "            if (s > bestScore) { bestScore = s; bestAct = o; }",
        "        }",
        "        out.Action = static_cast<ETacticalAction>(bestAct);",
        "",
        "        float dx = DirB[0], dy = DirB[1];",
        "        for (int32_t j = 0; j < NumFC; ++j)",
        "        {",
        "            dx += DirW[j] * fc[j];",
        "            dy += DirW[NumFC + j] * fc[j];",
        "        }",
        "        out.MoveDirX = std::tanh(dx);",
        "        out.MoveDirY = std::tanh(dy);",
        "",
        "        float b = BldB[0];",
        "        for (int32_t j = 0; j < NumFC; ++j) b += BldW[j] * fc[j];",
        "        out.BuildProb = Sigmoid(b);",
        "        return out;",
        "    }",
        "} // namespace CoreBotBrainV2",
        "",
    ]
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def load_sequence_dataset(
    paths: list[str],
    heatmap: HeatmapLookup,
    seq_len: int = 8,
    stride: int = 4,
    max_seqs: int = 90_000,
    s7_weight: float = 2.0,
):
    valid = [p for p in sorted(paths) if os.path.isfile(p) and os.path.getsize(p) > 1024]
    per_file_cap = max(350, (max_seqs * 2) // max(1, len(valid)))

    all_x, all_act, all_dir, all_dmask, all_bld, all_w = [], [], [], [], [], []
    files_used = 0

    for path in valid:
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
        if len(frames) < seq_len + 1:
            continue
        files_used += 1

        # Pass 1: estimate each player's skill percentile inside this match (kills + survival + height gains)
        p_kills = defaultdict(int)
        p_frames = defaultdict(int)
        p_zgains = defaultdict(int)
        prev_z = {}
        for snap in frames:
            for a in snap.get("actors", ()):
                if not a.get("alive", True) or a.get("state") in ("InAircraft", "Skydiving", "Warmup"):
                    continue
                aid = a.get("id") or a.get("name")
                if not aid:
                    continue
                p_frames[aid] += 1
                p_kills[aid] = max(p_kills[aid], int(a.get("kills", 0) or 0))
                z = float(a.get("z", 0.0))
                if aid in prev_z and z - prev_z[aid] > 200.0:
                    p_zgains[aid] += 1
                prev_z[aid] = z

        scored = sorted(
            p_frames.keys(),
            key=lambda aid: p_kills[aid] * 120.0 + p_frames[aid] * 3.0 + p_zgains[aid] * 15.0,
        )
        n_sc = max(1, len(scored) - 1)
        skill_map = {aid: clamp(0.15 + 0.85 * (idx / n_sc), 0.1, 1.0) for idx, aid in enumerate(scored)}

        # Pass 2: build per-player trajectories
        tracks = defaultdict(list)  # aid -> list of (feat26, action, dir2, dmask, build, weight)
        for i in range(len(frames) - 1):
            cur_snap, nxt_snap = frames[i], frames[i + 1]
            if cur_snap.get("phase") not in ("SafeZones", "Aircraft", "EndGame"):
                continue
            prev_snap = frames[i - 1] if i > 0 else None
            dt_prev = max(0.5, min(15.0, (cur_snap.get("ts", 0) - prev_snap.get("ts", 0)) / 1000.0)) if prev_snap else 1.0
            dt_nxt = max(0.5, min(15.0, (nxt_snap.get("ts", 0) - cur_snap.get("ts", 0)) / 1000.0))

            nxt_map = {(a.get("id") or a.get("name")): a for a in nxt_snap.get("actors", ())}
            prev_map = {(a.get("id") or a.get("name")): a for a in (prev_snap.get("actors", ()) if prev_snap else ())}
            src = str(cur_snap.get("source", ""))
            w_base = (s7_weight * 1.5) if src.startswith("replay-s3") else (s7_weight if (src.startswith("replay-s7") or src.startswith("replay-ch1")) else 1.0)

            for a in cur_snap.get("actors", ()):
                if not a.get("alive", True) or a.get("state") in ("InAircraft", "Skydiving", "Warmup"):
                    continue
                aid = a.get("id") or a.get("name")
                nxt = nxt_map.get(aid)
                if not nxt:
                    continue
                sk = skill_map.get(aid, 0.5)
                feat = extract_features_v2(a, cur_snap, prev_map.get(aid), dt=dt_prev, skill_level=sk, heatmap=heatmap)
                label = infer_action_from_transition(a, nxt, cur_snap)

                dx = float(nxt.get("x", 0.0)) - float(a.get("x", 0.0))
                dy = float(nxt.get("y", 0.0)) - float(a.get("y", 0.0))
                dz = float(nxt.get("z", 0.0)) - float(a.get("z", 0.0))
                dist = math.hypot(dx, dy)
                if dist > 80.0 * dt_nxt and dist < 850.0 * dt_nxt:
                    dir2 = (dx / dist, dy / dist)
                    dmask = 1.0
                else:
                    dir2 = (0.0, 0.0)
                    dmask = 0.0
                bld = 1.0 if (label == 1 or dz > 180.0) else 0.0
                tracks[aid].append((feat, label, dir2, dmask, bld, w_base))

        # Slice windows of length seq_len
        file_seqs = []
        for aid, tr in tracks.items():
            if len(tr) < seq_len:
                continue
            for s in range(0, len(tr) - seq_len + 1, stride):
                win = tr[s : s + seq_len]
                file_seqs.append(win)

        if not file_seqs:
            continue
        if len(file_seqs) > per_file_cap:
            rng_f = np.random.default_rng(abs(hash(os.path.basename(path))) % (2**32))
            idxs = rng_f.choice(len(file_seqs), size=per_file_cap, replace=False)
            file_seqs = [file_seqs[k] for k in idxs]

        for win in file_seqs:
            all_x.append([t[0] for t in win])
            all_act.append([t[1] for t in win])
            all_dir.append([t[2] for t in win])
            all_dmask.append([t[3] for t in win])
            all_bld.append([t[4] for t in win])
            all_w.append(win[-1][5])

    if not all_x:
        return None
    X = np.array(all_x, dtype=np.float32)
    A = np.array(all_act, dtype=np.int64)
    D = np.array(all_dir, dtype=np.float32)
    DM = np.array(all_dmask, dtype=np.float32)
    B = np.array(all_bld, dtype=np.float32)
    W = np.array(all_w, dtype=np.float32)

    if len(X) > max_seqs:
        rng = np.random.default_rng(42)
        keep = rng.choice(len(X), size=max_seqs, replace=False)
        X, A, D, DM, B, W = X[keep], A[keep], D[keep], DM[keep], B[keep], W[keep]
    return X, A, D, DM, B, W, files_used


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", nargs="+", default=[
        "ml/data/replays_ch1_jsonl/*.jsonl",
        "ml/data/replays_jsonl/ch2_*.jsonl",
        "ml/data/replays_jsonl/legitatx__season11-*.jsonl",
        "ml/data/replays_jsonl/ThisNils__1.jsonl",
        "ml/data/replays_jsonl/ThisNils__client-11.00.jsonl",
        "ml/data/replays_jsonl/xNocken__*.jsonl",
    ])
    ap.add_argument("--heatmap", default="server/data/heatmap-s7.json")
    ap.add_argument("--seq-len", type=int, default=8)
    ap.add_argument("--stride", type=int, default=3)
    ap.add_argument("--max-seqs", type=int, default=85_000)
    ap.add_argument("--epochs", type=int, default=28)
    ap.add_argument("--batch-size", type=int, default=256)
    ap.add_argument("--lr", type=float, default=2.5e-3)
    ap.add_argument("--out-dir", default="ml/models")
    args = ap.parse_args()

    heatmap = HeatmapLookup(args.heatmap)
    paths = sorted(set(p for pat in args.data for p in glob.glob(pat)))
    ds = load_sequence_dataset(paths, heatmap, seq_len=args.seq_len, stride=args.stride, max_seqs=args.max_seqs)
    if ds is None:
        raise SystemExit("No sequences loaded.")
    X, A, D, DM, B, W, n_files = ds
    print(f"Loaded {len(X)} sequences (T={args.seq_len}, total steps={len(X)*args.seq_len}) from {n_files} real matches.")

    counts = np.bincount(A.ravel(), minlength=len(ACTIONS))
    print("Action counts:", ", ".join(f"{ACTIONS[i]}={counts[i]}" for i in range(len(ACTIONS))))

    cls_w = np.ones(len(ACTIONS), dtype=np.float32)
    total_steps = float(A.size)
    for c in range(len(ACTIONS)):
        if counts[c] > 0:
            cls_w[c] = (total_steps / (len(ACTIONS) * counts[c])) ** 0.5
    cls_w /= cls_w.mean()

    rng = np.random.default_rng(42)
    idx = rng.permutation(len(X))
    split = int(len(X) * 0.85)
    tr, va = idx[:split], idx[split:]

    torch.manual_seed(42)
    model = TacticalGRUV2(in_dim=X.shape[2], hidden=48, fc_dim=32, num_actions=len(ACTIONS))
    n_params = sum(p.numel() for p in model.parameters())
    print(f"Model TacticalGRUV2: {n_params:,} parameters")

    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=args.epochs, eta_min=args.lr * 0.05)
    act_loss_fn = nn.CrossEntropyLoss(weight=torch.tensor(cls_w), reduction="none")

    X_tr, A_tr, D_tr, DM_tr, B_tr, W_tr = (
        torch.tensor(X[tr]), torch.tensor(A[tr]), torch.tensor(D[tr]),
        torch.tensor(DM[tr]), torch.tensor(B[tr]), torch.tensor(W[tr]),
    )
    X_va, A_va, D_va, DM_va, B_va = (
        torch.tensor(X[va]), torch.tensor(A[va]), torch.tensor(D[va]),
        torch.tensor(DM[va]), torch.tensor(B[va]),
    )

    best_acc = -1.0
    best_state = None
    best_dir_cos = 0.0

    for ep in range(1, args.epochs + 1):
        model.train()
        perm = torch.randperm(len(X_tr))
        tot_loss = 0.0
        for s in range(0, len(X_tr), args.batch_size):
            b = perm[s : s + args.batch_size]
            opt.zero_grad()
            act_log, pred_dir, pred_bld, _ = model(X_tr[b])
            # Action loss across all steps in window (weighted by sequence weight)
            al = act_loss_fn(act_log.reshape(-1, len(ACTIONS)), A_tr[b].reshape(-1)).reshape(len(b), -1).mean(dim=1)
            al = (al * W_tr[b]).mean()
            # Direction loss (MSE on moving steps)
            dmask = DM_tr[b].unsqueeze(-1)
            dl = (((pred_dir - D_tr[b]) ** 2) * dmask).sum() / (dmask.sum() * 2.0 + 1e-6)
            # Build/height probability loss
            bl = F.binary_cross_entropy_with_logits(pred_bld, B_tr[b])
            loss = al + 0.5 * dl + 0.3 * bl
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 2.0)
            opt.step()
            tot_loss += float(loss.item()) * len(b)
        sched.step()

        model.eval()
        with torch.no_grad():
            va_act, va_dir, va_bld, _ = model(X_va)
            # Evaluate on final step of each sequence (with full temporal context!)
            pred_last = va_act[:, -1].argmax(dim=1)
            true_last = A_va[:, -1]
            acc = float((pred_last == true_last).float().mean().item())
            # Direction cosine similarity on moving validation steps
            m = DM_va[:, -1] > 0.5
            if m.any():
                cos = F.cosine_similarity(va_dir[:, -1][m], D_va[:, -1][m], dim=-1).mean().item()
            else:
                cos = 0.0
        if acc > best_acc:
            best_acc = acc
            best_dir_cos = cos
            best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
        if ep in (1, 5, 10, 15, 20, 25, args.epochs):
            print(f"Epoch {ep:2d}/{args.epochs} | loss={tot_loss/len(X_tr):.4f} | val_acc={acc*100:.2f}% (best={best_acc*100:.2f}%) | dir_cos={cos:.3f}")

    if best_state is not None:
        model.load_state_dict(best_state)

    model.eval()
    with torch.no_grad():
        va_act, va_dir, va_bld, _ = model(X_va)
        pred_last = va_act[:, -1].argmax(dim=1).numpy()
        true_last = A_va[:, -1].numpy()
        val_acc = float((pred_last == true_last).mean())
    per_cls = []
    for c, name in enumerate(ACTIONS):
        mask = (true_last == c)
        if mask.sum() > 0:
            per_cls.append(f"{name}={(pred_last[mask]==c).mean()*100:.1f}%({int(mask.sum())})")
    print("V2 BC Per-action val accuracy:", ", ".join(per_cls))

    os.makedirs(args.out_dir, exist_ok=True)
    meta = {
        "stage": "v2-temporal-bc",
        "matches": int(n_files),
        "sequences": int(len(X)),
        "val_accuracy": round(val_acc, 4),
        "dir_cosine": round(best_dir_cos, 4),
    }
    pt_path = os.path.join(args.out_dir, "bot_brain_v2_bc.pt")
    torch.save({"state_dict": model.state_dict(), "meta": meta}, pt_path)
    json_path = os.path.join(args.out_dir, "bot_brain_v2_bc.json")
    export_v2_json(model, json_path, meta)
    cpp_path = os.path.join(args.out_dir, "CoreAthenaBotBrainV2.h")
    export_v2_cpp_header(model, cpp_path, meta)
    print(f"Exported Stage 1 V2:\n  - {pt_path}\n  - {json_path}\n  - {cpp_path}")


if __name__ == "__main__":
    main()
