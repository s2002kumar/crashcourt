# C5: saver mutants under checker v1

Threshold: caught in at least 19 of 20 trials. Result: FAIL.

| Mutant | Defect | Target | Caught | Valid | Target's outcomes | Other anomalies |
| --- | --- | --- | --- | --- | --- | --- |
| M1 | a put_writes call that writes two or more rows does not store its second row | I7a | 0 of 20 | 20 | NO_ANOMALY_UNSPECIFIED 20 | none |
| M2 | get_tuple by checkpoint id returns None | I5a | 0 of 20 | 20 | NO_ANOMALY_UNSPECIFIED 20 | none |
| M3 | put stores no parent | I5a | 20 of 20 | 20 | GAP 20 | I8 20 |
| M4 | the second put of each op is written in a transaction whose commit fails; the error is swallowed and the put reports success | I1b | 20 of 20 | 20 | VIOLATION_CANDIDATE 20 | I5b 20, I8 20 |
| M5 | the latest-checkpoint read returns the head's parent | I6b | 0 of 20 | 0 | INVALID 20 | none |

- M1 was caught by I7a in 0 of 20 trials; C5 needs at least 19 of 20
- M2 was caught by I5a in 0 of 20 trials; C5 needs at least 19 of 20
- M5 was caught by I6b in 0 of 20 trials; C5 needs at least 19 of 20
