// Downloads Core-Backend (https://github.com/PongooDev/Core-Backend, GPL-3.0) at the commit this project was tested
// with, applies core-backend.patch (PS4 login and the max Battle Pass admin route) and installs its dependencies.
// Usage: node setup.mjs        (needs git and npm)
import { execFileSync, execSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const REPO = "https://github.com/PongooDev/Core-Backend.git";
const COMMIT = "4911b0a9eece3a131d336701f3137bcb05646caa";
const here = path.dirname(fileURLToPath(import.meta.url));
const dir = path.join(here, "core-backend");
const patch = path.join(here, "core-backend.patch");
// npm is npm.cmd on Windows, which needs a shell; the arguments here are fixed, never user input.
const run = (cmd, args, cwd = here) => (cmd === "npm"
  ? execSync(`npm ${args.join(" ")}`, { cwd, stdio: "inherit" })
  : execFileSync(cmd, args, { cwd, stdio: "inherit" }));

if (!fs.existsSync(path.join(dir, ".git"))) {
  console.log(`Cloning Core-Backend into ${dir}`);
  run("git", ["clone", REPO, "core-backend"]);
}
const head = execFileSync("git", ["rev-parse", "HEAD"], { cwd: dir, encoding: "utf8" }).trim();
const dirty = execFileSync("git", ["status", "--porcelain"], { cwd: dir, encoding: "utf8" }).trim();
if (head !== COMMIT || !dirty) {
  if (dirty) {
    console.error("core-backend has local changes on another commit; move it away and run setup again.");
    process.exit(1);
  }
  run("git", ["fetch", "origin", COMMIT], dir);
  run("git", ["checkout", "--quiet", COMMIT], dir);
  run("git", ["apply", patch], dir);
  console.log("Patch applied.");
} else {
  console.log("core-backend is already on the tested commit with local changes (assumed to be the patch).");
}
run("npm", ["ci", "--omit=dev"], dir);
if (!fs.existsSync(path.join(here, "settings.json"))) fs.copyFileSync(path.join(here, "settings.example.json"), path.join(here, "settings.json"));
console.log("\nDone. Start the server with:  node start.mjs   (or the desktop app)");
