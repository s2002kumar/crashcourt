"""Experiment control (T-13): the pre-registration, the gate in front of every pre-registered batch,
the proof that a batch ran as pre-registered, and the cell summaries built from proven batches only.

- preregistration: research/preregistration.md's model and its binding JSON block;
- gate: `authorize_batch`, called before a batch runs, and `confirm_environment` after doctor;
- verify: re-proving an archived batch against git;
- fingerprint: which manifest fields make two batches materially different;
- aggregate: cells.json, never pooling batches that differ.
"""
