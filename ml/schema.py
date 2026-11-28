"""Shared schema for Fortnite 7.40 tactical ML (Behavioral Cloning, 2D Simulator, Replay Extractor, CoreV2 export).

Single source of truth for:
- The 16 normalized input features
- The 7 tactical actions
- Season 7 (Athena v7.40) world coordinate bounds and POIs
- Feature extraction & automatic action inference from telemetry/replay frames
"""
import math

WORLD_MIN = -135000.0
WORLD_MAX = 135000.0
WORLD_SPAN = WORLD_MAX - WORLD_MIN

S7_POIS = [
    {"name": "Junk Junction",  "x": 108000.0,  "y": -76000.0,  "loot": 0.65},
    {"name": "Haunted Hills",  "x": 78000.0,   "y": -92000.0,  "loot": 0.55},
    {"name": "Pleasant Park",  "x": 52000.0,   "y": -52000.0,  "loot": 0.90},
    {"name": "Lazy Links",     "x": 79000.0,   "y": 12000.0,   "loot": 0.75},
    {"name": "The Block",      "x": 92000.0,   "y": 52000.0,   "loot": 0.80},
    {"name": "Wailing Woods",  "x": 56000.0,   "y": 88000.0,   "loot": 0.65},
    {"name": "Snobby Shores",  "x": 12000.0,   "y": -108000.0, "loot": 0.65},
    {"name": "Loot Lake",      "x": 24000.0,   "y": -16000.0,  "loot": 0.75},
    {"name": "Tilted Towers",  "x": -14000.0,  "y": -36000.0,  "loot": 1.00},
    {"name": "Dusty Divot",    "x": -6000.0,   "y": 26000.0,   "loot": 0.70},
    {"name": "Retail Row",     "x": -22000.0,  "y": 66000.0,   "loot": 0.90},
    {"name": "Lonely Lodge",   "x": 10000.0,   "y": 102000.0,  "loot": 0.60},
    {"name": "Shifty Shafts",  "x": -44000.0,  "y": -36000.0,  "loot": 0.65},
    {"name": "Salty Springs",  "x": -42000.0,  "y": 12000.0,   "loot": 0.80},
    {"name": "Frosty Flights", "x": -46000.0,  "y": -98000.0,  "loot": 0.70},
    {"name": "Polar Peak",     "x": -62000.0,  "y": -64000.0,  "loot": 0.85},
    {"name": "Fatal Fields",   "x": -78000.0,  "y": 22000.0,   "loot": 0.75},
    {"name": "Paradise Palms", "x": -64000.0,  "y": 82000.0,   "loot": 0.90},
    {"name": "Happy Hamlet",   "x": -102000.0, "y": -46000.0,  "loot": 0.85},
    {"name": "Lucky Landing",  "x": -112000.0, "y": 18000.0,   "loot": 0.70},
]

FEATURE_NAMES = [
    "hp_norm",            # 0: health / 100
    "shield_norm",        # 1: shield / 100
    "mats_norm",          # 2: materials / 999
    "has_weapon",         # 3: 1 if carrying usable weapon
    "has_heals",          # 4: 1 if carrying healing/shield consumables
    "enemy_dist_norm",    # 5: nearest enemy distance / 15000 (150m), clamped [0,1]
    "enemy_dz_norm",      # 6: (enemy_z - my_z) / 1500, clamped [-1,1]
    "enemies_near_norm",  # 7: alive enemies within 40m / 5, clamped [0,1]
    "in_storm",           # 8: 1 if currently outside current storm circle
    "dist_next_zone",     # 9: signed distance outside next safe zone / 60000, clamped [-1,1]
    "storm_time_norm",    # 10: time remaining in current storm step / 120
    "storm_phase_norm",   # 11: storm phase / 8
    "recently_hurt",      # 12: 1 if damaged in last 5s
    "weapon_close",       # 13: 1 if best weapon is close-range (shotgun/SMG)
    "alive_ratio",        # 14: playersLeft / totalPlayers
    "loadout_score",      # 15: 0..1 weapon/loadout readiness
]

