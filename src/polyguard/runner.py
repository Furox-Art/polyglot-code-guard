"""Language discovery, configurable checks, and evidence-based fail-closed execution."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib


IGNORED_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".tox", "dist",
    "build", ".polyguard-build", "target", ".next", ".nuxt",
    ".polyguard-fixtures",
}
SUPPORTED_LANGUAGES = {
    "python", "rust", "go", "javascript", "java", "csharp",
    "php", "ruby", "swift", "c", "cpp",
}
LANGUAGE_ALIASES = {
    "py": "python", "js": "javascript", "ts": "javascript",
    "cc": "cpp", "cxx": "cpp", "c++": "cpp",
    "cs": "csharp",
}

# These patterns require a positive success count, not merely a successful
# process exit. The runner's own exit code remains authoritative for failures.
DEFAULT_SUCCESS_PATTERNS = {
    "python": [r"(?m)^\s*(\d+)\s+passed\b"],
    "rust": [r"(?m)^test result: ok\.\s*(\d+)\s+passed;"],
    "javascript": [
        r"(?im)^\s*#\s*pass\s+(\d+)\s*$",
        r"(?im)^\s*ℹ\s+pass\s+(\d+)\s*$",
        r"(?im)^Tests:\s*(\d+)\s+passed\b",
        r"\b(\d+)\s+passing\b",
    ],
    "java": [
        r"\bTests run:\s*(\d+),\s*Failures:\s*0,\s*Errors:\s*0,\s*Skipped:\s*0\b",
        r"\b(\d+)\s+tests completed,\s*0 failed\b",
    ],
    "csharp": [
        r"\bPassed!\s+-\s+Failed:\s*0,\s*Passed:\s*(\d+),",
        r"\bPassed:\s*(\d+)\s+Failed:\s*0\b",
    ],
    "php": [r"\bOK\s+\((\d+)\s+tests?"],
    "ruby": [
        r"\b(\d+)\s+examples?,\s+0\s+failures?\s*$",
        r"\b(\d+)\s+runs?,\s+\d+\s+assertions?,\s+0\s+failures?,\s+0\s+errors?,\s+0\s+skips?\b",
    ],
    "swift": [r"\bExecuted\s+(\d+)\s+tests?,\s+with\s+0\s+failures?\b"],
    "c": [r"\b100%\s+tests passed,\s*0\s+tests? failed\s+out of\s+(\d+)\b"],
    "cpp": [r"\b100%\s+tests passed,\s*0\s+tests? failed\s+out of\s+(\d+)\b"],
}


@dataclass
class CheckResult:
    language: str
    status: str
    commands: list[list[str]]
    exit_code: int | None
    elapsed_seconds: float
    reason: str = ""
    output: str = ""
    tests_passed: int | None = None
    execution_evidence: str = ""


@dataclass
class LanguageConfig:
    commands: list[list[str]]
    test_patterns: list[str]
    success_patterns: list[str] | None = None


def normalize_language(language: str) -> str:
    key = language.strip().lower()
    return LANGUAGE_ALIASES.get(key, key)


def _project_files(root: Path) -> list[Path]:
    root = root.resolve()
    found: list[Path] = []
    for path in root.rglob("*"):
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if any(part in IGNORED_DIRS for part in relative.parts):
            continue
        if path.is_file():
            found.append(path)
    return found


def _in_test_tree(root: Path, path: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        relative = path
    return any(part.lower() in {"test", "tests", "spec", "specs"} for part in relative.parts[:-1])


def _safe_read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")[:1_000_000]
    except OSError:
        return ""


def _has_test(files: Iterable[Path], language: str, root: Path | None = None) -> bool:
    """Conservative source-level precheck; this is not execution evidence."""
    all_files = list(files)
    base = root.resolve() if root else None
    for path in all_files:
        name = path.name.lower()
        suffix = path.suffix.lower()
        content = _safe_read(path)
        in_test_tree = _in_test_tree(base, path) if base else any(
            part.lower() in {"test", "tests", "spec", "specs"} for part in path.parts[:-1]
        )

        if language == "python" and suffix == ".py":
            is_test_file = in_test_tree or name.startswith("test_") or name.endswith("_test.py")
            if is_test_file and re.search(r"\b(?:def\s+test_\w+\s*\(|class\s+Test\w*)", content):
                return True
        elif language == "rust" and suffix == ".rs":
            if re.search(r"#\s*\[\s*(?:test|tokio::test|async_std::test)\s*\]", content):
                return True
        elif language == "javascript" and suffix in {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}:
            is_test_file = in_test_tree or re.search(r"\.(test|spec)\.", name)
            if is_test_file and re.search(r"\b(?:test|it|specify)\s*(?:\.\w+)?\s*\(", content):
                return True
        elif language == "go" and name.endswith("_test.go"):
            if re.search(r"\bfunc\s+Test[A-Z]\w*\s*\(", content):
                return True
        elif language == "java" and suffix == ".java":
            is_test_file = in_test_tree or name.endswith(("test.java", "tests.java"))
            if is_test_file and re.search(
                r"@(?:Test|ParameterizedTest|RepeatedTest|TestFactory|TestTemplate)\b", content
            ):
                return True
        elif language == "csharp" and suffix == ".cs":
            if re.search(r"\[(?:Fact|Theory|Test|TestCase|TestMethod)\b", content):
                return True
        elif language == "php" and suffix == ".php":
            is_test_file = in_test_tree or name.startswith("test")
            if is_test_file and re.search(r"(?:function\s+test\w*\s*\(|#\[Test\])", content, re.I):
                return True
        elif language == "ruby" and suffix == ".rb":
            is_test_file = in_test_tree or name.startswith("test_") or name.endswith("_spec.rb")
            if is_test_file and re.search(
                r"\b(?:it|specify|example)\s+['\"].+?\bdo\b|\bdef\s+test_\w+", content, re.S
            ):
                return True
        elif language == "swift" and suffix == ".swift":
            if in_test_tree and re.search(r"(?:\bfunc\s+test\w*\s*\(|@Test\b)", content):
                return True
        elif language == "c" and suffix == ".c":
            if in_test_tree and re.search(r"\b(?:test_\w+|main)\s*\(", content) and "assert(" in content:
                cmake = [p for p in all_files if p.name.lower() == "cmakelists.txt"]
                cmake_text = "\n".join(_safe_read(p) for p in cmake)
                if re.search(r"\badd_test\s*\(", cmake_text):
                    return True
        elif language == "cpp" and suffix in {".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx"}:
            has_framework_test = re.search(
                r"\b(?:TEST|TEST_F|TEST_P|TEST_CASE)\s*\(", content
            )
            has_assert_harness = re.search(r"\bmain\s*\(", content) and "assert(" in content
            if (in_test_tree or "test" in name) and (has_framework_test or has_assert_harness):
                cmake = [p for p in all_files if p.name.lower() == "cmakelists.txt"]
                cmake_text = "\n".join(_safe_read(p) for p in cmake)
                enabled = re.search(
                    r"\b(?:enable_testing)\s*\(|\binclude\s*\(\s*CTest\b", cmake_text, re.I
                )
                registered = re.search(
                    r"\b(?:add_test|gtest_discover_tests|catch_discover_tests)\s*\(", cmake_text
                )
                if enabled and registered:
                    return True
    return False


def _default_plans(root: Path) -> dict[str, list[list[str]]]:
    files = _project_files(root)
    names = {p.name for p in files}
    plans: dict[str, list[list[str]]] = {}

    if any(p.suffix.lower() == ".py" for p in files):
        plans["python"] = [
            [sys.executable, "-m", "compileall", "-q", "."],
            [sys.executable, "-m", "pytest", "-q"],
        ]

    if "Cargo.toml" in names or any(p.suffix == ".rs" for p in files):
        plans["rust"] = [["cargo", "test", "--all-targets"]]
    if "go.mod" in names:
        plans["go"] = [["go", "test", "-json", "./..."]]

    js_project = "package.json" in names and any(
        p.suffix.lower() in {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"} for p in files
    )
    if js_project:
        js_commands = [["npm", "test"]]
        if "tsconfig.json" in names:
            js_commands.append(["npx", "--no-install", "tsc", "--noEmit"])
        plans["javascript"] = js_commands

    java_build = names.intersection({"pom.xml", "build.gradle", "build.gradle.kts", "mvnw", "gradlew"})
    if java_build:
        if "pom.xml" in names:
            command = ["./mvnw", "test"] if "mvnw" in names else ["mvn", "test"]
        else:
            command = ["./gradlew", "test"] if "gradlew" in names else ["gradle", "test"]
        plans["java"] = [command]

    if any(p.suffix.lower() in {".csproj", ".sln"} for p in files):
        plans["csharp"] = [["dotnet", "test", "--logger", "console;verbosity=normal"]]

    if "composer.json" in names:
        phpunit = root / "vendor" / "bin" / "phpunit"
        plans["php"] = [[str(phpunit)] if phpunit.exists() else ["phpunit"]]

    if "Gemfile" in names or any(p.suffix == ".gemspec" for p in files):
        if any(p.name.lower().endswith("_spec.rb") for p in files):
            plans["ruby"] = [["bundle", "exec", "rspec"]]
        else:
            # Minitest ships with standard Ruby distributions; this avoids
            # assuming every project has a Rake task or a Rakefile.
            plans["ruby"] = [[
                "ruby", "-Itest", "-e",
                "require 'minitest/autorun'; Dir.glob('test/**/*_test.rb').each { |file| require File.expand_path(file) }",
            ]]

    if "Package.swift" in names:
        plans["swift"] = [["swift", "test"]]

    cmake_files = [p for p in files if p.name.lower() == "cmakelists.txt"]
    if cmake_files:
        cmake_text = "\n".join(_safe_read(p) for p in cmake_files)
        cmake_commands = [
            ["cmake", "-S", ".", "-B", ".polyguard-build", "-DBUILD_TESTING=ON"],
            ["cmake", "--build", ".polyguard-build", "--parallel"],
            ["ctest", "--test-dir", ".polyguard-build", "--output-on-failure"],
        ]
        if any(p.suffix.lower() == ".c" for p in files):
            plans["c"] = cmake_commands
        if any(p.suffix.lower() in {".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx"} for p in files):
            plans["cpp"] = cmake_commands
    return plans


def discover(root: Path) -> list[tuple[str, list[list[str]]]]:
    """Return detected languages and their default commands."""
    return list(_default_plans(root).items())


def _load_config(root: Path, config_path: str | Path | None) -> tuple[set[str], dict[str, LanguageConfig]]:
    path = Path(config_path).expanduser() if config_path else root / ".polyguard.toml"
    if not path.is_absolute():
        path = path.resolve()
    if not path.exists():
        if config_path is not None:
            raise ValueError(f"Configuration file does not exist: {path}")
        return set(), {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError(f"Could not read TOML configuration {path}: {exc}") from exc

    if not isinstance(data, dict) or set(data) - {"project", "checks"}:
        raise ValueError("Configuration may contain only [project] and [checks.<language>] sections.")

    project = data.get("project", {})
    checks = data.get("checks", {})
    if not isinstance(project, dict) or set(project) - {"languages"}:
        raise ValueError("[project] supports only the 'languages' key.")
    if not isinstance(checks, dict):
        raise ValueError("[checks] must contain language tables.")

    languages = project.get("languages", [])
    if not isinstance(languages, list) or not all(isinstance(x, str) for x in languages):
        raise ValueError("[project].languages must be an array of language names.")

    required: set[str] = set()
    for item in languages:
        language = normalize_language(item)
        if language not in SUPPORTED_LANGUAGES:
            raise ValueError(f"Unsupported language in [project].languages: {item}")
        required.add(language)

    configured: dict[str, LanguageConfig] = {}
    for raw_language, section in checks.items():
        language = normalize_language(raw_language)
        if language not in SUPPORTED_LANGUAGES:
            raise ValueError(f"Unsupported language in [checks]: {raw_language}")
        allowed = {"commands", "test_patterns", "success_patterns"}
        if not isinstance(section, dict) or set(section) - allowed:
            raise ValueError(
                f"[checks.{raw_language}] supports only 'commands', 'test_patterns', and 'success_patterns'."
            )
        commands = section.get("commands")
        patterns = section.get("test_patterns")
        success_patterns = section.get("success_patterns")
        if not isinstance(commands, list) or not commands:
            raise ValueError(f"[checks.{raw_language}].commands must be a non-empty array of argument arrays.")
        normalized_commands: list[list[str]] = []
        for command in commands:
            if not isinstance(command, list) or not command or not all(
                isinstance(arg, str) and arg for arg in command
            ):
                raise ValueError(f"Every command in [checks.{raw_language}] must be a non-empty string array.")
            normalized_commands.append(command)
        if not isinstance(patterns, list) or not patterns or not all(
            isinstance(pattern, str) and pattern for pattern in patterns
        ):
            raise ValueError(f"[checks.{raw_language}].test_patterns must be a non-empty string array.")
        if any(".." in Path(pattern).parts for pattern in patterns):
            raise ValueError("Test patterns may not traverse outside the project root.")
        if success_patterns is not None:
            if not isinstance(success_patterns, list) or not success_patterns or not all(
                isinstance(pattern, str) and pattern for pattern in success_patterns
            ):
                raise ValueError(
                    f"[checks.{raw_language}].success_patterns must be a non-empty array of regex strings."
                )
            for pattern in success_patterns:
                try:
                    re.compile(pattern)
                except re.error as exc:
                    raise ValueError(
                        f"Invalid success regex in [checks.{raw_language}]: {exc}"
                    ) from exc
        configured[language] = LanguageConfig(normalized_commands, patterns, success_patterns)
        required.add(language)

    return required, configured


def _matching_config_tests(root: Path, patterns: list[str]) -> bool:
    for pattern in patterns:
        try:
            if any(path.is_file() for path in root.glob(pattern)):
                return True
        except (OSError, ValueError):
            continue
    return False


def _execution_evidence(
    language: str,
    output: str,
    success_patterns: list[str] | None = None,
) -> tuple[int | None, str]:
    """Parse a positive successful-test count from a known runner summary."""
    if language == "go" and success_patterns is None:
        passed = 0
        for line in output.splitlines():
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if event.get("Action") == "pass" and event.get("Test"):
                passed += 1
        if passed:
            return passed, f"go test JSON: {passed} passing test event(s)"
        return None, "go test JSON contained no passing test events."

    patterns = success_patterns or DEFAULT_SUCCESS_PATTERNS.get(language, [])
    total = 0
    matched: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, output):
            try:
                count = int(match.group(1)) if match.lastindex else 1
            except (TypeError, ValueError):
                count = 0
            if count > 0:
                total += count
                matched.append(pattern)
    if total <= 0:
        return None, "No positive successful-test count was found in runner output."
    return total, f"Matched {len(set(matched))} execution-evidence pattern(s); {total} test(s) reported passing."


def _failure_report(reason: str, language: str = "configuration") -> dict:
    result = CheckResult(language, "FAIL", [], 2, 0.0, reason)
    return {"status": "FAIL", "results": [asdict(result)]}


def run_checks(
    root: Path,
    timeout: int = 120,
    config_path: str | Path | None = None,
) -> dict:
    root = root.expanduser().resolve()
    if timeout < 1:
        return _failure_report("Timeout must be at least 1 second.")
    if not root.is_dir():
        return _failure_report("Project directory does not exist.", "project")

    try:
        required, configured = _load_config(root, config_path)
    except ValueError as exc:
        return _failure_report(str(exc))

    plans = _default_plans(root)
    for language in required:
        plans.setdefault(language, [])
    if not plans:
        return _failure_report("No supported language project was detected.", "project")

    results: list[CheckResult] = []
    files = _project_files(root)
    for language, default_commands in sorted(plans.items()):
        language_config = configured.get(language)
        commands = language_config.commands if language_config else default_commands
        if not commands:
            results.append(CheckResult(
                language, "FAIL", [], 2, 0.0,
                "Language was required by configuration but no check commands were provided.",
            ))
            continue

        if language_config:
            has_tests = _matching_config_tests(root, language_config.test_patterns)
            test_reason = "Configured test_patterns did not match any files."
        else:
            has_tests = _has_test(files, language, root)
            test_reason = "No test suite was detected; fail-closed policy rejects untested code."
        if not has_tests:
            results.append(CheckResult(language, "FAIL", commands, 2, 0.0, test_reason))
            continue

        started = time.monotonic()
        last_code: int | None = 0
        output_parts: list[str] = []
        failure_reason = ""
        for command in commands:
            executable = command[0]
            executable_path: Path | None = None
            if Path(executable).is_absolute():
                executable_path = Path(executable)
            elif "/" in executable or executable.startswith(".\\"):
                executable_path = (root / executable).resolve()
            if executable_path is not None:
                if not executable_path.exists():
                    last_code = 127
                    failure_reason = f"Required command not found: {executable}"
                    break
            elif shutil.which(executable) is None:
                last_code = 127
                failure_reason = f"Required tool not installed: {executable}"
                break

            try:
                completed = subprocess.run(
                    command,
                    cwd=root,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=timeout,
                    check=False,
                    shell=False,
                )
                last_code = completed.returncode
                if completed.stdout:
                    output_parts.append(completed.stdout[-12000:])
                if last_code != 0:
                    failure_reason = f"Command failed with exit code {last_code}: {' '.join(command)}"
                    break
            except subprocess.TimeoutExpired as exc:
                last_code = 124
                captured = exc.stdout or ""
                if isinstance(captured, bytes):
                    captured = captured.decode("utf-8", errors="replace")
                output_parts.append(str(captured)[-12000:])
                failure_reason = f"Command timed out after {timeout} seconds: {' '.join(command)}"
                break
            except OSError as exc:
                last_code = 126
                failure_reason = f"Could not execute {' '.join(command)}: {exc}"
                break

        elapsed = time.monotonic() - started
        combined_output = "\n".join(output_parts)[-12000:]
        passed = last_code == 0 and not failure_reason
        tests_passed: int | None = None
        execution_evidence = ""
        if passed:
            tests_passed, execution_evidence = _execution_evidence(
                language,
                combined_output,
                language_config.success_patterns if language_config else None,
            )
            if tests_passed is None:
                passed = False
                failure_reason = (
                    "Commands exited successfully, but Polyguard could not verify that any tests "
                    "passed. Use a supported runner output format or configure success_patterns. "
                    "Custom success_patterns are trusted project configuration, not independent proof."
                )
        results.append(CheckResult(
            language, "PASS" if passed else "FAIL", commands, last_code,
            round(elapsed, 3), failure_reason, combined_output,
            tests_passed, execution_evidence,
        ))

    status = "PASS" if results and all(item.status == "PASS" for item in results) else "FAIL"
    return {"status": status, "root": str(root), "results": [asdict(item) for item in results]}


def render_report(report: dict, details: bool = False, as_json: bool = False) -> str:
    if as_json:
        return json.dumps(report, indent=2, ensure_ascii=False)
    if not details:
        return report["status"]
    lines = [report["status"]]
    for item in report["results"]:
        lines.append(f"{item['language']}: {item['status']}")
        if item.get("tests_passed") is not None:
            lines.append(f"  Tests passed: {item['tests_passed']}")
        if item.get("execution_evidence"):
            lines.append(f"  Evidence: {item['execution_evidence']}")
        if item["reason"]:
            lines.append(f"  {item['reason']}")
        if item["output"]:
            lines.append(item["output"])
    return "\n".join(lines)
