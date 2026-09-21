from __future__ import annotations

import argparse
from pathlib import Path

from youtube_comment_research.evaluation import evaluate_gold_labels


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate the deterministic classifier against an annotated gold set."
    )
    parser.add_argument("gold_file", type=Path)
    parser.add_argument("--high-confidence-threshold", type=float, default=0.80)
    args = parser.parse_args()
    result = evaluate_gold_labels(
        args.gold_file,
        high_confidence_threshold=args.high_confidence_threshold,
    )
    print(result.to_json())


if __name__ == "__main__":
    main()
