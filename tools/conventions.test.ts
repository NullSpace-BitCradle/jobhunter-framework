// jobhunter's MCD conventions (docs/mcd-conventions.md) checked against the
// vendored zero-fabrication validator. The validator ignores Agent Note text as
// evidence, so anything the writer is told to put on the page (a career-break
// reason, a crown-jewel metric) has to live in ordinary MCD content. These
// tests pin that down with the fictional example in examples/.

import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { validate } from "./validate";

const root = join(import.meta.dir, "..");
const read = (path: string) => readFileSync(join(root, path), "utf8").replace(/\r\n?/g, "\n");

const mcd = read("examples/career-document.example.md");
const resume = read("examples/resume.example.tex");
const template = read("templates/resume-template.tex");

const preamble = (tex: string) => tex.slice(0, tex.indexOf("\\begin{document}"));
const kinds = (r: ReturnType<typeof validate>) => r.findings.map((f) => `${f.kind}:${f.token}`);

describe("examples/resume.example.tex", () => {
  test("passes the validator against the example MCD", () => {
    const result = validate(resume, mcd);
    expect(result.findings).toEqual([]);
    expect(result.checked.headings).toBeGreaterThan(0);
    expect(result.checked.skills).toBeGreaterThan(0);
  });

  test("copies the template preamble verbatim", () => {
    expect(preamble(resume)).toBe(preamble(template));
  });

  test("shows the career break as its own entry", () => {
    expect(resume).toContain("\\headingBf{Career Break}{Sep 2022 - Present}");
    expect(resume).toContain("\\headingIt{Family caregiving}{}");
  });

  test("keeps the roles before the break", () => {
    for (const title of ["Charge Nurse", "Registered Nurse", "Patient Care Technician"]) {
      expect(resume).toContain(`\\headingIt{${title}}{}`);
    }
  });
});

describe("career break convention", () => {
  test("a reason that lives only in an Agent Note fails", () => {
    const noteOnly = mcd
      .replace("### Career Break -- Family caregiving", "### Career Break")
      .replace(
        "> **Agent Note:** Show this break as its own entry, reason exactly as written in the heading.",
        "> **Agent Note:** Show this break as its own entry with the reason Family caregiving.",
      );
    expect(kinds(validate(resume, noteOnly))).toContain("heading:Family caregiving");
  });

  test("break dates missing from the MCD fail", () => {
    const noDates = mcd.replace("**Dates:** Sep 2022 - Present", "");
    expect(kinds(validate(resume, noDates)).some((k) => k.startsWith("date:Sep 2022"))).toBe(true);
  });

  test("a break with no reason line passes when the MCD heading has none", () => {
    const bare = resume.replace("  \\headingIt{Family caregiving}{}\n", "");
    expect(validate(bare, mcd.replace("### Career Break -- Family caregiving", "### Career Break")).findings).toEqual([]);
  });
});

describe("crown jewel convention", () => {
  const work = [
    "## Work Experience",
    "",
    "### Charge Nurse -- Northfield Regional Hospital",
    "",
    "**Dates:** Mar 2016 - Aug 2022",
    "",
    "- Introduced barcode scanning double-checks for medication administration",
  ].join("\n");
  const summary = "\\begin{document}\n\\tinysection{Summary}\nCut medication administration errors by 40\\%.\n\\end{document}\n";

  test("a metric that lives only in the CROWN JEWEL note fails", () => {
    const noteOnly = `${work}\n\n> **Agent Note: CROWN JEWEL** Cut medication administration errors by 40%.\n`;
    expect(kinds(validate(summary, noteOnly))).toContain("number:40%");
  });

  test("the same metric passes once it is ordinary MCD content and the note points at it", () => {
    const pointed = `${work}\n- Cut medication administration errors by 40%\n\n> **Agent Note: CROWN JEWEL** The error reduction bullet above.\n`;
    expect(validate(summary, pointed).findings).toEqual([]);
  });
});
