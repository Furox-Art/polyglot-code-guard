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
4. For a non-standard test runner, explicit commands and file patterns can be configured for diagnostics; custom commands cannot establish PASS until a trusted runner adapter is implemented.
5. Run the complete configured test suite. Do not omit a detected or required language.
6. Require a positive passed-test count in recognized runner output. Java and PHP default checks must produce fresh JUnit XML; C# must produce a fresh TRX report. Swift must emit a positive native test-runner summary. A successful command exit code alone is not sufficient.
7. If a command fails, a toolchain is missing, tests cannot be found, the runner reports no passing tests, execution times out, or output evidence is missing, the verdict is FAIL.
8. Fix the implementation or tests and rerun all checks. Never delete, skip, weaken, or rewrite a valid test merely to obtain PASS.
9. Retain command output and the JSON report when inspectable evidence is requested.

## Output contract

- All required checks complete and positive test-pass evidence is present: PASS
- Any failure or incomplete verification: FAIL

The CLI exits 0 only for PASS and 1 for FAIL. Default output is exactly one word; use \`--details\` or \`--json\` for diagnostics.

## Custom command trust

Custom \`success_patterns\` are rejected. Custom commands may execute for diagnostics, but their output and generated reports cannot establish PASS because a command can forge them. Only the exact built-in command plan for the detected project is accepted as execution evidence. Java, C#, and PHP additionally require fresh native reports; Swift uses its runner summary.

## Scope

Built-in support: Python, Rust, Go, JavaScript/TypeScript, Java, C#, PHP, Ruby, Swift, C, and C++ with CMake/CTest. Use explicit configuration for unconventional project layouts and custom runners. Polyguard discovers supported package manifests recursively, executes each language package from its own root, and reports multiple packages of the same language separately. A root-level configuration is applied to each matching package with test patterns evaluated relative to that package root. Maven modules, Gradle settings builds, Cargo workspaces, npm workspaces, and .NET solutions are delegated to their native root command once to avoid duplicate child-package execution.

## Security

The verifier executes build tools, package managers, scripts, and tests from the target repository. Run untrusted code in a disposable container or isolated virtual machine without secrets, privileged access, or sensitive host mounts. Polyguard is not an operating-system sandbox.
