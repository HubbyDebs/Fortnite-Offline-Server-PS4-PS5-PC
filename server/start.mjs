// Fortnite 3.6 (Season 3) backend for PS4 (and LAN PCs). Usage: node start.mjs [--pc-only] [--settings file] [--data dir]
//
// Runs Core-Backend (./core-backend, see setup.mjs) with the settings in settings.json and guards the port:
// only this PC (127.0.0.1) and the PS4s get in. ps4_ip is a fixed IPv4 or "auto", which finds the consoles on the
// network with the PlayStation discovery protocol (find-ps4.mjs). With "lan": true, any private IPv4 on the local
// network gets in too (PCs with Core Launcher, other consoles). Windows usually allows Node from any address, so the
// firewall alone does not protect this port.
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import tls from "node:tls";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import { findPs4 } from "./find-ps4.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const { values: opt } = parseArgs({ options: {
  "pc-only": { type: "boolean" },
  settings: { type: "string" },
  data: { type: "string" }
} });

function fail(text) {
  console.error(`ERROR: ${text}`);
  process.exit(2);
}
const readJson = (f) => JSON.parse(fs.readFileSync(f, "utf8").replace(/^﻿/, ""));

const settingsFile = opt.settings ?? path.join(here, "settings.json");
if (!opt.settings && !fs.existsSync(settingsFile)) fs.copyFileSync(path.join(here, "settings.example.json"), settingsFile);
let s;
try {
  s = readJson(settingsFile);
} catch (e) {
  fail(`settings.json: ${e.message}`);
}
if (!fs.existsSync(path.join(here, "core-backend", "src", "server.js"))) fail("Core-Backend is missing: run  node setup.mjs  first");

const within = (v, min, max) => Number.isInteger(v) && v >= min && v <= max;
const auto = s.ps4_ip === "auto";
if (!auto && !net.isIPv4(s.ps4_ip ?? "")) fail('ps4_ip must be an IPv4 address or "auto"');
// Addresses accepted as PS4s (with "auto" they follow the consoles when their IP changes).
const ps4s = new Set(auto ? [] : [s.ps4_ip]);
if (!within(s.port, 1, 65535)) fail("port is not valid");
if (!/^[A-Za-z0-9_]{3,16}$/.test(s.psn_account ?? "") || /^(Core)?Server/i.test(s.psn_account)) {
  fail("psn_account: 3-16 letters or digits, not starting with Server");
}
if (!within(s.bots?.count, 0, 99)) fail("bots.count goes from 0 to 99");
if (!within(s.bots?.level, 1, 10)) fail("bots.level goes from 1 to 10");
if (!within(s.bots?.fight_chance, 0, 100)) fail("bots.fight_chance goes from 0 to 100");
// The bots' twenty traits (0-100; see TRAITS), or none: every trait then follows bots.level as before. bots.mixed gives each
// bot a personality of its own around them (needs the Fortnite-Offline-Server build of CoreV2).
const TRAITS = ["aimAccuracy", "aimTracking", "reactionSpeed", "firingFrequency", "aggression", "pursuit", "buildFrequency", "buildSpeed",
  "heightSeeking", "defensiveBuilding", "editFrequency", "editSpeed", "strafing", "jumping", "awareness", "prediction", "looting",
  "harvesting", "healing", "rotation"];
let traits = null;
if (s.bots.traits != null) {
  if (typeof s.bots.traits !== "object" || Array.isArray(s.bots.traits)) fail("bots.traits must be an object of traits 0-100");
  traits = {};
  for (const name of TRAITS) {
    const v = s.bots.traits[name] ?? 50;
    if (!within(v, 0, 100)) fail(`bots.traits.${name} goes from 0 to 100`);
    traits[name] = v;
  }
}
const mixed = s.bots.mixed === true;
// Building: the wood, stone and metal each bot leaves the bus with, and how many times what its pickaxe harvests (needs the
// Fortnite-Offline-Server build of CoreV2). Without them, bots start with none and harvest as players do.
const materials = s.bots.materials ?? 0;
if (!within(materials, 0, 999)) fail("bots.materials goes from 0 to 999");
const harvestMultiplier = s.bots.harvest_multiplier ?? 1;
if (!within(harvestMultiplier, 1, 10)) fail("bots.harvest_multiplier goes from 1 to 10");
// Accounts with everything unlocked (every cosmetic and the Battle Pass maxed); the rest progress by playing.
const unlockAll = new Set((s.unlock_all ?? []).map((n) => String(n).toLowerCase()));

