// Regenerates corev2.patch from a CoreV2 working copy (commits, uncommitted changes and new files that .gitignore doesn't
// exclude) against setup.mjs's base commit. The working copy's own index is left alone: a temporary one is used.
// Usage: node make-patch.mjs <path to the CoreV2 working copy>
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const COMMIT = fs.readFileSync(path.join(here, "setup.mjs"), "utf8").match(/const COMMIT = "([0-9a-f]{40})"/)[1];
const source = process.argv[2];
if (!source || !fs.existsSync(path.join(source, ".git"))) {
  console.error("Usage: node make-patch.mjs <path to the CoreV2 working copy>");
  process.exit(1);
}
const gitDir = execFileSync("git", ["rev-parse", "--absolute-git-dir"], { cwd: source, encoding: "utf8" }).trim();
const index = path.join(os.tmpdir(), `corev2-patch-index-${process.pid}`);
fs.copyFileSync(path.join(gitDir, "index"), index);
try {
  const env = { ...process.env, GIT_INDEX_FILE: index };
  const git = (args, opts = {}) => execFileSync("git", ["-c", "core.safecrlf=false", ...args], { cwd: source, env, maxBuffer: 256 * 1024 * 1024, ...opts });
  git(["add", "-A", "--", "."], { stdio: ["ignore", "ignore", "inherit"] });
  const diff = git(["diff", "--cached", "--binary", COMMIT]);
  fs.writeFileSync(path.join(here, "corev2.patch"), diff);
  const files = git(["diff", "--cached", "--name-only", COMMIT], { encoding: "utf8" }).trim().split("\n").filter(Boolean);
  console.log(`corev2.patch: ${diff.length} bytes, ${files.length} files, against ${COMMIT.slice(0, 7)}`);
} finally {
  fs.rmSync(index, { force: true });
}
