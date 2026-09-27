# T-01 evidence

- `manifest-local.json`: written by `uv run crashcourt doctor --manifest research/evidence/t-01/manifest-local.json`
  on the developer machine, then committed.
- CI's `doctor` job writes `manifest-ci.json` and runs `--compare` against the committed file. Any drift in pinned
  fields (lockfile hash, package versions, image digests, Postgres settings, Toxiproxy version, Python minor) fails
  the job. This is control C7's first use.
