"""Report how often the match scorer agrees with hand-labelled pairs.

    uv run python -m tests.eval.run_eval [--verbose] [--min-auc 0.9]

Uses the database in DATABASE_URL (load the seed data first) and the matching
settings from your .env, so you can tweak a weight and immediately see the effect.
"""

import argparse
import asyncio
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.config import get_settings
from app.db.session import create_engine, create_sessionmaker
from app.matching.candidates import connection_paths, load_profiles
from app.matching.scoring import score_pair
from app.services.matching import scoring_params

PAIRS = Path(__file__).parent / "pairs.json"


async def score_pairs(pairs: list[dict]) -> list[dict]:
    settings = get_settings()
    params = scoring_params(settings.matching)
    engine = create_engine(settings.database_url)
    now = datetime.now(UTC)
    results = []
    try:
        async with create_sessionmaker(engine)() as session:
            ids = {uuid.UUID(p[k]) for p in pairs for k in ("a", "b")}
            profiles = await load_profiles(session, ids)
            missing = ids - profiles.keys()
            if missing:
                sys.exit(f"{len(missing)} members from pairs.json are not in the database: load the seed data first")
            for p in pairs:
                a, b = uuid.UUID(p["a"]), uuid.UUID(p["b"])
                path = (await connection_paths(session, a)).get(b)
                s = score_pair(profiles[a], profiles[b], hops=path.hops if path else None,
                               path_strength=path.strength if path else 0.0, now=now, params=params)
                predicted = s.components.complementarity >= settings.matching.min_complementarity and s.score > 0
                results.append({**p, "score": s.score, "complementarity": s.components.complementarity,
                                "predicted": "good" if predicted else "bad"})
    finally:
        await engine.dispose()
    return results


def report(results: list[dict], verbose: bool) -> float:
    good = [r["score"] for r in results if r["label"] == "good"]
    bad = [r["score"] for r in results if r["label"] == "bad"]
    wins = sum(1.0 if g > b else 0.5 if g == b else 0.0 for g in good for b in bad)
    auc = wins / (len(good) * len(bad))
    agree = sum(r["predicted"] == r["label"] for r in results)

    print(f"pairs: {len(results)} ({len(good)} good, {len(bad)} bad)")
    print(f"mean score   good={sum(good) / len(good):.3f}   bad={sum(bad) / len(bad):.3f}")
    print(f"ranking agreement (AUC): {auc:.3f}")
    print(f"decision agreement:      {agree}/{len(results)} = {agree / len(results):.1%}")
    wrong = [r for r in results if r["predicted"] != r["label"]]
    if wrong:
        print("\ndisagreements:")
        for r in wrong:
            print(f"  labelled {r['label']:4s} score={r['score']:.3f} comp={r['complementarity']:.2f}  {r['note']}")
    if verbose:
        print("\nall pairs:")
        for r in sorted(results, key=lambda r: -r["score"]):
            print(f"  {r['label']:4s} {r['score']:.3f}  {r['note']}")
    return auc


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--min-auc", type=float, default=None, help="exit with status 1 if AUC is below this")
    args = parser.parse_args()
    results = asyncio.run(score_pairs(json.loads(PAIRS.read_text())))
    auc = report(results, args.verbose)
    if args.min_auc is not None and auc < args.min_auc:
        sys.exit(1)


if __name__ == "__main__":
    main()
