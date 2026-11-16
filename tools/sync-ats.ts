#!/usr/bin/env bun
// Keeps the files vendored from ats-resume-agent identical to the upstream
// commit recorded in tools/ats-vendor.json. ats-resume-agent is the one source
// of truth for the LaTeX templates and the validator/export tools; this repo
// carries copies so /apply works offline and on any OS, and this script makes
// any drift between the copies and the pinned upstream commit loud.
//
// Usage:
//   bun tools/sync-ats.ts check
//       Verify every vendored file matches the hash in the manifest.
//   bun tools/sync-ats.ts pull <path-to-ats-resume-agent-checkout> [--ref <git-ref>] [--dry-run]
//       Copy the vendored files from that checkout at <git-ref> (default: HEAD)
//       and re-pin the manifest to that commit. --dry-run reports what would
//       change without writing anything.
//
// Hashes are taken over LF-normalized text, so a Windows checkout with
// core.autocrlf=true (CRLF in the working tree) still matches.
//
// Exit codes: 0 clean, 1 drift found (check) or files changed (pull --dry-run),
// 2 usage, manifest, or git error.

import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";

export interface Manifest {
  upstream: string;
  ref: string;
  commit: string;
  files: Record<string, string>;
}

export type DriftKind = "missing" | "modified";

export interface Drift {
  path: string;
  kind: DriftKind;
}

const ROOT = resolve(import.meta.dir, "..");
const MANIFEST_PATH = join(ROOT, "tools", "ats-vendor.json");

export function hashText(text: string): string {
  return createHash("sha256").update(text.replace(/\r\n?/g, "\n"), "utf8").digest("hex");
}

export function parseManifest(raw: string): Manifest {
  const data: unknown = JSON.parse(raw);
  if (typeof data !== "object" || data === null) throw new Error("manifest is not a JSON object");
  const m = data as Record<string, unknown>;
  for (const key of ["upstream", "ref", "commit"] as const) {
    if (typeof m[key] !== "string" || !m[key]) throw new Error(`manifest field "${key}" must be a non-empty string`);
  }
  if (typeof m.files !== "object" || m.files === null || Array.isArray(m.files)) {
    throw new Error('manifest field "files" must be an object of path -> sha256');
  }
  const files: Record<string, string> = {};
  for (const [path, hash] of Object.entries(m.files as Record<string, unknown>)) {
    if (typeof hash !== "string" || !/^[0-9a-f]{64}$/.test(hash)) throw new Error(`manifest hash for ${path} is not a sha256 hex digest`);
    if (path.startsWith("/") || path.split("/").includes("..")) throw new Error(`manifest path ${path} must be relative and stay inside the repo`);
    files[path] = hash;
  }
  if (Object.keys(files).length === 0) throw new Error("manifest lists no files");
  return { upstream: m.upstream as string, ref: m.ref as string, commit: m.commit as string, files };
}

// Every vendored file whose LF-normalized content no longer matches the manifest.
export function findDrift(root: string, manifest: Manifest): Drift[] {
  const drift: Drift[] = [];
  for (const [path, expected] of Object.entries(manifest.files)) {
    const full = join(root, path);
    if (!existsSync(full)) {
      drift.push({ path, kind: "missing" });
      continue;
    }
    if (hashText(readFileSync(full, "utf8")) !== expected) drift.push({ path, kind: "modified" });
  }
  return drift;
}

function git(repo: string, args: string[]): string {
  return execFileSync("git", ["-C", repo, ...args], { encoding: "utf8", maxBuffer: 16 * 1024 * 1024, stdio: ["ignore", "pipe", "pipe"] });
}

function loadManifest(): Manifest {
  try {
    return parseManifest(readFileSync(MANIFEST_PATH, "utf8"));
  } catch (err) {
    console.error(`Cannot read ${MANIFEST_PATH}: ${(err as Error).message}`);
    process.exit(2);
  }
}