const dataDir = opt.data ?? path.join(here, "data");
fs.mkdirSync(dataDir, { recursive: true });
// settings.json wins over bot changes made in the admin panel.
const backendSettingsFile = path.join(dataDir, "settings.json");
const backendSettings = fs.existsSync(backendSettingsFile) ? readJson(backendSettingsFile) : {};
backendSettings.bots = {
  ...backendSettings.bots,
  forcePlayerAIs: s.bots.count > 0,
  count: s.bots.count,
  level: s.bots.level,
  botFightChance: s.bots.fight_chance,
  traits,
  mixed,
  startingMaterials: materials,
  harvestMultiplier
};
// Seconds on the warmup island before the bus (the game's default is 120); null or absent keeps the game's.
if (s.warmup_seconds != null) {
  if (!within(s.warmup_seconds, 10, 1800)) fail("warmup_seconds goes from 10 to 1800");
  backendSettings.warmupSeconds = s.warmup_seconds;
} else {
  backendSettings.warmupSeconds = null;
}
// Zero Build, a mode of its own: nobody builds (players or bots) and the bots gather no materials (CoreV2's own build).
backendSettings.zeroBuild = s.zero_build === true;
fs.writeFileSync(backendSettingsFile, JSON.stringify(backendSettings, null, 2));

process.env.FORTNITE_PS4_ACCOUNT = s.psn_account;
process.env.FORTNITE_FAST_QOS = "1";
// The PS4's performance mode (core-backend's cloudstorage.js ps4PerformanceSection): on unless set to false.
process.env.FORTNITE_PS4_PERFORMANCE = s.ps4_performance_mode === false ? "" : "1";
// The PS4's stretched-style high FOV (cloudstorage.js ps4StretchedFovSection): off unless set to true.
const FOV_MODES = new Set(["AspectRatio_MaintainYFOV", "AspectRatio_MajorAxisFOV", "AspectRatio_MaintainXFOV"]);
// The app's Echo 3.6 presets (1440x1080, 1279x1080, 1080x1080, wide112) are GoldHEN eboot patches instead: the stretch ones
// patch the MaintainXFOV branch of CalculateProjectionMatrixGivenView, so forcing MaintainYFOV here would skip them.
process.env.FORTNITE_PS4_STRETCHED_FOV = s.ps4_stretched_fov === true && FOV_MODES.has(s.ps4_fov_mode) ? s.ps4_fov_mode : "";
const backend = (f) => import(new URL(`./core-backend/src/${f}`, import.meta.url));
const { createServer } = await backend("server.js");
const { listen } = await backend("listen.js");
const { qosPingPort, startQosPingServer } = await backend("matchmaking/qos.js");

const open = !opt["pc-only"];
// fromCommandLine: the admin panel cannot change network access.
const server = await createServer({
  dataDir,
  port: s.port,
  remoteAccess: open,
  fromCommandLine: ["remoteAccess"],
  qosPingPort: 0,
  vivoxSipPort: 0,
  vivoxVoice: false
});

const logFile = path.join(dataDir, "ps4.log");
function note(text) {
  const line = `${new Date().toLocaleString("sv")} ${text}`;
  console.log(line);
  fs.appendFile(logFile, `${line}\n`, () => {});
}

const ipOf = (socket) => (socket.remoteAddress ?? "").replace(/^::ffff:/, "");
const isLocal = (ip) => ip === "::1" || ip.startsWith("127.");
const lan = s.lan === true;
const isPrivate = (ip) => /^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(ip);
const allowed = (ip) => isLocal(ip) || ps4s.has(ip) || (lan && isPrivate(ip));
const refused = new Set();
server.prependListener("connection", (socket) => {
  if (socket instanceof tls.TLSSocket) return; // tunnel of a connection that already passed the filter
  const ip = ipOf(socket);
  if (allowed(ip)) return;
  socket.destroy();
  if (refused.size < 256 && !refused.has(ip)) {
    refused.add(ip);
    note(`REFUSED ${ip}${auto ? "" : ' (if it is your PS4, set ps4_ip to "auto" or its IP in settings.json)'}`);
  }
});