ACTIONS = [
    "Fight",        # 0: engage enemy / hold angle / push
    "TakeHeight",   # 1: build ramps up to claim high ground
    "BoxAndHeal",   # 2: box up defensively and use healing/shields
    "RotateStorm",  # 3: prioritize moving into the next safe zone
    "Escape",       # 4: disengage and run away (unarmed or very low HP)
    "Loot",         # 5: search chests/floor for better weapons & heals
    "Harvest",      # 6: farm materials before fighting
]
ACTION_TO_IDX = {name: i for i, name in enumerate(ACTIONS)}


def clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def dist2d(ax: float, ay: float, bx: float, by: float) -> float:
    return math.hypot(ax - bx, ay - by)


def extract_features(actor: dict, snapshot: dict, prev_actor: dict | None = None) -> list[float]:
    """Build the 16-float normalized feature vector from any unified snapshot."""
    ax = float(actor.get("x", 0.0))
    ay = float(actor.get("y", 0.0))
    az = float(actor.get("z", 0.0))
    hp = float(actor.get("hp", 100.0))
    shield = float(actor.get("shield", 0.0))
    mats = float(actor.get("mats", 150.0))
    team = actor.get("team", -1)
    aid = actor.get("id") or actor.get("name")

    has_weapon = 1.0 if actor.get("hasWeapon", True) else 0.0
    has_heals = 1.0 if actor.get("hasHeals", False) else 0.0
    weapon_close = 1.0 if actor.get("weaponClose", False) else 0.0
    loadout_score = float(actor.get("loadout", 0.6 if has_weapon else 0.0))

    # Nearest alive enemy and local enemy density (40m = 4000 uu)
    nearest_dist = 15000.0
    nearest_dz = 0.0
    enemies_near = 0
    for other in snapshot.get("actors", ()):
        if not other.get("alive", True):
            continue
        oid = other.get("id") or other.get("name")
        if oid == aid:
            continue
        if team != -1 and other.get("team", -2) == team:
            continue
        d = dist2d(ax, ay, float(other.get("x", 0.0)), float(other.get("y", 0.0)))
        if d < 4000.0:
            enemies_near += 1
        if d < nearest_dist:
            nearest_dist = d
            nearest_dz = float(other.get("z", 0.0)) - az

    storm = snapshot.get("storm") or {}
    cur_r = float(storm.get("radius", 0.0))
    cur_x = float(storm.get("x", 0.0))
    cur_y = float(storm.get("y", 0.0))
    in_storm = 1.0 if (cur_r > 0.0 and dist2d(ax, ay, cur_x, cur_y) > cur_r) else 0.0

    next_r = float(storm.get("nextRadius", cur_r))
    next_x = float(storm.get("nextX", cur_x))
    next_y = float(storm.get("nextY", cur_y))
    dist_next_edge = (dist2d(ax, ay, next_x, next_y) - next_r) if next_r > 0.0 else 0.0

    time_rem = float(snapshot.get("timeRemaining", 60.0))
    storm_phase = float(storm.get("phase", 0.0))

    recently_hurt = 1.0 if actor.get("recentlyHurt", False) else 0.0
    if not recently_hurt and prev_actor is not None:
        prev_eff = float(prev_actor.get("hp", hp)) + float(prev_actor.get("shield", shield))
        if hp + shield < prev_eff - 1.0:
            recently_hurt = 1.0

    players_left = max(1.0, float(snapshot.get("playersLeft", 1)))
    total_players = max(players_left, float(snapshot.get("totalPlayers", 50)))

    return [
        clamp(hp / 100.0, 0.0, 1.0),
        clamp(shield / 100.0, 0.0, 1.0),
        clamp(mats / 999.0, 0.0, 1.0),
        has_weapon,
        has_heals,
        clamp(nearest_dist / 15000.0, 0.0, 1.0),
        clamp(nearest_dz / 1500.0, -1.0, 1.0),
        clamp(enemies_near / 5.0, 0.0, 1.0),
        in_storm,
        clamp(dist_next_edge / 60000.0, -1.0, 1.0),
        clamp(time_rem / 120.0, 0.0, 1.0),
        clamp(storm_phase / 8.0, 0.0, 1.0),
        recently_hurt,
        weapon_close,
        clamp(players_left / total_players, 0.0, 1.0),
        clamp(loadout_score, 0.0, 1.0),
    ]


