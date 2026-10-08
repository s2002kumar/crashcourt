# T-01 evidence

- `manifest-local.json`: written by `uv run crashcourt doctor --manifest research/evidence/t-01/manifest-local.json`
  on the developer machine from a clean checkout, then committed.
- CI's `doctor` job writes `manifest-ci.json` and runs `--compare` against the committed file. Any drift in pinned
  fields fails the job: lockfile hash, package versions, the full Python version and implementation, psycopg's
  implementation, libpq's major version, image digests, Postgres settings, and the Toxiproxy version. This is
  control C7's first use.
- Recorded but not compared: OS, machine, runner (local or a hosted runner), Docker and Compose versions, the
  Docker engine's kernel, the running image IDs (engine-specific; doctor checks them against the pinned refs on
  the machine that ran it), and libpq's minor version (psycopg-binary 3.3.6 bundles libpq 18.4 in its Windows wheel
  and 18.6 in its Linux wheel).
