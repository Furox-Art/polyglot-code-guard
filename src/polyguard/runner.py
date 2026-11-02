"""Language discovery and fail-closed test execution."""

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


IGNORED_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".tox", "dist",
    "build", ".polyguard-build", "target", ".next", ".nuxt",
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


def _project_files(root: Path) -> list[Path]:
    found: list[Path] = []
    for path in root.rglob("*"):
        if any(part in IGNORED_DIRS for part in path.parts):
            continue
        if path.is_file():
            found.append(path)
    return found


def _in_test_tree(path: Path) -> bool:
    return any(part.lower() in {"test", "tests", "spec", "specs"} for part in path.parts)


def _has_test(files: Iterable[Path], language: str) -> bool:
    """Require a recognizable test declaration, not merely a file named like a test."""
    all_files = list(files)
    for path in all_files:
        name = path.name.lower()
        suffix = path.suffix.lower()
        content = _safe_read(path)

        if language == "python" and suffix == ".py":
            is_test_file = _in_test_tree(path) or name.startswith("test_") or name.endswith("_test.py")
            if is_test_file and re.search(r"\b(?:def\s+test_\w+\s*\(|class\s+Test\w*)", content):
                return True
        elif language == "rust" and suffix == ".rs":
            if re.search(r"#\s*\[\s*(?:test|tokio::test|async_std::test)\s*\]", content):
                return True
        elif language == "javascript" and suffix in {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}:
            is_test_file = _in_test_tree(path) or re.search(r"\.(test|spec)\.", name)
            if is_test_file and re.search(r"\b(?:test|it|specify)\s*(?:\.\w+)?\s*\(", content):
                return True
        elif language == "go" and name.endswith("_test.go"):
            if re.search(r"\bfunc\s+Test[A-Z]\w*\s*\(", content):
                return True
        elif language == "java" and suffix == ".java":
            is_test_file = _in_test_tree(path) or name.endswith(("test.java", "tests.java"))
            if is_test_file and re.search(r"@(?:Test|ParameterizedTest|RepeatedTest|TestFactory|TestTemplate)\b", content):
                return True
        elif language == "csharp" and suffix == ".cs":
            if re.search(r"\[(?:Fact|Theory|Test|TestCase|TestMethod)\b", content):
                return True
        elif language == "php" and suffix == ".php":
            is_test_file = _in_test_tree(path) or name.startswith("test")
            if is_test_file and re.search(r"(?:function\s+test\w*\s*\(|#\[Test\])", content, re.I):
                return True
        elif language == "ruby" and suffix == ".rb":
            is_test_file = _in_test_tree(path) or name.startswith("test_") or name.endswith("_spec.rb")
            if is_test_file and re.search(r"\b(?:it|specify|example)\s+['\"].+?\bdo\b|\bdef\s+test_\w+", content, re.S):
                return True
        elif language == "swift" and suffix == ".swift":
            if _in_test_tree(path) and re.search(r"(?:\bfunc\s+test\w*\s*\(|@Test\b)", content):
                return True
        elif language == "cpp" and suffix in {".c", ".cc", ".cpp", ".cxx", ".h", ".hpp"}:
            if (_in_test_tree(path) or "test" in name) and re.search(r"\b(?:TEST|TEST_F|TEST_P|TEST_CASE)\s*\(", content):
                cmake_files = [p for p in all_files if p.name.lower() == "cmakelists.txt"]
                cmake_text = "\n".join(_safe_read(p) for p in cmake_files)
                enabled = re.search(r"\b(?:enable_testing)\s*\(|\binclude\s*\(\s*CTest\b", cmake_text, re.I)
                registered = re.search(r"\b(?:add_test|gtest_discover_tests|catch_discover_tests)\s*\(", cmake_text)
                if enabled and registered:
                    return True
    return False


def _safe_read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")[:1_000_000]
    except OSError:
        return ""


