# Polyguard

**Fail-closed verification for AI-generated code across multiple programming languages.**

Polyguard runs the real build and test tools for the languages it detects. It returns **PASS** only when every detected language has a recognizable test suite and all configured commands succeed. Missing tests, missing toolchains, timeouts, compilation errors, and failing tests return **FAIL**.

## Install

    python -m pip install polyglot-code-guard

## Use

Run this inside the project directory:

    polyguard verify

The normal verdict is intentionally one word:

    PASS

or:

    FAIL

For diagnostics:

    polyguard verify --details

For automation:

    polyguard verify --json

The process exits with code 0 for PASS and code 1 for FAIL.

## Initial language support

| Language | Test command |
| --- | --- |
| Python | compileall, then pytest |
| Rust | cargo test --all-targets |
| Go | go test ./... |
| JavaScript / TypeScript | npm test; TypeScript projects also run tsc --noEmit |
| Java | Maven test or Gradle test |
| C# | dotnet test |
| PHP | PHPUnit |
| Ruby | RSpec or Rake test |
| Swift | swift test |
| C / C++ | CMake build and CTest when CTest is configured |

The project must contain a detectable test suite for each detected language. Polyguard deliberately does not treat the absence of tests as a successful result.

## AI coding-agent skill

The repository includes SKILL.md with the mandatory agent workflow and output contract. The same skill instructions are included in the installed Python package as package data.

## Important limitations

Polyguard is a test-execution gate, not a proof of program correctness. The quality of a PASS depends on the requirements and coverage of the tests. It does not generate comprehensive tests automatically in this first release.

The verifier executes project build scripts and tests. Do not run untrusted code on a machine containing secrets or sensitive files. Use a disposable container or virtual machine with restricted permissions and no privileged host mounts.

Language detection and test-runner conventions vary across ecosystems. This alpha release uses conservative heuristics; unsupported project layouts may require explicit configuration in a future release.

## Development

    python -m pip install -e ".[test]"
    python -m pytest -q
    python -m build
    python -m twine check dist/*

## Publishing

The publish workflow uses PyPI Trusted Publishing. Configure a pending/normal publisher in PyPI for:
- Owner: Furox-Art
- Repository: polyglot-code-guard
- Workflow: publish.yml
- Environment: pypi

Then create a GitHub release to publish. No long-lived PyPI API token is stored in the repository.

## License

MIT
