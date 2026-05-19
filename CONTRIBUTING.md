# Contributing

Thanks for the interest. Reading this means you might be about to spend your time, which I respect.

## Expectations

- **This is a personal project released as-is.** I work on it when it serves my own job search or someone opens an issue that catches my attention. There is **no committed review timeline** for PRs or issues.
- **No commercial support.** If you need guaranteed turnaround, fork it.
- **Open source spirit.** If you ship something useful on top, link back. Not required, just nice.

## Before you open an issue

1. Search existing issues - someone may have hit the same surface.
2. If filing a bug, include:
   - The slash command or CLI invocation that failed
   - The actual stderr / failure output (not paraphrased)
   - Which scraper / ATS / board was involved
   - `python --version`, OS, LaTeX distribution if relevant
3. If filing a feature request, describe the **role you wanted to pursue** and which framework surface fell short. Use cases beat abstract feature wishes.

## Pull requests

- Open against `main`. There is no `develop` branch.
- Keep PRs **single-purpose**. One feature or one fix per PR.
- Add tests. The `discovery/tests/` directory has examples; mirror the style.
- Run the existing suite locally and confirm it still passes:
  ```bash
  cd discovery
  python3 -m pytest -q
  ```
- Follow the hyphens-only convention. The framework refuses em-dashes, en-dashes, and double-hyphens in generated output; the repo itself is held to the same standard.
- If you touch a scraper, note the ATS / board version you tested against in the PR description. The aggregator path (JobSpy) breaks regularly when LinkedIn / Indeed change their HTML.

## What is out of scope

- New ATS scrapers that require login / auth. The framework is built around public APIs.
- Workday support is a known gap and welcome, but please file an issue first to discuss the approach - it is not API-shaped and needs careful design.
- Anything that exfiltrates a user's Master Career Document, tracker, or generated PDFs to a remote service. Privacy is a hard constraint.

## Code of Conduct

By participating in this project you agree to abide by the [Code of Conduct](CODE_OF_CONDUCT.md).
