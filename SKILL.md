---
name: polyglot-code-verification
description: Verify AI-generated code with actual project test runners and fail closed when tests do not execute or fail.
---

# Polyglot Code Verification

Use this skill whenever code is generated, changed, refactored, or described as complete.

## Required workflow

1. Identify every language, build system, package root, and test framework touched by the change.
2. Add requirement-based tests for normal behavior, edge cases, invalid inputs, error paths, and regressions.
3. Run \`polyguard verify --path . --details\`.
4. For a non-standard test runner, configure explicit commands, file patterns, and a success-summary parser in \`.polyguard.toml\`.
5. Run the complete configured test suite. Do not omit a detected or required language.
6. Require a positive passed-test count in recognized runner output. A successful command exit code or a test-looking filename alone is not sufficient.
7. If a command fails, a toolchain is missing, tests cannot be found, the runner reports no passing tests, execution times out, or output evidence is missing, the verdict is FAIL.
8. Fix the implementation or tests and rerun all checks. Never delete, skip, weaken, or rewrite a valid test merely to obtain PASS.
9. Retain command output and the JSON report when inspectable evidence is requested.

## Output contract

- All required checks complete and positive test-pass evidence is present: PASS
- Any failure or incomplete verification: FAIL

The CLI exits 0 only for PASS and 1 for FAIL. Default output is exactly one word; use \`--details\` or \`--json\` for diagnostics.

## Custom command trust

A custom \`success_patterns\` entry is a trusted assertion by the repository owner about command output. It is not independent proof that the command executed real tests. Do not use a dummy command that prints a success-looking summary. Prefer built-in adapters and native test reports.

## Scope

Built-in support: Python, Rust, Go, JavaScript/TypeScript, Java, C#, PHP, Ruby, Swift, C, and C++ with CMake/CTest. Use explicit configuration for unconventional project layouts and custom runners. Full monorepo orchestration is not implemented; run Polyguard separately from each package root.

## Security

The verifier executes build tools, package managers, scripts, and tests from the target repository. Run untrusted code in a disposable container or isolated virtual machine without secrets, privileged access, or sensitive host mounts. Polyguard is not an operating-system sandbox.