def discover(root: Path) -> list[tuple[str, list[list[str]]]]:
    """Return supported language plans. A detected language without tests is still a failure."""
    files = _project_files(root)
    names = {p.name for p in files}
    plans: list[tuple[str, list[list[str]]]] = []

    py_files = [p for p in files if p.suffix == ".py"]
    if py_files:
        plans.append(("python", [
            [sys.executable, "-m", "compileall", "-q", "."],
            [sys.executable, "-m", "pytest", "-q"],
        ]))

    if "Cargo.toml" in names or any(p.suffix == ".rs" for p in files):
        plans.append(("rust", [["cargo", "test", "--all-targets"]]))

    if "go.mod" in names:
        plans.append(("go", [["go", "test", "./..."]]))

    js_project = "package.json" in names and any(
        p.suffix.lower() in {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"} for p in files
    )
    if js_project:
        js_commands = [["npm", "test"]]
        if "tsconfig.json" in names:
            js_commands.append(["npx", "--no-install", "tsc", "--noEmit"])
        plans.append(("javascript", js_commands))

    java_build = names.intersection({"pom.xml", "build.gradle", "build.gradle.kts", "mvnw", "gradlew"})
    if java_build:
        if "pom.xml" in names:
            cmd = ["./mvnw", "test"] if "mvnw" in names else ["mvn", "test"]
        else:
            cmd = ["./gradlew", "test"] if "gradlew" in names else ["gradle", "test"]
        plans.append(("java", [cmd]))

    cs_projects = [p for p in files if p.suffix.lower() in {".csproj", ".sln"}]
    if cs_projects:
        plans.append(("csharp", [["dotnet", "test", "--no-restore"]]))

    if "composer.json" in names:
        phpunit = root / "vendor" / "bin" / "phpunit"
        plans.append(("php", [[str(phpunit)] if phpunit.exists() else ["phpunit"]]))

    if "Gemfile" in names or any(p.suffix == ".gemspec" for p in files):
        if any(p.parts[-2:-1] == ("spec",) for p in files):
            plans.append(("ruby", [["bundle", "exec", "rspec"]]))
        else:
            plans.append(("ruby", [["bundle", "exec", "rake", "test"]]))

    if "Package.swift" in names:
        plans.append(("swift", [["swift", "test"]]))

    if "CMakeLists.txt" in names:
        cmake_text = _safe_read(root / "CMakeLists.txt")
        if re.search(r"enable_testing\s*\(|include\s*\(\s*CTest", cmake_text, re.I):
            plans.append(("cpp", [
                ["cmake", "-S", ".", "-B", ".polyguard-build", "-DBUILD_TESTING=ON"],
                ["cmake", "--build", ".polyguard-build", "--parallel"],
                ["ctest", "--test-dir", ".polyguard-build", "--output-on-failure"],
            ]))
        else:
            plans.append(("cpp", [["cmake", "-S", ".", "-B", ".polyguard-build"]]))

    return plans


def run_checks(root: Path, timeout: int = 120) -> dict:
    root = root.resolve()
    if not root.is_dir():
        return {
            "status": "FAIL",
            "results": [asdict(CheckResult("project", "FAIL", [], 2, 0.0, "Project directory does not exist."))],
        }

    plans = discover(root)
    if not plans:
        return {
            "status": "FAIL",
            "results": [asdict(CheckResult("project", "FAIL", [], 2, 0.0, "No supported language project was detected."))],
        }

    results: list[CheckResult] = []
    files = _project_files(root)
    for language, commands in plans:
        if not _has_test(files, language):
            results.append(CheckResult(
                language, "FAIL", commands, 2, 0.0,
                "No test suite was detected; fail-closed policy rejects untested code.",
            ))
            continue

        started = time.monotonic()
        last_code: int | None = 0
        output_parts: list[str] = []
        failure_reason = ""
        for command in commands:
            executable = command[0]
            if executable.startswith("./"):
                executable_path = root / executable[2:]
                if not executable_path.exists():
                    last_code = 127
                    failure_reason = f"Required test runner not found: {executable}"
                    break
            elif not Path(executable).is_absolute() and shutil.which(executable) is None:
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

        elapsed = time.monotonic() - started
        passed = last_code == 0 and not failure_reason
        results.append(CheckResult(
            language,
            "PASS" if passed else "FAIL",
            commands,
            last_code,
            round(elapsed, 3),
            failure_reason,
            "\n".join(output_parts)[-12000:],
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
        if item["reason"]:
            lines.append(f"  {item['reason']}")
        if item["output"]:
            lines.append(item["output"])
    return "\n".join(lines)
