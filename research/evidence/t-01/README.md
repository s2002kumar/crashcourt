# T-01 evidence

- `manifest-local.json`: written by `uv run crashcourt doctor --manifest research/evidence/t-01/manifest-local.json`
  on the developer machine from a clean checkout, then committed. While CI compared against it, a unit test failed if
  it was incomplete, written from a dirty tree, or not in the manifest schema of the time.
- CI's `doctor` job writes `manifest-ci.json` and runs `--compare` against the committed file. Any drift in pinned
  fields fails the job: lockfile hash, package versions, the full Python version and implementation, psycopg's
  implementation, libpq, image digests, Postgres settings, and the Toxiproxy version. This is control C7's first use.
- libpq is compared in full, except between two runs that used psycopg-binary's bundled libpq from different
  platform wheels (psycopg-binary 3.3.6 bundles libpq 18.4 in its Windows wheel and 18.6 in its Linux wheel). Then
  the major version must match and the difference is printed as a note on every comparison.
- Recorded but not compared: OS, machine, runner (local or a hosted runner), Docker and Compose versions, the Docker
  engine's kernel, and the running image IDs (engine-specific; doctor checks them against the pinned refs on the
  machine that ran it).
- A later task that changes a pinned field commits a new reference manifest in its own evidence folder and points
  CI at it, so this file stays the record of the T-01 environment. T-02 did so when it changed uv.lock.
