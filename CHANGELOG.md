# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- Fail closed on ambiguous runner evidence instead of inferring test execution.
- Reject skipped and empty test evidence; an all-skipped or zero-test run cannot produce PASS.
- Run only explicitly discovered Node test files, and correlate Node JUnit records with the
  pre-execution discovery snapshot.
- Require independent fail-closed test evidence per discovered package, so one package's
  evidence cannot cover a sibling package in a polyglot monorepo.

### Documentation

- Correct the zero-skip integration policy documentation.

## [v0.5.0] - 2026-11-07

### Changed

- **Python 3.10 support dropped.** `requires-python` is now `>=3.11` and the CI matrix runs 3.11, 3.12 and 3.13. No code change was needed: the package was already 3.10-compatible but the portfolio is consolidating on one support floor, and 3.10 reached end of life upstream. No 3.10 classifier was declared, so only the metadata, the matrix and the version changed.

## [v0.4.0] - 2026-11-03

### Added

- Native structured test-report validation for Java (Surefire/Gradle JUnit XML),
  C# (TRX), and PHP (PHPUnit JUnit XML), plus monorepo coverage across the supported
  language matrix.
- Vitest summary support as recognized test-execution evidence.

### Fixed

- Fail on skipped tests and require every detected toolchain instead of silently
  passing a partial verification.
- Reject symlinked project files during project discovery.
- Reject non-finite Go test durations.
- Terminate the timed-out command's process group/tree so runner descendants are not
  left behind.
- Bound captured command output and validate native test records.
- Detect freshly generated reports without relying on filesystem clock precision.

### Changed

- Document and enforce the runner evidence boundaries; a successful exit code alone is
  not accepted as proof that tests executed.

### Development

- Enforce 100% line and branch coverage in CI (`--cov-fail-under=100`).
- Verify that failing integration fixtures actually reach their assertions.
- Make the output-tail assertion newline portable.
- Fix native CI toolchain setup, install Swift in the Python test matrix, and pin the
  unit matrix to the Swift-supported Ubuntu runner. Swift fixtures use the available
  macOS runner.

[Unreleased]: https://github.com/Furox-Art/polyglot-code-guard/compare/v0.4.0...HEAD
[v0.4.0]: https://github.com/Furox-Art/polyglot-code-guard/releases/tag/v0.4.0
