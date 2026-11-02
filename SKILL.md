---
name: polyglot-code-verification
description: A fail-closed quality gate for AI-generated code across multiple programming languages.
---

# Polyglot Code Verification Skill

This skill makes executable evidence mandatory before AI-generated code is accepted.

Run the CLI from the target project:

    polyguard verify --path . --details

The default user-facing verdict is intentionally minimal:

- All detected language suites exist and all configured commands pass: PASS
- A test fails, a required test suite is missing, a toolchain is unavailable, or execution times out: FAIL

A zero exit code is required before reporting success. Exit code 1 means FAIL.

Do not infer correctness from code inspection alone. Do not omit failing languages. Do not weaken tests to manufacture a passing result. Add requirement-based tests for important edge cases and rerun the full suite.

Supported project types in this release: Python, Rust, Go, JavaScript/TypeScript, Java, C#, PHP, Ruby, Swift, and C/C++ projects using CMake.

The verifier executes build and test scripts. Run unknown or untrusted code in a disposable sandbox with no secrets and no privileged host mounts. Polyguard does not itself provide operating-system isolation.
