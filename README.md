# jobhunter-framework

> Unified, AI-powered job-hunting pipeline built on [Claude Code](https://docs.anthropic.com/en/docs/claude-code).

Discover relevant roles, tailor resumes with zero fabrication, and track applications end-to-end.

## What it includes

- **Career Document Builder** - interactive interview that produces a structured Master Career Document (MCD), the single source of truth for your career history
- **Job Discovery** - Python scanner that pulls from two complementary source types:
 - **Direct ATS scrapers** - Greenhouse, Lever, Ashby, SmartRecruiters, Workable
 - **Job board aggregator** - LinkedIn, Indeed, Glassdoor, Google, ZipRecruiter (via [python-jobspy](https://github.com/speedyapply/jobspy))
 
 Cross-source deduplication ensures the same posting found on both an ATS and a job board appears only once (ATS version preferred). Results are filtered by keyword, location, remote requirement, and anti-target rules derived from your MCD, then written to a ranked markdown digest
- **Resume Writer** - generates ATS-optimized LaTeX resumes and cover letters tailored to each job description, with a hard zero-fabrication policy
- **Orchestration** - Claude Code slash commands that chain the pieces into one smooth workflow:
 - `/discover` - run a scan and show the top new matches
 - `/apply <url|company>` - end-to-end: fetch the JD, tailor resume + cover letter, log the application
 - `/triage` - classify recruiter mail in the job-hunt inbox, update the tracker, schedule interviews
 - `/sync-filters` - regenerate discovery anti-target filters from the MCD's Anti-Target Lanes section
- **Application tracker** - a plain-markdown `applications.md` file that records every application, status change, and interview

## Status

Actively-developed framework. Roadmap:

- [x] Phase 1 - scaffold + clean code/content separation
- [x] Phase 2 - unified config + `config.yaml`-driven paths + user-data/code separation
- [x] Phase 3 - `/discover`, `/apply`, `/triage` commands + `applications.md` tracker schema
- [x] Phase 3.5 - job board aggregator (LinkedIn via JobSpy) + cross-source deduplication
- [ ] Phase 4 - tests, agent config-driven paths (no-symlinks), polish, public release

## Prerequisites

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code)
- Python 3.10+ (for job discovery; 3.10 is the minimum required by python-jobspy)
- `pdflatex` with the following TeX Live packages (beyond base `texlive-latex-base`):
 - `texlive-latex-recommended` - provides `ragged2e`, `microtype`
 - `texlive-latex-extra` - provides `tabularx`, `enumitem`, `titlesec`, etc.
 - `texlive-fonts-extra` - provides `fontawesome5`, `CormorantGaramond`, `charter`

**Debian/Ubuntu/WSL:**
```bash
sudo apt-get install texlive-latex-base texlive-latex-recommended \
 texlive-latex-extra texlive-fonts-recommended texlive-fonts-extra
```

**macOS:**
```bash
brew install --cask mactex-no-gui
```

If `pdflatex` complains about a missing `.sty` file, install the individual package via `tlmgr install <package>` or `tlmgr --usermode install <package>` (no root required).

## Install

```bash
git clone https://github.com/NullSpace-BitCradle/jobhunter-framework.git
cd jobhunter-framework
cp config.example.yaml config.yaml # customize paths
```

### Job Discovery

```bash
cd discovery
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Copy the example configs and customize for your target companies/keywords
cp config/companies.example.yaml config/companies.yaml
cp config/keywords.example.yaml config/keywords.yaml
cp config/filters.example.yaml config/filters.yaml

# Verify your company slugs resolve
python main.py --verify

# Run a real scan - writes a ranked digest to output/digest-YYYY-MM-DD.md
python main.py

# Dry run (no state updates)
python main.py --dry-run

# Verbose logging
python main.py -v
```

#### Job board search (JobSpy)

By default, discovery only scrapes direct ATS boards. To also search LinkedIn (and optionally Indeed, Glassdoor, etc.), enable the `jobspy` section in `config/keywords.yaml`:

```yaml
jobspy:
 enabled: true
 sites:
 - linkedin
 location: "USA"
 results_wanted: 50
 hours_old: 72
```

If no `search_term` is set, one is auto-generated from your `domain_keywords_in_title`. Jobs found on both a direct ATS and a job board are automatically deduplicated - the ATS version (with richer descriptions and direct apply links) is always preferred.

#### Deduplication

Discovery deduplicates at two levels:

1. **Within-source** - each job gets a unique ID (`{ats}:{slug}:{id}` for ATS, `jobspy:{site}:{hash}` for boards). IDs are tracked in `state/seen-jobs.json`; jobs seen on prior runs are filtered out.
2. **Cross-source** - when the same role appears on both an ATS and a job board, fuzzy matching on normalized company name + title keeps only the ATS version.

### Resume Writing

The resume writer is a Claude Code sub-agent. From the repo root:

```bash
claude
```

With your Master Career Document present and a job description file in place (`Job_Description-[Company]-[Role].md`), ask Claude:

> "Resume and cover letter for the [Company] file"

Claude invokes the `ats-resume-writer` agent, produces a tailored `.tex`, compiles it to PDF via `pdflatex`, and cleans up auxiliary files.

## Master Career Document

The resume writer uses a single Master Career Document (MCD) as the source of truth. Use the `career-doc-builder` agent for a guided interview:

```
Help me build my career document
```

It produces an 18-section MCD covering positioning, skills, work history, metrics, and customization guidance. Reuse the same MCD every time you generate a resume - no re-interviewing per application.

## Project structure

```
jobhunter-framework/
├── .claude/
│ ├── agents/ # Claude Code sub-agents
│ │ ├── ats-resume-writer.md
│ │ └── career-doc-builder.md
│ └── commands/ # Slash commands (/discover, /apply, /triage, /sync-filters)
├── discovery/
│ ├── main.py # Orchestrator - scan, filter, score, write digest
│ ├── dedup.py # Cross-source deduplication (ATS vs board)
│ ├── scrapers/
│ │ ├── base.py # Job dataclass + Scraper base class
│ │ ├── greenhouse.py # Greenhouse ATS
│ │ ├── lever.py # Lever ATS
│ │ ├── ashby.py # Ashby ATS
│ │ ├── smartrecruiters.py # SmartRecruiters ATS
│ │ ├── workable.py # Workable ATS
│ │ └── jobspy.py # Job board aggregator (LinkedIn, Indeed, etc.)
│ ├── config/ # YAML configs (companies, keywords, filters)
│ ├── tests/ # Unit tests (35 tests across all scrapers + dedup)
│ ├── state/ # Seen-jobs dedup state (created on first run)
│ └── output/ # Daily digest markdown files
├── templates/ # LaTeX resume + cover letter templates
├── config.example.yaml # Framework config template
├── applications.template.md # Application tracker schema
└── CLAUDE.md # Claude Code project instructions
```

## Zero-fabrication policy

The resume writer will never estimate metrics, fabricate experience, or generalize beyond what is in your MCD. If a quantified achievement isn't in the source document, it won't appear in the output. This is a hard constraint.

## License

MIT for the code. The LaTeX resume template is CC-BY-4.0 (based on work by Michael Lustfield). See [LICENSE](LICENSE) for details.
