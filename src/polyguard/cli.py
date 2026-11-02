"""Command-line interface for Polyguard."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import __version__
from .runner import render_report, run_checks


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="polyguard",
        description="Run project test suites and fail closed when verification is incomplete.",
    )
    parser.add_argument("verify", nargs="?", choices=["verify"], default="verify")
    parser.add_argument("--path", default=".", help="Project directory to verify.")
    parser.add_argument("--config", help="Path to a Polyguard TOML config (default: .polyguard.toml in project root).")
    parser.add_argument("--timeout", type=int, default=120, help="Timeout per command in seconds.")
    parser.add_argument("--details", action="store_true", help="Show per-language diagnostics.")
    parser.add_argument("--json", action="store_true", help="Emit a machine-readable JSON report.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = run_checks(Path(args.path), timeout=args.timeout, config_path=args.config)
    print(render_report(report, details=args.details, as_json=args.json))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
