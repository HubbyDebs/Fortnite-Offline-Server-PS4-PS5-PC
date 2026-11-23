// Fortnite 7.40 backend for PS4 (and LAN PCs). Usage: node start.mjs [--pc-only] [--settings file] [--data dir]
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
  mixed
};
fs.writeFileSync(backendSettingsFile, JSON.stringify(backendSettings, null, 2));

process.env.FORTNITE_PS4_ACCOUNT = s.psn_account;
const backend = (f) => import(new URL(`./core-backend/src/${f}`, import.meta.url));
const { createServer } = await backend("server.js");
const { listen } = await backend("listen.js");

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

// What the console asks for, without query strings or tokens.
let userAgent;
server.prependListener("request", (req, res) => {
  const from = ipOf(req.socket);
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

console.log(`Backend on port ${s.port}. Bots: ${s.bots.count}, ${traits ? "custom traits" : `level ${s.bots.level}`}${mixed ? ", mixed personalities" : ""}.`);
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
