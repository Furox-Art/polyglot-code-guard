---
name: polyglot-code-verification
description: Verify AI-generated code with real project test commands and fail closed when tests are missing or fail.
---

# Polyglot Code Verification

Use this skill whenever code is generated, changed, refactored, or described as complete.

## Required workflow

1. Identify all languages, build systems, packages, and project roots touched by the change.
2. Review the user's requirements and create or update tests for normal behavior, edge cases, invalid inputs, error paths, and regressions.
3. For conventional repositories, run `polyguard verify --path . --details`.
4. For a custom layout or test runner, add a `.polyguard.toml` with explicit commands and test file patterns. Never use a command that simply prints success.
5. Run the complete configured test suite. Do not omit a detected or required language.
6. If any command fails, the toolchain is missing, tests cannot be found, configuration is invalid, or a timeout occurs, the final verdict is FAIL.
7. Fix the implementation or tests and rerun all checks. Never delete, skip, weaken, or rewrite a valid test merely to obtain PASS.
8. Retain command output and the JSON report when the user requests inspectable evidence.

## Output contract

- All detected and required language checks pass: PASS
- Any failure or incomplete verification: FAIL

The CLI exits 0 only for PASS and 1 for FAIL. The default output is exactly one word; use `--details` or `--json` for diagnostics.

## Security

The verifier executes build tools, package managers, scripts, and tests from the target repository. Run untrusted code in a disposable container or isolated virtual machine without secrets, privileged access, or sensitive host mounts. Polyguard is not an operating-system sandbox.

## Scope

Built-in support: Python, Rust, Go, JavaScript/TypeScript, Java, C#, PHP, Ruby, Swift, and C/C++ with CMake. Use project configuration for unconventional layouts, custom test runners, and monorepos.
