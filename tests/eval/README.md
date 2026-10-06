# Match evaluation set

`pairs.json` holds 49 member pairs from the seed data, each labelled `good` (one can
genuinely help the other) or `bad` (they can't), with a note saying why. Labels were
assigned by reading the actual need and offer texts, not by running the scorer.

- **Good (26):** the 6 deliberate cross-sector bridge cases, the 6 legs of the two
  exchange loops, and a spread of founder/mentor/student/investor pairs where one
  side's offer meets the other's need (including a student and a hiring founder).
- **Bad (23):** mostly "hard negatives": people who look alike (connected,
  classmates, shared traits, same role) but have nothing to offer each other.

Run it against a database loaded with the seed data (real embeddings recommended):

    uv run python -m tests.eval.run_eval
    uv run python -m tests.eval.run_eval --verbose      # every pair with its score
    uv run python -m tests.eval.run_eval --min-auc 0.9  # exit 1 below this (for CI)

Metrics:
- **Ranking agreement (AUC):** of all (good, bad) combinations, how often the good pair
  scores higher. 1.0 = perfect, 0.5 = coin toss.
- **Decision agreement:** how often the engine's own "is this a match?" rule
  (complementarity >= MATCHING__MIN_COMPLEMENTARITY and score > 0) matches the label.

If you regenerate the seed data, member ids change: rebuild the pairs.
