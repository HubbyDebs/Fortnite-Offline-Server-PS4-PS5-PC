// Downloads CoreV2 (https://github.com/PongooDev/CoreV2, GPL-3.0) at the commit this project's bots are built on and
// applies corev2.patch (the player bots: navigation, storm, looting, fights, match stats; navmesh cache; server stutter
// and network fixes). The result in corev2/src builds Core.dll, CoreClient.dll and CoreRedirect.dll.
// Usage: node setup.mjs           (needs git)
//        node setup.mjs --build   (also builds Release x64 with Visual Studio 2022's MSBuild)
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const REPO = "https://github.com/PongooDev/CoreV2.git";
const COMMIT = "e1330694f2a71adb6fd943f1a70bf2df2ea47da1";
const here = path.dirname(fileURLToPath(import.meta.url));
const dir = path.join(here, "src");
const patch = path.join(here, "corev2.patch");
const run = (cmd, args, cwd = here) => execFileSync(cmd, args, { cwd, stdio: "inherit" });
const out = (args, cwd = dir) => execFileSync("git", args, { cwd, encoding: "utf8" }).trim();

// core.autocrlf off: the patch is made against the repository's own line endings.
if (!fs.existsSync(path.join(dir, ".git"))) {
  console.log(`Cloning CoreV2 into ${dir}`);
  run("git", ["-c", "core.autocrlf=false", "clone", "--no-checkout", REPO, "src"]);
  run("git", ["config", "core.autocrlf", "false"], dir);
}
// An earlier patch (or edits) in src is reset to the base commit, so the current patch is what gets built.
run("git", ["fetch", "--quiet", "origin", COMMIT], dir);
run("git", ["checkout", "--quiet", "--force", COMMIT], dir);
run("git", ["submodule", "update", "--init", "--recursive", "--quiet"], dir);
run("git", ["apply", "--whitespace=nowarn", patch], dir);
console.log("Patch applied.");

if (process.argv.includes("--build")) {
  // Visual Studio 2022 (v143) only: Visual Studio 18's compiler doesn't match the projects' precompiled headers.
  const vswhere = path.join(process.env["ProgramFiles(x86)"] || "C:\\Program Files (x86)", "Microsoft Visual Studio", "Installer", "vswhere.exe");
  const msbuild = execFileSync(vswhere, ["-version", "[17.0,18.0)", "-products", "*", "-requires", "Microsoft.Component.MSBuild", "-find", "MSBuild\\**\\Bin\\MSBuild.exe"], { encoding: "utf8" })
    .split(/\r?\n/).find(Boolean);
  if (!msbuild) {
    console.error("Visual Studio 2022 (or its Build Tools) with MSBuild was not found.");
    process.exit(1);
  }
  run(msbuild, ["Core.sln", "/m", "/nologo", "/v:minimal", "/p:Configuration=Release", "/p:Platform=x64"], dir);
  console.log(`\nBuilt: ${path.join(dir, "x64", "Release")}  (point settings.json core_dir there to use it)`);
}
