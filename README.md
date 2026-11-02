# Polyguard

**Fail-closed verification for AI-generated code across multiple programming languages.**

Polyguard discovers supported project types, checks for a test suite, runs the relevant build/test commands, and returns a one-word verdict by default. A passing command exit code is not enough: Polyguard also requires a positive test-success summary from a recognized runner output format.

## Install

\`\`\`bash
python -m pip install --upgrade polyglot-code-guard
\`\`\`

## Run

From the target repository:

\`\`\`bash
polyguard verify
\`\`\`

Default output:

\`\`\`text
PASS
\`\`\`

or:

\`\`\`text
FAIL
\`\`\`

Diagnostics and automation:

\`\`\`bash
polyguard verify --details
polyguard verify --json
polyguard verify --path ./my-project --timeout 180
polyguard verify --config ./ci/polyguard.toml
polyguard --version
\`\`\`

Exit code is **0** for PASS and **1** for FAIL. Missing tests, missing toolchains, invalid configuration, a build/test failure, timeout, or missing positive test-execution evidence returns FAIL.

## Built-in language support

| Language | Default build/test plan | Positive execution evidence |
| --- | --- | --- |
| Python | \`compileall\`, then \`pytest -q\` | Pytest passed-test summary |
| Rust | \`cargo test --all-targets\` | Cargo test-result summary |
| Go | \`go test -json ./...\` | Passing test events in JSON stream |
| JavaScript / TypeScript | \`npm test\`; TypeScript also runs \`tsc --noEmit\` when configured | Recognized runner pass summary (including Vitest default/minimal, Jest, Mocha, and Node TAP formats) |
| Java | Maven `clean test` or Gradle `clean test` | Parsed Surefire/Gradle JUnit XML; at least one pass, zero failures |
| C# | dotnet test with a Polyguard TRX prefix | Parsed TRX counters; at least one pass, zero failures |
| PHP | PHPUnit with --log-junit | Parsed JUnit XML; at least one pass, zero failures |
| Ruby | RSpec, or Ruby's Minitest runner | RSpec/Minitest success summary |
| Swift | `swift test --parallel` | Recognized Swift Testing/XCTest runner summary with a positive passed-test count |
| C | CMake build and CTest | CTest positive test count |
| C++ | CMake build and CTest | CTest positive test count |

C and C++ are separate language identifiers in configuration. JavaScript and TypeScript share the npm project plan; TypeScript type checking does not replace running tests.

### Monorepos and workspaces

Polyguard recursively discovers independent package roots and runs each package in its own working directory. It also recognizes common aggregator manifests and delegates execution to the native workspace command once, avoiding duplicate child-package runs:

- Maven `<modules>` and Gradle `settings.gradle(.kts)`
- Cargo `[workspace].members`
- npm `workspaces`
- .NET solution (`.sln`) files

Each independently discovered project is reported separately. A failure or incomplete test report in any required project makes the overall result FAIL. For workspace aggregators, native package managers remain responsible for workspace dependency resolution and execution.

## Configure custom test commands

For non-standard project layouts or test runners, create \`.polyguard.toml\` in the project root or pass one with \`--config\`.

\`\`\`toml
[project]
languages = ["python"]

[checks.python]
commands = [
  ["python3", "-m", "compileall", "-q", "src"],
  ["python3", "-m", "pytest", "-q"],
]
test_patterns = ["tests/test_*.py", "tests/**/*.py"]
\`\`\`

Configuration fields:
- \`commands\`: non-empty argument arrays. Commands are launched without a shell.
- \`test_patterns\`: at least one matching file must exist inside the project root.

**Evidence boundary:** custom \`success_patterns\` are rejected. An arbitrary output regex can be satisfied by a dummy command and cannot establish that tests executed. Custom commands may execute for diagnostics, but they cannot establish PASS: their stdout and generated reports are not trusted execution evidence. PASS requires the exact built-in command plan for the detected language project. A custom command adapter is not currently supported.

## Test evidence and verdict rules

A language check returns PASS only when:
1. The project and required test suite are detected.
2. Every required command completes successfully.
3. The exact built-in command plan for the detected project ran; custom commands cannot establish PASS.
4. A built-in runner summary or a freshly generated native structured test report proves that at least one test passed.
5. No command failure or timeout occurs.

A zero-test run, an all-skipped suite, an output with no recognized success summary, inconsistent report counters, a malformed or stale native report, or a non-zero exit code returns FAIL. Java and PHP require freshly generated JUnit XML; C# requires a fresh TRX report. Swift uses a positive test count from the SwiftPM runner summary because Swift Testing does not produce a usable xUnit report in this workflow. A successful command exit alone is insufficient. C# currently targets the VSTest TRX logger contract; Microsoft.Testing.Platform projects that do not emit this TRX report fail closed until an MTP-specific adapter is configured. JSON and detailed output include passed, failed, skipped, and total counts when the runner report exposes them.

## Integration tests

The repository includes disposable real-project fixtures for all eleven supported language identifiers, including real Java/JUnit, C#/.NET, PHP/PHPUnit, and SwiftPM/Swift Testing projects. The suite also covers mixed-language and native-workspace monorepos. Run:

\`\`\`bash
python -m pip install -e ".[test]"
python -m pytest -q
\`\`\`

Integration fixtures are created in temporary directories and run through Polyguard's actual project discovery and command execution path. Missing local toolchains are skipped by local integration tests; required CI jobs install their toolchains explicitly. The CI workflow also runs runner unit tests on Linux, Windows, and macOS, plus a native SwiftPM fixture on macOS.

## AI coding-agent skill

The repository includes \`SKILL.md\`, also shipped in the installed package. Agents should run:

\`\`\`bash
polyguard verify --path . --details
\`\`\`

Agents must not report successful verification unless the command exits 0 and returns PASS. They must not delete, skip, or weaken tests to manufacture a passing result.

## Important limitations and security

Polyguard is a test-execution gate, **not a formal proof of program correctness**. PASS means that the built-in command plan succeeded and positive test-pass evidence was recognized. It does not guarantee adequate test coverage, correct requirements, or absence of defects.

The verifier executes project build scripts, package managers, and tests. Treat repositories and dependencies as untrusted. Run them in a disposable container or VM without secrets, privileged access, or sensitive host mounts. Polyguard does not itself provide operating-system isolation.

Automatic language discovery is conservative. Custom frameworks can be configured for diagnostic execution, but custom commands cannot establish PASS. Only the built-in command plan currently produces trusted execution evidence. Polyguard discovers supported package manifests recursively and executes each discovered language package from its own project root. Multiple packages using the same language are reported separately; the overall result is FAIL if any package fails. A root-level .polyguard.toml applies its language configuration to each matching package, with test patterns evaluated relative to that package root. Workspace-specific dependency orchestration remains the responsibility of the native package manager.

## Development

\`\`\`bash
python -m pip install -e ".[test]"
python -m pytest -q
python -m build
python -m twine check dist/*
\`\`\`

## Publishing

GitHub Actions builds the distributions and publishes with PyPI Trusted Publishing. Configure the PyPI publisher for:
- Owner: \`Furox-Art\`
- Repository: \`polyglot-code-guard\`
- Workflow: \`publish.yml\`
- Environment: \`pypi\`

## License

MIT
