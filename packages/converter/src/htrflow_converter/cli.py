"""``htrflow-campaigns`` CLI: validate a campaigns repo (spec §3)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .parse import ValidationError, load


def _validate(repo_dir: str) -> int:
    repo = Path(repo_dir)
    try:
        load(repo / "campaigns", repo / "pipelines", repo / "converter.yaml")
    except ValidationError as e:
        for problem in e.problems:
            print(problem)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="htrflow-campaigns")
    sub = parser.add_subparsers(dest="command", required=True)
    validate_p = sub.add_parser("validate", help="validate campaigns/ and pipelines/")
    validate_p.add_argument("repo_dir")
    args = parser.parse_args(argv)
    # "validate" is the only registered subcommand and add_subparsers(required=True)
    # rejects anything else, so this is the only reachable command.
    return _validate(args.repo_dir)


if __name__ == "__main__":
    sys.exit(main())
