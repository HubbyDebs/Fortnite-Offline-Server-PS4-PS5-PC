// Test without the game: this PC plays the PS4 (by its network IP) and the gameserver (by 127.0.0.1).
// It does not test the game, CoreV2 or the match's UDP traffic. Usage: node test.mjs
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const ips = Object.values(os.networkInterfaces()).flat().filter((i) => i.family === "IPv4" && !i.internal).map((i) => i.address);
const lan = ips.find((ip) => ip.startsWith("192.168.")) ?? ips[0];
if (!lan) throw new Error("this PC needs a network connection");
const base = JSON.parse(fs.readFileSync(path.join(here, "settings.example.json"), "utf8"));
const port = 5711;
const UA = "Fortnite/++Fortnite+Release-7.40-CL-4980899 PS4/";

let passed = 0;
let failed = 0;
function check(what, ok, extra = "") {
  if (ok) passed++;
  else failed++;
  console.log(`${ok ? "OK  " : "FAIL"} ${what} ${extra}`);
}

async function start(ps4Ip, ...more) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "fn-ps4-"));
  fs.writeFileSync(path.join(tmp, "s.json"), JSON.stringify({ ...base, ps4_ip: ps4Ip, port }));
  const p = spawn(process.execPath, [path.join(here, "start.mjs"), "--settings", path.join(tmp, "s.json"), "--data", tmp, ...more]);
  let output = "";
  await new Promise((resolve, reject) => {
    const t = setTimeout(() => {
      p.kill();
      reject(new Error(`does not start:\n${output}`));
    }, 90000);
    const read = (d) => {
      output += d;
      if (output.includes("READY")) {
        clearTimeout(t);
        resolve();
      }
    };
    p.stdout.on("data", read);
    p.stderr.on("data", read);
    p.on("exit", () => {
      clearTimeout(t);
      reject(new Error(`exited:\n${output}`));
    });
  });
  return {
    log: () => output,
    async stop() {
      const end = new Promise((r) => p.once("exit", r));
      p.kill();
      await end;
      fs.rmSync(tmp, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
    }
  };
}

async function request(host, route, { token, json, form, client = "ps4client:secret", ua = UA } = {}) {
  const headers = { "User-Agent": ua, Connection: "close" };
  let body;
  if (token) headers.Authorization = `bearer ${token}`;
  if (form) {
    body = new URLSearchParams(form).toString();
    headers["Content-Type"] = "application/x-www-form-urlencoded";
    headers.Authorization = `basic ${Buffer.from(client).toString("base64")}`;
  }
  if (json) {
    body = JSON.stringify(json);
    headers["Content-Type"] = "application/json";
  }
  const r = await fetch(`http://${host}:${port}${route}`, { method: body ? "POST" : "GET", headers, body, signal: AbortSignal.timeout(20000) });
  const t = await r.text();
  let b = t;
  try {
    b = JSON.parse(t);
  } catch { /* not JSON */ }
  return { s: r.status, b, t };
}

function openSocket(url, t) {
  const ws = new WebSocket(url, { headers: { Authorization: `Epic-Signed ${t.ticketType} ${t.payload} ${t.signature}`, "User-Agent": UA } });
  const queue = [];
  const waiting = [];
  ws.onmessage = (e) => {
    if (e.data === "pong") return;
    const m = JSON.parse(e.data);
    if (waiting.length) waiting.shift()(m);
    else queue.push(m);
  };
  async function until(name) {
    for (;;) {
      const m = queue.length ? queue.shift() : await new Promise((resolve, reject) => {
        const limit = setTimeout(() => reject(new Error(`${name} never arrived`)), 60000);
        waiting.push((v) => {
          clearTimeout(limit);
          resolve(v);
        });
      });
      if (m.name === name) return m;
    }
  }
  return { ws, until };
}

