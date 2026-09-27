# Expected mutation result

Line coverage of `shipping/rates.py` is 100 %. Mutation testing (TestMind's operators, all
mutants run) gives **20 mutants: 12 killed, 8 survived, score 60 %**. The golden test is
`tests/sandbox/test_mutation_engine.py`.

| Line | Mutant | Survives because |
|---|---|---|
| 3 | `weight <= 0` → `weight < 0` | no test uses a weight of 0 |
| 3 | `0` → `1` | same: nothing tests the lower bound |
| 6 | `weight > 10` → `weight >= 10` | no test sits on the 10 kg boundary |
| 6 | `10` → `11` | same boundary |
| 9 | `15` → `16` | the test only checks that express costs *more* |
| 10 | `round(cost, 2)` → `round(cost, 3)` | the tested prices have at most two decimals; for these inputs the mutant is **equivalent** (a known limit of mutation testing, not a missing test for every input) |
| 15 | `total >= 100` → `total > 100` | the 100 threshold is never tested |
| 15 | `100` → `101` | same threshold |

Every other mutant (arithmetic on the price formula, removing the discount or the surcharge,
`return None`, `or` → `and`) is killed by at least one test.