// Live match telemetry (CoreV2 -> POST /fortnite/api/game/v2/matchmakingservice/telemetry, local-only).
// Kept in memory and served to the desktop app via GET /admin/api/match/live.
let liveMatch = null;
let liveUpdatedAt = 0;
let liveEvents = [];
let prevActors = new Map();
let liveRecordId = null;
let liveLastRecordAt = 0;
const MAX_TELEMETRY_BYTES = 512 * 1024;
const LIVE_STALE_MS = 10_000;
const RECORD_INTERVAL_MS = 500;
const matchesDir = path.join(dataDir, "matches");
// Commands for the match server from the desktop app's map ("teleportto\t<name>"), handed over in the answer to its next
// telemetry post (CoreV2 RunMapCommands). Old ones are dropped: the match server posts every 0.5 s.
let mapCommands = [];
const MAP_COMMAND_TTL_MS = 5_000;

function updateLiveTelemetry(data) {
  const now = Date.now();
  const actors = Array.isArray(data?.actors) ? data.actors : [];
  const phase = String(data?.phase ?? "Unknown");
  // Reset event history when a brand-new match starts (Setup/Warmup after another match or >60s gap).
  if (!liveMatch || now - liveUpdatedAt > 60_000 || (phase === "Warmup" && liveMatch.phase !== "Warmup" && liveMatch.phase !== "Setup")) {
    liveEvents = [];
    prevActors = new Map();
    liveRecordId = `match-${new Date(now).toISOString().replace(/[:.]/g, "-")}`;
    liveLastRecordAt = 0;
  }
  const timeStr = new Date().toTimeString().slice(0, 8);
  const nextActors = new Map();
  for (const a of actors) {
    const key = String(a.id ?? a.name ?? "");
    if (!key) continue;
    nextActors.set(key, a);
    const prev = prevActors.get(key);
    if (prev && prev.alive && !a.alive) {
      liveEvents.push({ t: timeStr, type: "elim", text: `${a.name || key} eliminado`, isBot: !!a.isBot });
    } else if (prev && (a.kills ?? 0) > (prev.kills ?? 0)) {
      liveEvents.push({ t: timeStr, type: "kill", text: `${a.name || key} suma ${a.kills} elim.`, isBot: !!a.isBot });
    }
  }
  if (liveEvents.length > 40) liveEvents = liveEvents.slice(-40);
  prevActors = nextActors;
  liveMatch = {
    phase,
    phaseStep: String(data?.phaseStep ?? ""),
    timeRemaining: Number(data?.timeRemaining ?? 0),
    playersLeft: Number(data?.playersLeft ?? actors.filter((a) => a.alive).length),
    totalPlayers: Number(data?.totalPlayers ?? actors.length),
    storm: data?.storm ?? null,
    bus: data?.bus ?? null,
    pois: Array.isArray(data?.pois) && data.pois.length ? data.pois : (liveMatch?.pois ?? []),
    actors
  };
  liveUpdatedAt = now;
  // Record real matches to data/matches/<matchId>.jsonl (skip when record: false, e.g. playback).
  if (data?.record !== false && actors.length > 0 && now - liveLastRecordAt >= RECORD_INTERVAL_MS) {
    liveLastRecordAt = now;
    try {
      fs.mkdirSync(matchesDir, { recursive: true });
      const rec = { ts: now, source: String(data?.source ?? "corev2"), ...liveMatch, pois: undefined };
      fs.appendFile(path.join(matchesDir, `${liveRecordId}.jsonl`), JSON.stringify(rec) + "\n", () => {});
    } catch { /* non-fatal */ }
  }
}

