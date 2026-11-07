# Usage

This page summarizes everyday use. The full description of supported languages, evidence
rules, and limitations lives in the [README](../README.md).

## Install

```bash
python -m pip install --upgrade polyglot-code-guard
```

## Verify a project

Run from the target repository root:

```bash
polyguard verify
```

Default output is one word:

```text
PASS
```

or:

```text
FAIL
```

Exit code is `0` for PASS and `1` for FAIL.

## Diagnostics and automation options

```bash
polyguard verify --details
polyguard verify --json
polyguard verify --path ./my-project --timeout 180
polyguard verify --config ./ci/polyguard.toml
polyguard --version
```

`--details` and `--json` include passed, failed, skipped, and total counts when the runner
report exposes them. Captured command output is streamed and limited to the final 256 KiB
per command. On timeout, the command's process group/tree is terminated.

## Custom configuration

Create `.polyguard.toml` in the project root, or pass one with `--config`:

```toml
[project]
languages = ["python"]

[checks.python]
commands = [
  ["python3", "-m", "compileall", "-q", "src"],
  ["python3", "-m", "pytest", "-q"],
]
test_patterns = ["tests/test_*.py", "tests/**/*.py"]
```

- `commands`: non-empty argument arrays, launched without a shell.
- `test_patterns`: at least one matching file must exist inside the project root.

Custom `success_patterns` are rejected. Custom commands may run for diagnostics, but they
cannot establish PASS: only the exact built-in command plan for the detected language
project produces trusted execution evidence.

## When does a check PASS?

A language check returns PASS only when all of the following hold:

1. The project and the required test suite are detected.
2. Every required command completes successfully.
3. The exact built-in command plan for the detected project ran.
4. A built-in runner summary or a freshly generated native structured report proves that at
   least one test passed.
5. No command failure or timeout occurs.

Missing tests, missing toolchains, invalid configuration, a zero-test run, an all-skipped
suite, an unrecognized success summary, a malformed or stale native report, or a non-zero
exit code all return FAIL.

## AI coding-agent workflow

Agents should run:

```bash
polyguard verify --path . --details
```

and must not report successful verification unless the command exits `0` and prints PASS.
Tests must never be deleted, skipped, or weakened to manufacture a passing result.

## Safety

Polyguard executes the target repository's build tools, package managers, and tests. Treat
repositories and dependencies as untrusted and run them in a disposable container or VM
without secrets, privileged access, or sensitive host mounts. Polyguard is not an
operating-system sandbox.
