"""TestMind on BugsInPy: real bugs from real Python projects (docs/evaluation.md).

For every bug, BugsInPy gives the commit that fixed it, the fix itself (the source diff) and
the tests that fail before the fix and pass after it. The harness:

1. builds two trees from the fixed commit: `fixed`, and `buggy` with the source fix reversed
   (so both have the same, fixed tests) -- prepare.py;
2. runs both through a running TestMind stack, over its HTTP API, exactly as a user would --
   client.py;
3. compares what TestMind says with the fix: did the bug's tests fail before and pass after,
   and did TestMind blame the function the fix changed -- score.py.

What the fix changed (the ground truth) is computed here from the diff with Python's `ast`,
independently of TestMind's own analysers -- ground_truth.py.
"""
