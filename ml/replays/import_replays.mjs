// Downloads Fortnite tournament replays (by matchId) and/or parses local .replay files into
// our unified Season-7-compatible JSONL format for Behavioral Cloning (ml/train_bc.py).
//
// Filters out modern-only movement mechanics (sliding, vehicles, tactical sprint / mobility > 820 uu/s)
// so the AI only learns Chapter 1 / Season 7 compatible tactical decisions.
//
// Usage:
//   node ml/replays/import_replays.mjs --match <matchId1> [matchId2 ...]
//   node ml/replays/import_replays.mjs --dir <folder-with-replays>
//   node ml/replays/import_replays.mjs --file <path/to/file.replay>
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const replayDownloader = require("fortnite-replay-downloader");
const parseReplay = require("fortnite-replay-parser");
const defaultHandleEventEmitter = require("fortnite-replay-parser/export/handleEventEmitter");

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..", "..");
const REPLAYS_DIR = path.join(root, "ml", "data", "replays");
const OUT_DIR = path.join(root, "ml", "data", "replays_jsonl");

// Chapter 1 Season 7 movement filter: reject frames where a player is sliding, driving, or moving
// faster than normal Chapter 1 foot speed (~600 uu/s, allow margin up to 820 uu/s for slopes/jumps).
const MAX_CH1_FOOT_SPEED = 820.0;
const SAMPLE_INTERVAL_S = 1.0;

function isModernMovement(player, prevPos, dt) {
  if (player.bIsSliding || player.bIsSlopeSliding || player.bIsTacticalSprinting) return true;
  if (player.bIsDriving || player.Vehicle || player.CurrentVehicle || player.VehicleSeat != null) return true;
  if (player.bIsZiplining || player.bIsSkydiving || player.bIsParachuteOpen) return true;
  const loc = player.ReplicatedMovement?.location;
  if (loc && prevPos && dt > 0.1) {
    const speed2d = Math.hypot((loc.x ?? 0) - prevPos.x, (loc.y ?? 0) - prevPos.y) / dt;
    if (speed2d > MAX_CH1_FOOT_SPEED) return true;
  }
  return false;
}

function getLocation(player) {
  const loc = player.ReplicatedMovement?.location;
  if (!loc) return null;
  const x = Number(loc.x), y = Number(loc.y), z = Number(loc.z ?? 0);
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
  return { x, y, z };
}

function getYaw(player) {
  const rot = player.ReplicatedMovement?.rotation;
  return Number.isFinite(rot?.yaw) ? Number(rot.yaw) : 0;
}

function getHealth(player) {
  const cur = player.health?.CurrentValue ?? player.health?.BaseValue;
  return Number.isFinite(cur) ? Math.max(0, Math.min(100, cur)) : 100;
}

function getShield(player) {
  const cur = player.shield?.CurrentValue ?? player.shield?.BaseValue;
  return Number.isFinite(cur) ? Math.max(0, Math.min(100, cur)) : 0;
}

