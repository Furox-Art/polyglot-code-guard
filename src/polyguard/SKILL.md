---
name: polyglot-code-verification
description: Verify AI-generated code with real language test runners and fail closed when tests are missing or fail.
---

# Polyglot Code Verification

Use this skill whenever code is generated, changed, refactored, or proposed as complete.

## Mandatory gate

1. Identify the project root and the languages present.
2. Ensure the implementation matches the user's stated requirements.
3. Run the verifier from the project root: polyguard verify --path . --details.
4. Do not claim that code works unless the verifier exits with status code 0 and prints PASS.
5. If any required test fails, the final verdict must be exactly FAIL. Do not label untested code as successful.
6. Missing test suites, missing language toolchains, timeouts, compilation errors, and test-runner errors are failures under the default fail-closed policy.
7. Inspect the diagnostic report, fix the implementation or tests, and rerun the complete verification suite.
8. Never weaken, delete, skip, or rewrite a test merely to obtain PASS. Explain any necessary change to test expectations.
9. Preserve the test output and command details when the user asks for evidence.

## Output contract

- Successful verification: PASS
- Failed or incomplete verification: FAIL

Use --json when another tool or CI system needs the structured report. Use --details when debugging.

## Security

The verifier executes project build and test commands. Treat source code, build scripts, dependencies, and tests as untrusted. Run untrusted projects in a disposable container or isolated virtual machine without secrets, privileged access, or sensitive host mounts. Polyguard is a quality gate, not a security sandbox.

## Scope

The initial runner recognizes Python, Rust, Go, JavaScript/TypeScript, Java, C#, PHP, Ruby, Swift, and C/C++ CMake projects. A language is not marked PASS unless a test suite is detected and every configured command succeeds.
