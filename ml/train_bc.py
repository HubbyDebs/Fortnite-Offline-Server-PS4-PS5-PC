"""Behavioral Cloning trainer for Fortnite 7.40 bots (PyTorch 16 -> 32 -> 7 MLP).

Reads unified JSONL matches from:
  - server/data/matches/*.jsonl   (real PS4/PC/CoreV2 telemetry, weighted higher!)
  - ml/data/replays_jsonl/*.jsonl (extracted from real Fortnite .replay files, weighted higher!)
  - ml/data/sim_matches/*.jsonl   (fast 2D simulator matches)

Exports:
  - ml/models/bot_brain.json                  (for ml/sim2d.py & Python tools)
  - ml/models/CoreAthenaBotBrainWeights.h     (pure C++20 header for CoreV2, < 2 us per bot)
"""
import argparse
import glob
import json
import os

import numpy as np
import torch
import torch.nn as nn

from schema import ACTIONS, FEATURE_NAMES, extract_features, infer_action_from_transition


class TacticalMLP(nn.Module):
    def __init__(self, in_dim: int = 16, hidden1: int = 64, hidden2: int = 32, out_dim: int = 7):
        super().__init__()
        self.fc1 = nn.Linear(in_dim, hidden1)
        self.fc2 = nn.Linear(hidden1, hidden2)
        self.fc3 = nn.Linear(hidden2, out_dim)

    def forward(self, x):
        h1 = torch.relu(self.fc1(x))
        h2 = torch.relu(self.fc2(h1))
        return self.fc3(h2)


