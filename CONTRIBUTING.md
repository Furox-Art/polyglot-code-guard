# Contributing

Thanks for helping improve Polyguard. This project verifies that tests actually ran, so
contributions are held to the same standard: no test may be skipped or weakened.

## Development setup

Requires Python 3.10 or newer (CI runs 3.10 through 3.13).

```bash
python -m pip install -e ".[test]"
```

The integration suite executes real compilers and test runners. It fails rather than
skips when a toolchain is unavailable, so install the ones you intend to exercise:

| Language | Toolchain |
| --- | --- |
| Python | `python` / `python3` |
| Rust | `cargo` |
| Go | `go` |
| JavaScript / TypeScript | `node` (18.15 or newer; `tsc` for type checking) |
| Java | JDK, `mvn` (or Gradle) |
| C# | `dotnet` |
| PHP | `php` with `mbstring`/`xml`/`dom`, `composer` |
| Ruby | `ruby` |
| Swift | Swift 6.2 toolchain |
| C / C++ | CMake, CTest, plus a C/C++ compiler |

## Running tests

```bash
# Full suite
python -m pytest -q

# One module
python -m pytest -q tests/test_runner.py

# Integration fixtures only
python -m pytest -q tests/integration/test_toolchain_fixtures.py
```

`tests/conftest.py` fails the whole session if any test is skipped.

## Coverage gate

CI enforces 100% line and branch coverage of Polyguard's own Python code
(`.github/workflows/tests.yml`). Reproduce the gate locally:

```bash
python -m pytest -q --cov=polyguard --cov-branch --cov-report=term-missing --cov-fail-under=100
```

`fail_under = 100` is also set in `pyproject.toml` under `[tool.coverage.report]`. New
branches need tests; do not lower the gate.

## Packaging checks

CI builds and validates the distributions on every push:

```bash
python -m build
python -m twine check dist/*
```

## Linting and local hooks

Optional git hooks are provided in `.pre-commit-config.yaml` (trailing whitespace, end of
file, YAML/TOML/JSON syntax, large files, merge conflicts, case conflicts, line endings,
and Ruff lint/format).

```bash
python -m pip install pre-commit
pre-commit install
pre-commit run --all-files
```

Ruff is currently a local-only hook: CI does not run Ruff, and the codebase is not yet
Ruff-clean, so expect findings before the first commit that touches a file.

## Pull request expectations

1. Add or update tests for behavior changes; keep the 100% coverage gate green.
2. Do not delete, skip, weaken, or rewrite a valid test to obtain PASS.
3. If your change affects verdict semantics, evidence parsing, or discovery, state the
   fail-closed behavior explicitly in the PR description.
4. Keep exit-code semantics: `0` only for PASS, `1` for FAIL.

## Reporting security issues

Do not open a public issue for a vulnerability; see [SECURITY.md](SECURITY.md).