async function parseReplayBufferToJsonl(buffer, name) {
  fs.mkdirSync(OUT_DIR, { recursive: true });
  const frames = [];
  const prevPositions = new Map();
  let lastSampleTime = -999;
  let keptActors = 0;
  let filteredModern = 0;
  let buildsTotal = 0;

  await parseReplay(buffer, {
    parseLevel: 10,
    parseEvents: true,
    parsePackets: true,
    handleEventEmitter: (emitters) => {
      defaultHandleEventEmitter(emitters);
      emitters.propertyExportEmitter.on("PlayerBuilds", () => { buildsTotal++; });
      emitters.parsingEmitter.on("nextFrame", ({ timeSeconds, result, states }) => {
        if (!Number.isFinite(timeSeconds) || timeSeconds - lastSampleTime < SAMPLE_INTERVAL_S) return;
        const dt = lastSampleTime > -100 ? timeSeconds - lastSampleTime : SAMPLE_INTERVAL_S;
        lastSampleTime = timeSeconds;

        const playersObj = states?.players;
        if (!playersObj) return;

        const actors = [];
        for (const [ch, p] of Object.entries(playersObj)) {
          if (!p || p.bOnlySpectator) continue;
          const loc = getLocation(p);
          if (!loc) continue;
          const id = String(p.UniqueId ?? p.PlayerId ?? `ch${ch}`);
          const prev = prevPositions.get(id);
          const alive = !p.bIsDying && !p.bIsDead && (p.Place == null || p.Place === 0);

          if (alive && isModernMovement(p, prev, dt)) {
            filteredModern++;
            prevPositions.set(id, { ...loc, t: timeSeconds });
            continue;
          }
          prevPositions.set(id, { ...loc, t: timeSeconds });

          if (!alive) continue;
          keptActors++;
          actors.push({
            id,
            name: String(p.PlayerNamePrivate ?? id).slice(0, 24),
            isBot: Boolean(p.bIsABot),
            x: Math.round(loc.x),
            y: Math.round(loc.y),
            z: Math.round(loc.z),
            yaw: Math.round(getYaw(p) * 10) / 10,
            hp: Math.round(getHealth(p)),
            maxHp: 100,
            shield: Math.round(getShield(p)),
            maxShield: 100,
            mats: 250,
            hasWeapon: true,
            hasHeals: getHealth(p) + getShield(p) < 160,
            alive: true,
            dbno: Boolean(p.bIsDBNO),
            kills: Number(p.KillScore ?? 0),
            team: p.TeamIndex != null ? Number(p.TeamIndex) : -1,
            state: "Landed",
            style: p.bIsABot ? "replay-bot" : "replay-human",
          });
        }

        if (!actors.length) return;
        const zones = result?.gameData?.safeZones ?? [];
        const z = zones.length ? zones[zones.length - 1] : null;
        const storm = z ? {
          phase: zones.length,
          x: Math.round(z.LastCenter?.x ?? z.NextCenter?.x ?? 0),
          y: Math.round(z.LastCenter?.y ?? z.NextCenter?.y ?? 0),
          radius: Math.round(z.LastRadius ?? z.Radius ?? 0),
          nextX: Math.round(z.NextCenter?.x ?? 0),
          nextY: Math.round(z.NextCenter?.y ?? 0),
          nextRadius: Math.round(z.NextRadius ?? 0),
        } : null;

        frames.push({
          ts: Math.round(timeSeconds * 1000),
          source: "replay-filtered",
          phase: timeSeconds < 25 ? "Aircraft" : "SafeZones",
          phaseStep: storm ? "StormShrinking" : "Warmup",
          timeRemaining: z?.SafeZoneFinishShrinkTime ? Math.max(0, Math.round(z.SafeZoneFinishShrinkTime - timeSeconds)) : 30,
          playersLeft: actors.length,
          totalPlayers: Object.keys(playersObj).length || actors.length,
          storm,
          actors,
        });
      });
    },
  });

  const outFile = path.join(OUT_DIR, `${name}.jsonl`);
  fs.writeFileSync(outFile, frames.map((f) => JSON.stringify(f)).join("\n") + (frames.length ? "\n" : ""));
  console.log(`  -> ${name}: ${frames.length} frames, ${keptActors} clean Ch1-compatible actor samples (filtered ${filteredModern} modern-movement/airborne samples, ${buildsTotal} builds) -> ${outFile}`);
  return frames.length;
}

async function downloadAndParseMatch(matchId) {
  fs.mkdirSync(REPLAYS_DIR, { recursive: true });
  const localReplay = path.join(REPLAYS_DIR, `${matchId}.replay`);
  let buffer;
  if (fs.existsSync(localReplay)) {
    console.log(`Using cached replay ${localReplay}`);
    buffer = fs.readFileSync(localReplay);
  } else {
    console.log(`Downloading tournament replay ${matchId}...`);
    buffer = await replayDownloader.downloadReplay({
      matchId,
      eventCount: 1000,
      dataCount: 1000,
      checkpointCount: 0,
      maxConcurrentDownloads: 8,
      updateCallback: (u) => {
        if (u.dataChunks?.max) {
          process.stdout.write(`\r  chunks: ${u.dataChunks.current}/${u.dataChunks.max}   `);
        }
      },
    });
    process.stdout.write("\n");
    fs.writeFileSync(localReplay, buffer);
    console.log(`Saved ${localReplay} (${(buffer.length / (1024 * 1024)).toFixed(1)} MB)`);
  }
  return parseReplayBufferToJsonl(buffer, matchId);
}

async function main() {
  const args = process.argv.slice(2);
  if (!args.length) {
    console.log("Usage:");
    console.log("  node ml/replays/import_replays.mjs --match <matchId1> [matchId2 ...]");
    console.log("  node ml/replays/import_replays.mjs --file <path/to/file.replay>");
    console.log("  node ml/replays/import_replays.mjs --dir <folder> [--limit 20]");
    process.exit(0);
  }

  let limit = Infinity;
  const limIdx = args.indexOf("--limit");
  if (limIdx !== -1 && args[limIdx + 1]) limit = Math.max(1, Number(args[limIdx + 1]));

  if (args[0] === "--match") {
    const ids = args.slice(1).filter((a) => !a.startsWith("--"));
    for (const id of ids) {
      try {
        await downloadAndParseMatch(id);
      } catch (e) {
        console.error(`Failed match ${id}:`, e?.message ?? e);
      }
    }
    return;
  }

  const files = [];
  if (args[0] === "--file" && args[1]) {
    files.push(args[1]);
  } else if (args[0] === "--dir" && args[1]) {
    for (const entry of fs.readdirSync(args[1])) {
      if (entry.toLowerCase().endsWith(".replay")) files.push(path.join(args[1], entry));
    }
  }

  let ok = 0;
  for (const f of files.slice(0, limit)) {
    const base = path.basename(f, path.extname(f));
    console.log(`Parsing ${path.basename(f)}...`);
    try {
      const n = await parseReplayBufferToJsonl(fs.readFileSync(f), base);
      if (n > 0) ok++;
    } catch (e) {
      console.error(`  !! Failed ${path.basename(f)}: ${e?.message ?? e}`);
    }
  }
  console.log(`Done: parsed ${ok}/${Math.min(files.length, limit)} replay(s).`);
}

main();