function check(): number {
  const manifest = loadManifest();
  const drift = findDrift(ROOT, manifest);
  const pin = `${manifest.ref} (${manifest.commit.slice(0, 12)})`;
  if (drift.length === 0) {
    console.log(`PASS: ${Object.keys(manifest.files).length} vendored files match ats-resume-agent ${pin}.`);
    return 0;
  }
  console.error(`FAIL: ${drift.length} vendored file(s) differ from ats-resume-agent ${pin}:`);
  for (const d of drift) console.error(`  ${d.kind.padEnd(8)} ${d.path}`);
  console.error("\nThese files are owned upstream. Make the change in ats-resume-agent, then run:");
  console.error("  bun tools/sync-ats.ts pull <path-to-ats-resume-agent> --ref <tag-or-commit>");
  return 1;
}

function pull(args: string[]): number {
  let repoArg: string | undefined;
  let ref: string | undefined = "HEAD";
  let dryRun = false;
  let bad = false;
  for (let i = 0; i < args.length; i++) {
    if (args[i] === "--ref") ref = args[++i];
    else if (args[i] === "--dry-run") dryRun = true;
    else if (args[i].startsWith("-") || repoArg !== undefined) bad = true;
    else repoArg = args[i];
  }
  if (bad || !repoArg || !ref || ref.startsWith("-")) {
    console.error("Usage: bun tools/sync-ats.ts pull <path-to-ats-resume-agent-checkout> [--ref <git-ref>] [--dry-run]");
    return 2;
  }
  const repo = resolve(repoArg);
  const manifest = loadManifest();

  let commit: string;
  const incoming: Record<string, string> = {};
  try {
    commit = git(repo, ["rev-parse", "--verify", `${ref}^{commit}`]).trim();
    for (const path of Object.keys(manifest.files)) {
      incoming[path] = git(repo, ["show", `${commit}:${path}`]).replace(/\r\n?/g, "\n");
    }
  } catch (err) {
    const stderr = (err as { stderr?: string }).stderr?.trim();
    console.error(`git failed in ${repo}: ${stderr || (err as Error).message}`);
    return 2;
  }

  const changed = Object.keys(incoming).filter((path) => {
    const full = join(ROOT, path);
    return !existsSync(full) || hashText(readFileSync(full, "utf8")) !== hashText(incoming[path]);
  });
  const pinMoved = commit !== manifest.commit;

  if (dryRun) {
    console.log(`ats-resume-agent ${ref} is ${commit.slice(0, 12)}; pinned ${manifest.commit.slice(0, 12)}${pinMoved ? " (differs)" : ""}.`);
    if (changed.length === 0) console.log("No vendored file would change.");
    for (const path of changed) console.log(`  would update ${path}`);
    return changed.length || pinMoved ? 1 : 0;
  }

  for (const path of changed) {
    const full = join(ROOT, path);
    if (!existsSync(dirname(full))) {
      console.error(`Refusing to create directory ${dirname(full)}; add the vendored path by hand first.`);
      return 2;
    }
    writeFileSync(full, incoming[path]);
  }
  const files: Record<string, string> = {};
  for (const path of Object.keys(manifest.files)) files[path] = hashText(incoming[path]);
  const updated: Manifest = { upstream: manifest.upstream, ref, commit, files };
  writeFileSync(MANIFEST_PATH, `${JSON.stringify(updated, null, 2)}\n`);
  console.log(`Pinned ats-resume-agent ${ref} (${commit.slice(0, 12)}); ${changed.length} file(s) updated.`);
  for (const path of changed) console.log(`  updated ${path}`);
  if (changed.length) console.log("Review the diff, then re-run the writer on a known job description before committing.");
  return 0;
}

if (import.meta.main) {
  const [command, ...rest] = process.argv.slice(2);
  if (command === "check") process.exit(check());
  if (command === "pull") process.exit(pull(rest));
  console.error("Usage: bun tools/sync-ats.ts check | pull <path-to-ats-resume-agent-checkout> [--ref <git-ref>] [--dry-run]");
  process.exit(2);
}