function handleLiveMap(req, res) {
  const from = ipOf(req.socket);
  const route = req.url.split("?")[0];
  if (route === "/fortnite/api/game/v2/matchmakingservice/telemetry" && req.method === "POST") {
    if (!isLocal(from)) {
      res.writeHead(403, { "Content-Type": "application/json" });
      res.end('{"error":"local only"}');
      return true;
    }
    let body = "";
    let tooBig = false;
    req.on("data", (chunk) => {
      if (tooBig) return;
      body += chunk;
      if (body.length > MAX_TELEMETRY_BYTES) tooBig = true;
    });
    req.on("end", () => {
      if (tooBig) {
        res.writeHead(413, { "Content-Type": "application/json" });
        res.end('{"error":"payload too large"}');
        return;
      }
      try {
        updateLiveTelemetry(JSON.parse(body || "{}"));
        const now = Date.now();
        const commands = mapCommands.filter((c) => now - c.at < MAP_COMMAND_TTL_MS).map((c) => c.line);
        mapCommands = [];
        res.writeHead(200, { "Content-Type": "text/plain; charset=utf-8" });
        res.end(commands.length ? `${commands.join("\n")}\n` : "");
      } catch (e) {
        res.writeHead(400, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ error: e.message }));
      }
    });
    return true;
  }
  if (route === "/admin/api/match/teleport" && req.method === "POST") {
    if (!isLocal(from)) {
      res.writeHead(403, { "Content-Type": "application/json" });
      res.end('{"error":"local only"}');
      return true;
    }
    let body = "";
    req.on("data", (chunk) => { if (body.length < 4096) body += chunk; });
    req.on("end", () => {
      let target = "";
      try {
        target = String(JSON.parse(body || "{}").target ?? "");
      } catch {}
      // A player or bot name as the match server sent it: printable ASCII, no tabs or line breaks.
      target = target.replace(/[^\x20-\x7e]/g, "").trim().slice(0, 64);
      if (!target) {
        res.writeHead(400, { "Content-Type": "application/json" });
        res.end('{"error":"target"}');
        return;
      }
      const active = Boolean(liveMatch && Date.now() - liveUpdatedAt < LIVE_STALE_MS);
      if (!active) {
        res.writeHead(409, { "Content-Type": "application/json" });
        res.end('{"error":"no match running"}');
        return;
      }
      mapCommands.push({ line: `teleportto\t${target}`, at: Date.now() });
      note(`Live map: teleport to ${target}`);
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end('{"ok":true}');
    });
    return true;
  }
  if (route === "/admin/api/match/live" && req.method === "GET") {
    if (!isLocal(from)) {
      res.writeHead(403, { "Content-Type": "application/json" });
      res.end('{"error":"local only"}');
      return true;
    }
    const ageMs = liveUpdatedAt ? Date.now() - liveUpdatedAt : null;
    const active = Boolean(liveMatch && ageMs !== null && ageMs < LIVE_STALE_MS);
    res.writeHead(200, { "Content-Type": "application/json", "Cache-Control": "no-store" });
    res.end(JSON.stringify({
      active,
      stale: Boolean(liveMatch && !active),
      updatedAt: liveUpdatedAt || null,
      ageMs,
      telemetry: liveMatch,
      events: liveEvents
    }));
    return true;
  }
  if (route === "/admin/api/match/live" && req.method === "DELETE") {
    if (!isLocal(from)) {
      res.writeHead(403, { "Content-Type": "application/json" });
      res.end('{"error":"local only"}');
      return true;
    }
    liveMatch = null;
    liveUpdatedAt = 0;
    liveEvents = [];
    prevActors = new Map();
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end('{"ok":true}');
    return true;
  }
  return false;
}

const existingRequestHandlers = server.listeners("request");
server.removeAllListeners("request");
server.on("request", (req, res) => {
  if (handleLiveMap(req, res)) return;
  for (const handler of existingRequestHandlers) handler(req, res);
});

// What the console asks for, without query strings or tokens.
let userAgent;
server.prependListener("request", (req, res) => {
  const from = ipOf(req.socket);
  // Marks a console's request for the backend (its PS4-only hotfix settings); never taken from the request itself.
  delete req.headers["x-fos-ps4"];
  if (ps4s.has(from)) req.headers["x-fos-ps4"] = "1";
  if (lan && !ps4s.has(from) && isPrivate(from) && req.method === "POST" && req.url.startsWith("/account/api/oauth/token")) {
    res.on("finish", () => { if (res.statusCode === 200) note(`PC player connected from ${from}`); });
  }
  if (!ps4s.has(from)) return;
  const route = req.url.split("?")[0].replace(/(sessions\/kill\/)[^/]+/, "$1***");
  if (req.headers["user-agent"] !== userAgent) {
    userAgent = req.headers["user-agent"];
    note(`PS4 user-agent: ${userAgent}`);
  }
  res.on("finish", () => note(`PS4 ${req.method} ${route} -> ${res.statusCode}`));
  // When the Battle Royale profile loads: an account in unlock_all gets everything.
  const profile = req.method === "POST" && /\/profile\/([0-9a-f]{32})\/client\/QueryProfile\?(?:.*&)?profileId=athena(?:&|$)/i.exec(req.url);
  if (profile) res.on("finish", () => { if (res.statusCode === 200) unlock(profile[1]); });
});

