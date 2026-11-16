import { describe, expect, test } from "bun:test";
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { findDrift, hashText, parseManifest } from "./sync-ats";

const root = join(import.meta.dir, "..");
const manifest = parseManifest(readFileSync(join(root, "tools/ats-vendor.json"), "utf8"));

describe("vendored ats-resume-agent files", () => {
  test("match the pinned upstream commit", () => {
    expect(findDrift(root, manifest)).toEqual([]);
  });

  test("hashes ignore CRLF vs LF", () => {
    expect(hashText("a\r\nb\r\n")).toBe(hashText("a\nb\n"));
    expect(hashText("a\nb\n")).not.toBe(hashText("a\nc\n"));
  });

  test("a local edit or a deleted file is reported", () => {
    const dir = mkdtempSync(join(tmpdir(), "sync-ats-"));
    try {
      for (const path of Object.keys(manifest.files)) {
        mkdirSync(dirname(join(dir, path)), { recursive: true });
        copyFileSync(join(root, path), join(dir, path));
      }
      const [first, second] = Object.keys(manifest.files);
      writeFileSync(join(dir, first), `${readFileSync(join(dir, first), "utf8")}\n% local tweak\n`);
      rmSync(join(dir, second));
      expect(findDrift(dir, manifest)).toEqual([
        { path: first, kind: "modified" },
        { path: second, kind: "missing" },
      ]);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});

describe("parseManifest", () => {
  const base = { upstream: "u", ref: "r", commit: "c" };
  const hash = "0".repeat(64);

  test("rejects paths that leave the repo", () => {
    expect(() => parseManifest(JSON.stringify({ ...base, files: { "../x": hash } }))).toThrow(/inside the repo/);
    expect(() => parseManifest(JSON.stringify({ ...base, files: { "/etc/x": hash } }))).toThrow(/relative/);
  });

  test("rejects malformed hashes and empty file lists", () => {
    expect(() => parseManifest(JSON.stringify({ ...base, files: { a: "abc" } }))).toThrow(/sha256/);
    expect(() => parseManifest(JSON.stringify({ ...base, files: {} }))).toThrow(/no files/);
    expect(() => parseManifest(JSON.stringify({ ...base, ref: "", files: { a: hash } }))).toThrow(/ref/);
  });
});