let srv = await start(lan);
const sockets = [];
try {
  const oauth = "/account/api/oauth/token";
  check("client token", (await request(lan, oauth, { form: { grant_type: "client_credentials" } })).s === 200);
  const login = await request(lan, oauth, { form: { grant_type: "external_auth", external_auth_type: "psn", external_auth_token: "SECRET-PSN" } });
  const t = login.b.access_token;
  const id = login.b.account_id;
  check("PS4 login (external_auth psn)", login.s === 200 && !!t && !!id, login.s);
  check("other external_auth refused", (await request(lan, oauth, { form: { grant_type: "external_auth", external_auth_type: "xbl", external_auth_token: "x" } })).s === 400);
  check("psn without token refused", (await request(lan, oauth, { form: { grant_type: "external_auth", external_auth_type: "psn" } })).s === 400);
  const pwd = await request(lan, oauth, { form: { grant_type: "password", username: "Tester@core.com", password: "12345678" } });
  check("email login (name@core.com)", pwd.s === 200, pwd.s);
  const all = await request(lan, oauth, { form: { grant_type: "password", username: "Everything@core.com", password: "12345678" } });
  await request(lan, `/fortnite/api/game/v2/profile/${all.b.account_id}/client/QueryProfile?profileId=athena&rvn=-1`, { token: all.b.access_token, json: {} });
  for (let i = 0; i < 160 && !/everything unlocked|Could not unlock/.test(srv.log()); i++) await new Promise((r) => setTimeout(r, 250));
  check("unlock_all account: all cosmetics and Battle Pass maxed", /Everything: everything unlocked/.test(srv.log()), (srv.log().match(/.*unlock.*/i) ?? [""])[0]);
  const locker = await request(lan, `/fortnite/api/game/v2/profile/${all.b.account_id}/client/QueryProfile?profileId=athena&rvn=-1`, { token: all.b.access_token, json: {} });
  const items = Object.keys(locker.b.profileChanges?.[0]?.profile?.items ?? {}).length;
  const attrs = locker.b.profileChanges?.[0]?.profile?.stats?.attributes ?? {};
  check("  locker filled and pass bought", items > 500 && attrs.book_purchased === true && attrs.book_level >= 100, `${items} items, tier ${attrs.book_level}`);
  check("verify", (await request(lan, "/account/api/oauth/verify", { token: t })).s === 200);
  const account = await request(lan, `/account/api/public/account/${id}`, { token: t });
  check("account", account.s === 200 && account.b.displayName === base.psn_account);
  check("lightswitch", (await request(lan, "/lightswitch/api/service/bulk/status?serviceId=Fortnite", { token: t })).s === 200);
  for (const profile of ["athena", "common_core"]) {
    const r = await request(lan, `/fortnite/api/game/v2/profile/${id}/client/QueryProfile?profileId=${profile}&rvn=-1`, { token: t, json: {} });
    check(`profile ${profile}`, r.s === 200 && r.b.profileChanges?.length > 0, r.s);
  }
  const routes = [["calendar", "/fortnite/api/calendar/v1/timeline"], ["item shop", "/fortnite/api/storefront/v2/catalog"], ["news", "/content/api/pages/fortnite-game"]];
  for (const [what, route] of routes) check(what, (await request(lan, route, { token: t })).s === 200);
  const files = await request(lan, "/fortnite/api/cloudstorage/system", { token: t });
  let ini = "";
  for (const f of files.b) ini += (await request(lan, `/fortnite/api/cloudstorage/system/${f.uniqueFilename}`, { token: t })).t;
  check("hotfix points XMPP at this PC", ini.includes(lan));

  const gs = await request("127.0.0.1", oauth, { form: { grant_type: "password", username: "CoreServer1", password: "Core" } });
  const st = gs.b.access_token;
  check("gameserver login", gs.s === 200);
  check("no gameserver login from the network", (await request(lan, oauth, { form: { grant_type: "password", username: "CoreServer2", password: "Core" } })).s === 403);
  const session = await request("127.0.0.1", "/fortnite/api/matchmaking/session", { token: st, json: {
    ownerId: gs.b.account_id, ownerName: "CoreServer1", serverName: "Core-1", serverAddress: "127.0.0.1", serverPort: 7777,
    maxPublicPlayers: 100, maxPrivatePlayers: 0, buildUniqueId: "5046157", attributes: { GAMEMODE_s: "FORTATHENA", REGION_s: "NA" }
  } });
  check("match session registered", session.s === 200);
  const mm = "/fortnite/api/game/v2/matchmakingservice/ticket";
  const ts = await request("127.0.0.1", `${mm}/session/${session.b.id}?bucketIds=${encodeURIComponent("5046157:0:NA:*")}&minPlayers=1&maxPlayers=100`, { token: st });
  const server = openSocket(`ws://127.0.0.1:${port}/`, ts.b);
  sockets.push(server.ws);
  await server.until("Registered");
  const tj = await request(lan, `${mm}/player/${id}?partyPlayerIds=${id}&bucketId=5046157:0:NA:playlist_defaultsolo&player.platform=PS4`, { token: t });
  check("PS4 ticket points at this PC", tj.s === 200 && String(tj.b.serviceUrl).includes(lan), tj.b.serviceUrl);
  const client = openSocket(`ws://${lan}:${port}/`, tj.b);
  sockets.push(client.ws);
  const assign = await server.until("AssignMatch");
  server.ws.send(JSON.stringify({ name: "AssignMatchResult", payload: { matchId: assign.payload.matchId, result: "ready" } }));
  const play = await client.until("Play");
  check("matchmaking complete", play.payload.sessionId === session.b.id);
  const where = await request(lan, `/fortnite/api/matchmaking/session/${session.b.id}`, { token: t });
  check("PS4 sent to this PC:7777", where.b.serverAddress === lan && where.b.serverPort === 7777, `${where.b.serverAddress}:${where.b.serverPort}`);
  check("join", (await request(lan, `/fortnite/api/matchmaking/session/${session.b.id}/join`, { token: t, json: {} })).s === 204);

  const ds = await request("127.0.0.1", oauth, {
    form: { grant_type: "client_credentials" },
    client: "313dfed3ec3023e52d3be9dda178bdfa:111aa434a6780d54f5cf759149639f20"
  });
  const w = await request("127.0.0.1", "/fortnite/api/game/v2/matchmakingservice/warmup", {
    token: ds.b.access_token,
    ua: "Core V2",
    json: { phase: "Warmup", accountIds: [id], secondsLeft: 100, playlistId: 0, playlistName: "Playlist_DefaultSolo" }
  });
  check("gameserver gets the bots", (w.b.bots?.count ?? 0) === base.bots.count, JSON.stringify(w.b.bots));
  check("admin panel closed to the network", (await request(lan, "/admin/api/matchmaking/overview")).s === 403);
  check("the PSN token is not logged", !srv.log().includes("SECRET-PSN"));
} catch (e) {
  check("walkthrough", false, e.stack);
} finally {
  for (const w of sockets) w.close();
  await srv.stop();
}

const closed = [["another LAN address is refused", "192.0.2.1"], ["--pc-only closes the network", lan, "--pc-only"]];
for (const [what, ps4Ip, ...more] of closed) {
  srv = await start(ps4Ip, ...more);
  try {
    let refused = false;
    try {
      await request(lan, "/core/api/status");
    } catch {
      refused = true;
    }
    check(what, refused);
    check("  and this PC still gets in", (await request("127.0.0.1", "/core/api/status")).s === 200);
  } finally {
    await srv.stop();
  }
}

console.log(`\n${passed} passed, ${failed} failed`);
process.exitCode = failed ? 1 : 0;