FEATURE_NAMES_V2 = FEATURE_NAMES + [
    "skill_level",    # 16: 0.1..1.0 bot difficulty / player skill percentile
    "vel_x_norm",     # 17: player velocity X / 600, clamped [-1,1]
    "vel_y_norm",     # 18: player velocity Y / 600, clamped [-1,1]
    "vel_z_norm",     # 19: player velocity Z / 400, clamped [-1,1]
    "enemy_dx_norm",  # 20: unit vector X toward nearest enemy
    "enemy_dy_norm",  # 21: unit vector Y toward nearest enemy
    "zone_dx_norm",   # 22: unit vector X toward next safe zone center
    "zone_dy_norm",   # 23: unit vector Y toward next safe zone center
    "hm_traffic",     # 24: Chapter 1 50x50 heatmap traffic density [0,1]
    "hm_danger",      # 25: Chapter 1 50x50 heatmap death danger [0,1]
]


class HeatmapLookup:
    """Fast O(1) lookup into server/data/heatmap-s7.json (50x50 Chapter 1 Athena Influence Map)."""

    def __init__(self, path: str = "server/data/heatmap-s7.json"):
        import json, os
        self.grid = 50
        self.traffic = None
        self.deaths = None
        if path and os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            self.grid = int(d.get("grid", 50))
            self.traffic = d.get("traffic")
            self.deaths = d.get("deaths")

    def sample(self, x: float, y: float) -> tuple[float, float]:
        if self.traffic is None or self.deaths is None:
            return 0.0, 0.0
        g = self.grid
        u = int((y - WORLD_MIN) / WORLD_SPAN * g)
        v = int((1.0 - (x - WORLD_MIN) / WORLD_SPAN) * g)
        u = 0 if u < 0 else (g - 1 if u >= g else u)
        v = 0 if v < 0 else (g - 1 if v >= g else v)
        return float(self.traffic[v][u]), float(self.deaths[v][u])


def extract_features_v2(
    actor: dict,
    snapshot: dict,
    prev_actor: dict | None = None,
    dt: float = 1.0,
    skill_level: float = 0.7,
    heatmap: HeatmapLookup | None = None,
) -> list[float]:
    """Build the 26-float V2 feature vector (16 core + skill + velocity + enemy/zone dirs + Ch1 heatmap)."""
    base = extract_features(actor, snapshot, prev_actor)
    ax = float(actor.get("x", 0.0))
    ay = float(actor.get("y", 0.0))
    az = float(actor.get("z", 0.0))
    team = actor.get("team", -1)
    aid = actor.get("id") or actor.get("name")

    vx = vy = vz = 0.0
    if prev_actor is not None and dt > 0.05:
        vx = clamp((ax - float(prev_actor.get("x", ax))) / (dt * 600.0), -1.0, 1.0)
        vy = clamp((ay - float(prev_actor.get("y", ay))) / (dt * 600.0), -1.0, 1.0)
        vz = clamp((az - float(prev_actor.get("z", az))) / (dt * 400.0), -1.0, 1.0)

    # Unit direction toward nearest enemy
    edx = edy = 0.0
    best_d = 15000.0
    for other in snapshot.get("actors", ()):
        if not other.get("alive", True):
            continue
        oid = other.get("id") or other.get("name")
        if oid == aid:
            continue
        if team != -1 and other.get("team", -2) == team:
            continue
        ox, oy = float(other.get("x", 0.0)), float(other.get("y", 0.0))
        d = math.hypot(ox - ax, oy - ay)
        if 1.0 < d < best_d:
            best_d = d
            edx, edy = (ox - ax) / d, (oy - ay) / d

    # Unit direction toward next safe zone center
    zdx = zdy = 0.0
    storm = snapshot.get("storm") or {}
    nx = float(storm.get("nextX", storm.get("x", 0.0)))
    ny = float(storm.get("nextY", storm.get("y", 0.0)))
    zd = math.hypot(nx - ax, ny - ay)
    if zd > 1.0:
        zdx, zdy = (nx - ax) / zd, (ny - ay) / zd

    hm_t, hm_d = heatmap.sample(ax, ay) if heatmap is not None else (0.0, 0.0)
    return base + [
        clamp(float(skill_level), 0.0, 1.0),
        vx, vy, vz,
        edx, edy,
        zdx, zdy,
        hm_t, hm_d,
    ]


