"""Command-line interface for Polyguard."""

from __future__ import annotations

import argparse
from pathlib import Path

from .runner import render_report, run_checks


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="polyguard",
        description="Run available language test suites and fail closed when proof is missing.",
    )
    parser.add_argument("verify", nargs="?", choices=["verify"], default="verify")
    parser.add_argument("--path", default=".", help="Project directory to verify.")
    parser.add_argument("--timeout", type=int, default=120, help="Timeout per command in seconds.")
    parser.add_argument("--details", action="store_true", help="Show per-language diagnostics.")
    parser.add_argument("--json", action="store_true", help="Emit a machine-readable JSON report.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.timeout < 1:
        raise SystemExit("--timeout must be at least 1 second.")
    report = run_checks(Path(args.path), timeout=args.timeout)
    print(render_report(report, details=args.details, as_json=args.json))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