def load_jsonl_dataset(
    paths: list[str],
    human_weight: float = 3.0,
    s7_weight: float = 1.5,
    max_samples: int = 500_000,
):
    """Load transitions evenly across all non-empty match files so no single large match starves the rest."""
    valid_paths = [p for p in sorted(paths) if os.path.isfile(p) and os.path.getsize(p) > 1024]
    if not valid_paths:
        return None, None, None, 0

    per_file_cap = max(5_000, (max_samples * 2) // max(1, len(valid_paths)))
    xs, ys, ws = [], [], []
    files_used = 0
    for path in valid_paths:
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
        if len(frames) < 2:
            continue
        files_used += 1
        file_xs, file_ys, file_ws = [], [], []
        for i in range(len(frames) - 1):
            cur_snap, nxt_snap = frames[i], frames[i + 1]
            if cur_snap.get("phase") not in ("SafeZones", "Aircraft", "EndGame"):
                continue
            prev_snap = frames[i - 1] if i > 0 else None
            nxt_map = {(a.get("id") or a.get("name")): a for a in nxt_snap.get("actors", ())}
            prev_map = {(a.get("id") or a.get("name")): a for a in (prev_snap.get("actors", ()) if prev_snap else ())}
            src = str(cur_snap.get("source", ""))
            is_ch1 = src.startswith("replay-s3") or src.startswith("replay-s7") or src.startswith("replay-ch1")
            for a in cur_snap.get("actors", ()):
                if not a.get("alive", True) or a.get("state") in ("InAircraft", "Skydiving", "Warmup"):
                    continue
                aid = a.get("id") or a.get("name")
                nxt = nxt_map.get(aid)
                if not nxt:
                    continue
                feat = extract_features(a, cur_snap, prev_map.get(aid))
                label = infer_action_from_transition(a, nxt, cur_snap)
                is_human = not a.get("isBot", True) or src.startswith("replay")
                weight = human_weight if is_human else 1.0
                if is_ch1:
                    weight *= s7_weight
                file_xs.append(feat)
                file_ys.append(label)
                file_ws.append(weight)
        if not file_xs:
            continue
        if len(file_xs) > per_file_cap:
            rng_f = np.random.default_rng(abs(hash(os.path.basename(path))) % (2**32))
            take = rng_f.choice(len(file_xs), size=per_file_cap, replace=False)
            for idx_t in take:
                xs.append(file_xs[idx_t])
                ys.append(file_ys[idx_t])
                ws.append(file_ws[idx_t])
        else:
            xs.extend(file_xs)
            ys.extend(file_ys)
            ws.extend(file_ws)
    if not xs:
        return None, None, None, 0
    X = np.array(xs, dtype=np.float32)
    y = np.array(ys, dtype=np.int64)
    w = np.array(ws, dtype=np.float32)
    if len(X) > max_samples:
        rng_all = np.random.default_rng(42)
        keep = rng_all.choice(len(X), size=max_samples, replace=False)
        X, y, w = X[keep], y[keep], w[keep]
    return X, y, w, files_used


def export_cpp_header(model: TacticalMLP, out_path: str, val_acc: float, total_samples: int, n_files: int):
    w1 = model.fc1.weight.detach().cpu().numpy()  # (64, 16)
    b1 = model.fc1.bias.detach().cpu().numpy()    # (64,)
    w2 = model.fc2.weight.detach().cpu().numpy()  # (32, 64)
    b2 = model.fc2.bias.detach().cpu().numpy()    # (32,)
    w3 = model.fc3.weight.detach().cpu().numpy()  # (7, 32)
    b3 = model.fc3.bias.detach().cpu().numpy()    # (7,)

    def fmt_arr(arr):
        return ", ".join(f"{float(v):.6f}f" for v in arr.ravel())

    lines = [
        "#pragma once",
        "// Auto-generated by ml/train_bc.py (Behavioral Cloning tactical brain for CoreV2 bots).",
        f"// Trained on {total_samples} real transitions from {n_files} matches (val accuracy: {val_acc * 100.0:.1f}%).",
        "// Architecture: 16 -> 64 -> 32 -> 7 MLP. Pure C++20, zero dependencies, < 4 us per bot evaluation.",
        "",
        "namespace CoreBotBrain",
        "{",
        f"    inline constexpr int32_t NumFeatures = {w1.shape[1]};",
        f"    inline constexpr int32_t NumHidden1 = {w1.shape[0]};",
        f"    inline constexpr int32_t NumHidden2 = {w2.shape[0]};",
        f"    inline constexpr int32_t NumActions = {w3.shape[0]};",
        "",
        "    enum class ETacticalAction : int32_t",
        "    {",
        *[f"        {name} = {i}," for i, name in enumerate(ACTIONS)],
        "    };",
        "",
        "    // Feature order: " + ", ".join(FEATURE_NAMES),
        f"    inline constexpr float W1[{w1.size}] = {{ {fmt_arr(w1)} }};",
        f"    inline constexpr float B1[{b1.size}] = {{ {fmt_arr(b1)} }};",
        f"    inline constexpr float W2[{w2.size}] = {{ {fmt_arr(w2)} }};",
        f"    inline constexpr float B2[{b2.size}] = {{ {fmt_arr(b2)} }};",
        f"    inline constexpr float W3[{w3.size}] = {{ {fmt_arr(w3)} }};",
        f"    inline constexpr float B3[{b3.size}] = {{ {fmt_arr(b3)} }};",
        "",
        "    inline ETacticalAction Evaluate(const float Features[NumFeatures], float* OutLogits = nullptr)",
        "    {",
        "        float Hidden1[NumHidden1];",
        "        for (int32_t i = 0; i < NumHidden1; ++i)",
        "        {",
        "            float Sum = B1[i];",
        "            const int32_t Row = i * NumFeatures;",
        "            for (int32_t j = 0; j < NumFeatures; ++j)",
        "                Sum += W1[Row + j] * Features[j];",
        "            Hidden1[i] = Sum > 0.0f ? Sum : 0.0f;",
        "        }",
        "        float Hidden2[NumHidden2];",
        "        for (int32_t i = 0; i < NumHidden2; ++i)",
        "        {",
        "            float Sum = B2[i];",
        "            const int32_t Row = i * NumHidden1;",
        "            for (int32_t j = 0; j < NumHidden1; ++j)",
        "                Sum += W2[Row + j] * Hidden1[j];",
        "            Hidden2[i] = Sum > 0.0f ? Sum : 0.0f;",
        "        }",
        "        int32_t BestAction = 0;",
        "        float BestScore = -1e30f;",
        "        for (int32_t o = 0; o < NumActions; ++o)",
        "        {",
        "            float Score = B3[o];",
        "            const int32_t Row = o * NumHidden2;",
        "            for (int32_t i = 0; i < NumHidden2; ++i)",
        "                Score += W3[Row + i] * Hidden2[i];",
        "            if (OutLogits) OutLogits[o] = Score;",
        "            if (Score > BestScore)",
        "            {",
        "                BestScore = Score;",
        "                BestAction = o;",
        "            }",
        "        }",
        "        return static_cast<ETacticalAction>(BestAction);",
        "    }",
        "} // namespace CoreBotBrain",
        "",
    ]
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main():
    ap = argparse.ArgumentParser(description="Train Behavioral Cloning tactical bot brain")
    ap.add_argument("--data", nargs="+", default=[
        "ml/data/replays_ch1_jsonl/*.jsonl",
        "ml/data/replays_jsonl/*.jsonl",
        "server/data/matches/*.jsonl",
    ])
    ap.add_argument("--epochs", type=int, default=25)
    ap.add_argument("--batch-size", type=int, default=512)
    ap.add_argument("--lr", type=float, default=3e-3)
    ap.add_argument("--human-weight", type=float, default=3.0)
    ap.add_argument("--s7-weight", type=float, default=1.5)
    ap.add_argument("--max-samples", type=int, default=500_000)
    ap.add_argument("--init", type=str, default=None, help="Optional pretrained bot_brain.json to fine-tune from")
    ap.add_argument("--out-dir", type=str, default="ml/models")
    args = ap.parse_args()

    paths = []
    for pattern in args.data:
        paths.extend(glob.glob(pattern))
    paths = sorted(set(paths))
    X, y, w, n_files = load_jsonl_dataset(
        paths,
        human_weight=args.human_weight,
        s7_weight=args.s7_weight,
        max_samples=args.max_samples,
    )
    if X is None:
        raise SystemExit("No JSONL samples found. Run `python ml/sim2d.py --generate 20` or extract .replay files first.")

    print(f"Loaded {len(X)} transitions from {n_files} match file(s).")
    counts = np.bincount(y, minlength=len(ACTIONS))
    print("Action distribution:", ", ".join(f"{ACTIONS[i]}={counts[i]}" for i in range(len(ACTIONS))))

    # Deterministic train/val split (85/15)
    rng = np.random.default_rng(42)
    idx = rng.permutation(len(X))
    split = int(len(X) * 0.85)
    tr, va = idx[:split], idx[split:]

    # Inverse-sqrt class weighting so rare high-value actions (TakeHeight, Escape, BoxAndHeal) aren't drowned out
    cls_w = np.ones(len(ACTIONS), dtype=np.float32)
    for c in range(len(ACTIONS)):
        if counts[c] > 0:
            cls_w[c] = (len(X) / (len(ACTIONS) * counts[c])) ** 0.5
    cls_w /= cls_w.mean()

    torch.manual_seed(42)
    model = TacticalMLP(in_dim=X.shape[1], hidden1=64, hidden2=32, out_dim=len(ACTIONS))
    if args.init and os.path.isfile(args.init):
        with open(args.init, encoding="utf-8") as f:
            pre = json.load(f)
        if "w3" in pre:
            with torch.no_grad():
                model.fc1.weight.copy_(torch.tensor(pre["w1"], dtype=torch.float32))
                model.fc1.bias.copy_(torch.tensor(pre["b1"], dtype=torch.float32))
                model.fc2.weight.copy_(torch.tensor(pre["w2"], dtype=torch.float32))
                model.fc2.bias.copy_(torch.tensor(pre["b2"], dtype=torch.float32))
                model.fc3.weight.copy_(torch.tensor(pre["w3"], dtype=torch.float32))
                model.fc3.bias.copy_(torch.tensor(pre["b3"], dtype=torch.float32))
            print(f"Loaded pretrained weights from {args.init} for fine-tuning.")
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=args.epochs, eta_min=args.lr * 0.05)
    loss_fn = nn.CrossEntropyLoss(weight=torch.tensor(cls_w), reduction="none")

    X_tr, y_tr, w_tr = torch.tensor(X[tr]), torch.tensor(y[tr]), torch.tensor(w[tr])
    X_va, y_va = torch.tensor(X[va]), torch.tensor(y[va])

    best_acc = -1.0
    best_state = None

    for ep in range(1, args.epochs + 1):
        model.train()
        perm = torch.randperm(len(X_tr))
        total_loss = 0.0
        for s in range(0, len(X_tr), args.batch_size):
            b = perm[s : s + args.batch_size]
            opt.zero_grad()
            losses = loss_fn(model(X_tr[b]), y_tr[b]) * w_tr[b]
            loss = losses.mean()
            loss.backward()
            opt.step()
            total_loss += float(loss.item()) * len(b)
        sched.step()
        model.eval()
        with torch.no_grad():
            pred = model(X_va).argmax(dim=1)
            acc = float((pred == y_va).float().mean().item())
        if acc > best_acc:
            best_acc = acc
            best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
        if ep in (1, 5, 10, 15, 20, 30, 40, args.epochs):
            print(f"Epoch {ep:2d}/{args.epochs} | loss={total_loss/len(X_tr):.4f} | val_acc={acc*100:.2f}% (best={best_acc*100:.2f}%)")

    if best_state is not None:
        model.load_state_dict(best_state)

    model.eval()
    with torch.no_grad():
        va_pred = model(X_va).argmax(dim=1).numpy()
        val_acc = float((va_pred == y[va]).mean())

    per_cls = []
    for c, name in enumerate(ACTIONS):
        mask = (y[va] == c)
        if mask.sum() > 0:
            c_acc = float((va_pred[mask] == c).mean())
            per_cls.append(f"{name}={c_acc*100:.1f}%({int(mask.sum())})")
    print("Per-action val accuracy:", ", ".join(per_cls))

    os.makedirs(args.out_dir, exist_ok=True)
    json_path = os.path.join(args.out_dir, "bot_brain.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "arch": "16-64-32-7",
            "features": FEATURE_NAMES,
            "actions": ACTIONS,
            "val_accuracy": round(val_acc, 4),
            "samples": int(len(X)),
            "matches": int(n_files),
            "w1": model.fc1.weight.detach().cpu().numpy().round(6).tolist(),
            "b1": model.fc1.bias.detach().cpu().numpy().round(6).tolist(),
            "w2": model.fc2.weight.detach().cpu().numpy().round(6).tolist(),
            "b2": model.fc2.bias.detach().cpu().numpy().round(6).tolist(),
            "w3": model.fc3.weight.detach().cpu().numpy().round(6).tolist(),
            "b3": model.fc3.bias.detach().cpu().numpy().round(6).tolist(),
        }, f)

    cpp_path = os.path.join(args.out_dir, "CoreAthenaBotBrainWeights.h")
    export_cpp_header(model, cpp_path, val_acc, len(X), n_files)
    print(f"\nExported:\n  - {json_path}\n  - {cpp_path}\nFinal best val accuracy: {val_acc*100:.2f}%")


if __name__ == "__main__":
    main()