def infer_action_from_transition(cur: dict, nxt: dict, snap_cur: dict) -> int:
    """Infer a tactical action label for unlabeled human/replay recordings from state(t) -> state(t+1)."""
    # Explicit action label if already present (e.g. simulator or instrumented telemetry)
    explicit = cur.get("action")
    if explicit in ACTION_TO_IDX:
        return ACTION_TO_IDX[explicit]

    goal = str(cur.get("goal", ""))
    goal_map = {
        "Fight": 0, "Hunt": 0,
        "TakeHeight": 1,
        "BoxAndHeal": 2, "Heal": 2,
        "RotateStorm": 3, "Storm": 3,
        "Escape": 4,
        "Loot": 5, "Search": 5, "TravelToPOI": 5,
        "Harvest": 6,
    }
    if goal in goal_map:
        return goal_map[goal]

    hp0, sh0 = float(cur.get("hp", 100.0)), float(cur.get("shield", 0.0))
    hp1, sh1 = float(nxt.get("hp", hp0)), float(nxt.get("shield", sh0))
    if (hp1 + sh1) > (hp0 + sh0) + 5.0:
        return ACTION_TO_IDX["BoxAndHeal"]

    mats0, mats1 = float(cur.get("mats", 0.0)), float(nxt.get("mats", 0.0))
    if mats1 > mats0 + 15.0:
        return ACTION_TO_IDX["Harvest"]

    ax0, ay0, az0 = float(cur.get("x", 0.0)), float(cur.get("y", 0.0)), float(cur.get("z", 0.0))
    ax1, ay1, az1 = float(nxt.get("x", ax0)), float(nxt.get("y", ay0)), float(nxt.get("z", az0))

    # Nearest enemy distance change
    aid = cur.get("id") or cur.get("name")
    team = cur.get("team", -1)
    best_e, best_d = None, 1e9
    for o in snap_cur.get("actors", ()):
        if not o.get("alive", True) or (o.get("id") or o.get("name")) == aid:
            continue
        if team != -1 and o.get("team", -2) == team:
            continue
        d = dist2d(ax0, ay0, float(o.get("x", 0.0)), float(o.get("y", 0.0)))
        if d < best_d:
            best_d, best_e = d, o

    if best_e is not None and best_d < 4500.0:
        is_replay = str(snap_cur.get("source", "")).startswith("replay")
        if az1 - az0 > 180.0 and (mats1 < mats0 or is_replay):
            return ACTION_TO_IDX["TakeHeight"]
        d1 = dist2d(ax1, ay1, float(best_e.get("x", 0.0)), float(best_e.get("y", 0.0)))
        # Disengaging rapidly from a close threat (low HP, unarmed, or sprinting away without ADS in replay)
        if (hp0 + sh0 < 65.0 or not cur.get("hasWeapon", True)) and d1 > best_d + 250.0:
            return ACTION_TO_IDX["Escape"]
        if is_replay and best_d < 3200.0 and not cur.get("targeting", False) and d1 > best_d + 380.0:
            return ACTION_TO_IDX["Escape"]
        if int(nxt.get("kills", 0)) > int(cur.get("kills", 0)) or best_d < 3200.0:
            return ACTION_TO_IDX["Fight"]

    storm = snap_cur.get("storm") or {}
    nr = float(storm.get("nextRadius", 0.0))
    if nr > 0.0:
        nx, ny = float(storm.get("nextX", 0.0)), float(storm.get("nextY", 0.0))
        d0_zone = dist2d(ax0, ay0, nx, ny)
        d1_zone = dist2d(ax1, ay1, nx, ny)
        if d0_zone > nr and d1_zone < d0_zone - 200.0:
            return ACTION_TO_IDX["RotateStorm"]

    return ACTION_TO_IDX["Loot"]
