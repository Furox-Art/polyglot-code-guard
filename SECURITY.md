# Security Policy

## Supported versions

Polyguard is pre-1.0. Only the latest published release receives security fixes.

| Version | Supported |
| --- | --- |
| 0.4.x (current) | Yes |
| Older releases | No, upgrade to the latest version |

## Reporting a vulnerability

Report vulnerabilities privately through GitHub Security Advisories:

<https://github.com/Furox-Art/polyglot-code-guard/security/advisories/new>

Please include the affected version, a minimal reproduction (project layout,
configuration, and the command you ran), and the impact you observed. Do not open a public
issue for an undisclosed vulnerability. You can expect an initial response within 7 days.

## In scope

- A FAIL verdict that can be turned into PASS without tests actually passing, for example
  forged, stale, empty, all-skipped, or mismatched test evidence.
- Command injection, shell interpretation, or argument smuggling in the build/test command
  plans, including crafted project configuration files and manifests.
- Path handling flaws: escaping the project root, symlink traversal during project or test
  file discovery, or reading/writing outside the intended directory.
- Crashes or unhandled exceptions that produce a verdict other than FAIL when evidence is
  missing or malformed.

## Out of scope

- Polyguard does not provide operating-system isolation. It executes the target
  repository's build tools, package managers, and tests, so running untrusted code without a
  disposable container or VM is a documented deployment risk, not a vulnerability.
- Failures caused only by a missing or unsupported toolchain, or by a runner that emits no
  recognized report format (the documented fail-closed outcome).
- Test quality: PASS does not imply adequate coverage, correct requirements, or the absence
  of defects. Polyguard is a test-execution gate, not a correctness proof.
- Vulnerabilities in third-party toolchains, package managers, and test runners that
  Polyguard invokes.
- Vulnerabilities that require an already-compromised host, secrets in the environment, or
  privileged access the documentation tells you not to grant.
