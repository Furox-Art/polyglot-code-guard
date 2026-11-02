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
| JavaScript / TypeScript | \`npm test\`; TypeScript also runs \`tsc --noEmit\` when configured | Recognized runner pass summary |
| Java | Maven test or Gradle test | Maven/Gradle test summary |
| C# | \`dotnet test\` | .NET test-runner pass summary |
| PHP | PHPUnit | PHPUnit success summary |
| Ruby | RSpec, or Ruby's Minitest runner | RSpec/Minitest success summary |
| Swift | \`swift test\` | Swift test-result summary |
| C | CMake build and CTest | CTest positive test count |
| C++ | CMake build and CTest | CTest positive test count |

C and C++ are separate language identifiers in configuration. JavaScript and TypeScript share the npm project plan; TypeScript type checking does not replace running tests.

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
success_patterns = ['(?m)^([0-9]+) passed']
\`\`\`

Configuration fields:
- \`commands\`: non-empty argument arrays. Commands are launched without a shell.
- \`test_patterns\`: at least one matching file must exist inside the project root.
- \`success_patterns\`: optional regular expressions that extract a positive passing-test count from combined command output. If omitted, Polyguard uses the built-in pattern for that language.

**Trust boundary:** a user-defined command or output regex is a project-owner assertion. A malicious or incorrect command can print a fake summary. Polyguard cannot independently prove what an arbitrary custom command did. For the strongest built-in verification, use the default language runner or a recognized test-runner adapter and its native report. Do not use \`echo\`, \`print\`, or a dummy process as a test command.

The verifier rejects invalid regular expressions and test patterns that traverse outside the project root.

## Test evidence and verdict rules

A language check returns PASS only when:
1. The project and required test suite are detected.
2. Every required command completes successfully.
3. The runner output contains a positive passing-test count, or a configured custom success pattern matches one.
4. No command failure or timeout occurs.

A zero-test run, an output with no recognized success summary, or a non-zero exit code returns FAIL. In JSON and detailed output, Polyguard includes the parsed test count and the evidence summary when available.

## Integration tests

The repository includes disposable real-project fixtures for toolchains available in the local environment. Run:

\`\`\`bash
python -m pip install -e ".[test]"
python -m pytest -q
\`\`\`

Integration fixtures are created in temporary directories and run through Polyguard's actual project discovery and command execution path. Missing local toolchains are skipped by local integration tests; CI jobs install their required toolchains explicitly.

## AI coding-agent skill

The repository includes \`SKILL.md\`, also shipped in the installed package. Agents should run:

\`\`\`bash
polyguard verify --path . --details
\`\`\`

Agents must not report successful verification unless the command exits 0 and returns PASS. They must not delete, skip, or weaken tests to manufacture a passing result.

## Important limitations and security

Polyguard is a test-execution gate, **not a formal proof of program correctness**. PASS means that the configured commands succeeded and a positive test-pass summary was recognized. It does not guarantee adequate test coverage, correct requirements, absence of defects, or trustworthy user-defined commands/patterns.

The verifier executes project build scripts, package managers, and tests. Treat repositories and dependencies as untrusted. Run them in a disposable container or VM without secrets, privileged access, or sensitive host mounts. Polyguard does not itself provide operating-system isolation.

Automatic language discovery is conservative. Custom frameworks can use explicit commands and success patterns. Full monorepo orchestration is not implemented yet: the current release does not discover every nested package or assign a separate working directory to each package. Run Polyguard separately from each package root until package-level configuration is added.

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
