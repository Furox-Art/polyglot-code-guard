# Polyguard

**A fail-closed verification gate for AI-generated code across multiple programming languages.**

Polyguard discovers supported project types, checks that a real test suite is present, executes the project's test/build commands, and reports a one-word verdict by default. It reports **PASS** only when every detected or explicitly required language has test evidence and every configured command exits successfully.

## Install

```bash
python -m pip install --upgrade polyglot-code-guard
```

## Run

From the target repository:

```bash
polyguard verify
```

Normal output is exactly one verdict:

```text
PASS
```

or:

```text
FAIL
```

Useful diagnostic modes:

```bash
polyguard verify --details
polyguard verify --json
polyguard verify --path ./my-project --timeout 180
polyguard verify --config ./ci/polyguard.toml
polyguard --version
```

Exit code is **0** for PASS and **1** for FAIL. A missing project, malformed configuration, missing test suite, missing toolchain, command error, or timeout fails closed.

## Configure project-specific test commands

Automatic detection is useful for conventional projects, but real repositories often use custom layouts and test runners. Put a `.polyguard.toml` file in the project root, or pass a file with `--config`.

Example:

```toml
[project]
# Every listed language is required for an overall PASS.
languages = ["python", "rust"]

[checks.python]
commands = [
  ["python3", "-m", "compileall", "-q", "src"],
  ["python3", "-m", "pytest", "-q"],
]
test_patterns = ["tests/test_*.py", "tests/**/*.py"]

[checks.rust]
commands = [["cargo", "test", "--all-targets"]]
test_patterns = ["tests/**/*.rs", "src/**/*.rs"]
```

Rules:
- `commands` is a non-empty list of argument arrays. Commands are launched without a shell.
- `test_patterns` must match at least one file inside the project root.
- A language under `[project].languages` is mandatory even if automatic detection misses it.
- A custom check overrides the built-in commands and test-file heuristic for that language.
- Patterns containing parent-directory traversal (`..`) are rejected.
- Do not use a command that merely prints success; it must execute the actual test suite.

A starter file is available as `.polyguard.example.toml`.

## Initial built-in language support

| Language | Default command |
| --- | --- |
| Python | `python -m compileall`, then `python -m pytest` |
| Rust | `cargo test --all-targets` |
| Go | `go test ./...` |
| JavaScript / TypeScript | `npm test`; TypeScript also runs `tsc --noEmit` when configured |
| Java | Maven `test` or Gradle `test` |
| C# | `dotnet test` |
| PHP | PHPUnit |
| Ruby | RSpec or Rake test |
| Swift | `swift test` |
| C / C++ | CMake build and CTest |

Automatic test evidence is detected using language-specific test names, declarations, or annotations. For unconventional frameworks, use `.polyguard.toml` to define the actual test commands and test file patterns.

## AI coding-agent skill

The repository contains `SKILL.md`. Add it to the coding agent's instruction/skills directory and require the agent to run:

```bash
polyguard verify --path . --details
```

Agents must not claim successful verification unless the command exits 0 and returns PASS. If any language fails, the overall result is FAIL. Agents must not delete, skip, or weaken tests to obtain PASS.

## Important limitations and security

Polyguard is a test execution gate, **not a formal proof of correctness**. PASS means the configured commands completed successfully; it does not guarantee comprehensive test coverage, correct requirements, or absence of defects. Tests can be weak or misleading. Test quality remains a separate responsibility.

The verifier executes project build scripts, package managers, and test commands. Treat repositories and dependencies as untrusted. Run them in a disposable container or VM without credentials, privileged access, or sensitive host mounts. Polyguard does not itself provide operating-system isolation.

Built-in language recognition is intentionally conservative. Use explicit configuration for monorepos, non-standard directory structures, custom test frameworks, or multiple packages in one repository.

## Development

```bash
python -m pip install -e ".[test]"
python -m pytest -q
python -m build
python -m twine check dist/*
```

## Publishing

The GitHub Actions workflow builds the distributions and publishes using PyPI Trusted Publishing. Configure the PyPI publisher for:
- Owner: `Furox-Art`
- Repository: `polyglot-code-guard`
- Workflow: `publish.yml`
- Environment: `pypi`

## License

MIT
