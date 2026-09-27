# T-00 name check · 2026-09-26

Why rename: mstevens843/crashpoint already uses "crashpoint" for crash testing of the same runtimes.
Criteria: no collision among agent, durability, or testing tools; free on GitHub, PyPI, npm; signals
reliability evidence rather than speed; memorable to a recruiter and credible to a maintainer.

| Candidate | Evidence (web search, 2026-09-26) | Fit | Status |
| --- | --- | --- | --- |
| Forkline | PyPI `forkline` 0.5.0 (2026-02-27): replay tracing and diffing for agentic workflows; GitHub org `forkline` | Same space | Rejected |
| Ledgerline | PyPI `ledgerline` 0.3.3 (2026-06-13): finance pipeline exposing tools to AI agents over MCP; several GitHub repos | PyPI name taken | Rejected |
| Durabench | No public project found | "bench" implies a performance benchmark, which the spec rules out | Viable, with caveat |
| Crashcourt | No public project found | Verdicts, evidence, exhibits, witnesses: matches the product's vocabulary | Viable |
| Fencepost | Ruby gem `fencepost` 0.3.0 (Rails permissions); nothing found on PyPI or npm | Evokes fencing tokens; also the off-by-one "fencepost error" | Viable, with caveat |

Web search is not a registry check. Before creating the repo (network required):
`pip index versions <name>` or https://pypi.org/project/<name>/, `npm view <name>`, a GitHub name search, a domain lookup.

Recommendation: Crashcourt; Fencepost if a more sober tone is preferred.
Decision: **Crashcourt** (2026-09-26). Package and CLI: `crashcourt`.
