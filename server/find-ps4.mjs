// Finds PS4/PS5 consoles on the local network with the PlayStation discovery protocol (UDP 987), the same one
// Remote Play and the PlayStation app use. Consoles answer it without GoldHEN.
// Standalone: node find-ps4.mjs
import dgram from "node:dgram";
import net from "node:net";
import os from "node:os";
import { fileURLToPath } from "node:url";

const PS_PORT = 987;
const SEARCH = "SRCH * HTTP/1.1\ndevice-discovery-protocol-version:00020020\n";

function broadcasts() {
  const out = new Set(["255.255.255.255"]);
  for (const i of Object.values(os.networkInterfaces()).flat()) {
    if (i.family !== "IPv4" || i.internal) continue;
    const ip = i.address.split(".").map(Number);
    const mask = i.netmask.split(".").map(Number);
    out.add(ip.map((b, n) => (b & mask[n]) | (~mask[n] & 255)).join("."));
  }
  return [...out];
}

function parse(text) {
  const [status, ...rest] = text.split(/\r?\n/);
  const fields = {};
  for (const l of rest) {
    const i = l.indexOf(":");
    if (i > 0) fields[l.slice(0, i).trim()] = l.slice(i + 1).trim();
  }
  return { status, fields };
}

// Resolves to [{ ip, name, type, awake }] (awake=false: in rest mode).
export function findPs4({ wait = 1500, tries = 2 } = {}) {
  return new Promise((resolve) => {
    const found = new Map();
    const sock = dgram.createSocket({ type: "udp4", reuseAddr: true });
    sock.on("error", () => { try { sock.close(); } catch {} resolve([]); });
    sock.on("message", (buf, info) => {
      const { status, fields } = parse(buf.toString("utf8"));
      const type = fields["host-type"] ?? "";
      if (!/^PS[45]$/i.test(type) || !net.isIPv4(info.address)) return;
      found.set(info.address, { ip: info.address, name: fields["host-name"] ?? type, type, awake: / 200 /.test(status) });
    });
    sock.bind(0, () => {
      sock.setBroadcast(true);
      const send = () => { for (const b of broadcasts()) sock.send(SEARCH, PS_PORT, b, () => {}); };
      send();
      for (let n = 1; n < tries; n++) setTimeout(send, (wait / tries) * n);
      setTimeout(() => { sock.close(); resolve([...found.values()]); }, wait);
    });
  });
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const list = await findPs4();
  if (!list.length) console.log("No PS4 found (it must be on, or in rest mode with network enabled).");
  for (const p of list) console.log(`${p.ip}  ${p.name}  ${p.type}  ${p.awake ? "on" : "rest mode"}`);
}