// The backend's own admin API (it only answers this PC).
async function admin(method, route) {
  const r = await fetch(`http://127.0.0.1:${s.port}/admin/api${route}`, {
    method,
    headers: { "Content-Type": "application/json" },
    body: method === "GET" ? undefined : "{}"
  });
  const json = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(json.errorMessage ?? `HTTP ${r.status}`);
  return json;
}
const unlocked = new Set();
async function unlock(id) {
  if (!unlockAll.size || unlocked.has(id)) return;
  unlocked.add(id);
  try {
    const account = await admin("GET", `/accounts/${id}`);
    const name = account.displayName ?? account.username ?? "";
    if (!unlockAll.has(name.toLowerCase())) return;
    await admin("POST", `/accounts/${id}/locker/grant-all`);
    const pass = await admin("POST", `/accounts/${id}/battle-pass/max`);
    note(`${name}: everything unlocked (all cosmetics, Battle Pass tier ${pass.tier}). Restart the game if you don't see it.`);
  } catch (e) {
    unlocked.delete(id);
    note(`Could not unlock everything for ${id}: ${e.message}`);
  }
}

try {
  await listen(server, server.network, s.port);
} catch (e) {
  fail(e.code === "EADDRINUSE" ? `port ${s.port} is already in use` : e.message);
}

// The PS4's XMPP: the game connects over TCP to xmpp-service-prod.ol.epicgames.com:5222 (the fnredir plugin resolves
// that name to this PC). It is passed as is to the backend, which serves XMPP on its own port (core-backend/src/xmpp).
const XMPP_PORT = 5222;
if (open) {
  const xmpp = net.createServer((incoming) => {
    const ip = ipOf(incoming);
    if (!allowed(ip)) return incoming.destroy();
    note(`XMPP connected from ${ip}`);
    const outgoing = net.connect(s.port, "127.0.0.1");
    incoming.pipe(outgoing).pipe(incoming);
    const close = () => { incoming.destroy(); outgoing.destroy(); };
    incoming.on("error", close).on("close", close);
    outgoing.on("error", close).on("close", close);
  });
  xmpp.on("error", (e) => note(`XMPP: could not open port ${XMPP_PORT} (${e.code ?? e.message})`));
  xmpp.listen(XMPP_PORT, "0.0.0.0");
}

// Fast QoS ping responder (UDP 22222): answers the console's datacenter check in ~1 ms instead of waiting ~90 s
// for Epic's dead ping servers to time out.
const qosPing = await startQosPingServer(
  { bindAddress: () => (open ? undefined : "127.0.0.1"), allows: (ip) => allowed(String(ip ?? "").replace(/^::ffff:/, "")) },
  qosPingPort,
  (text) => note(text)
);
if (qosPing.port) process.env.FORTNITE_QOS_PORT = String(qosPing.port);

console.log(`Backend on port ${s.port}. Bots: ${s.bots.count}, ${traits ? "custom traits" : `level ${s.bots.level}`}${mixed ? ", mixed personalities" : ""}${materials ? `, ${materials} of each material` : ""}${harvestMultiplier > 1 ? `, harvest x${harvestMultiplier}` : ""}.`);
console.log(`Admin panel: http://127.0.0.1:${s.port}/admin`);
if (open) {
  for (const i of Object.values(os.networkInterfaces()).flat()) {
    if (i.family === "IPv4" && !i.internal) console.log(`Address for the consoles: http://${i.address}:${s.port}`);
  }
  if (auto) {
    const search = async () => {
      const seen = await findPs4();
      const now = new Set(seen.map((p) => p.ip));
      for (const p of seen) if (!ps4s.has(p.ip)) note(`PS4 found: ${p.name} at ${p.ip}`);
      // A PS4 that stops answering is only forgotten when another one answers: in rest mode it may not reply.
      if (now.size) for (const ip of ps4s) if (!now.has(ip)) { ps4s.delete(ip); note(`PS4 no longer at ${ip}`); }
      for (const ip of now) ps4s.add(ip);
      return seen;
    };
    if (!(await search()).length) console.log("No PS4 seen yet; searching again every 30 s.");
    setInterval(search, 30_000).unref?.();
  } else {
    console.log(`Only the PS4 at ${s.ps4_ip} is accepted.`);
  }
  if (lan) note("LAN mode: PCs and consoles on the local network are accepted");
} else {
  console.log("PC-only mode: consoles cannot connect.");
}
console.log("READY");
