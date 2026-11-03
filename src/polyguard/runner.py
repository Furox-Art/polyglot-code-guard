"""Language discovery, configurable checks, and evidence-based fail-closed execution."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Thread
from typing import Iterable

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib


IGNORED_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".tox", "dist",
    "build", ".polyguard-build", "target", ".next", ".nuxt",
    ".polyguard-fixtures", ".polyguard-results", "vendor", "bin", "obj", ".build", ".nuget", "TestResults",
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
        # Vitest's default/minimal reporters include a test-level summary:
        # "Tests 16 passed | 4 skipped (20)". Do not count "Test Files".
        r"(?im)^\s*Tests\s+(\d+)\s+passed(?:\s*\|[^\r\n]*)?\s+\(\d+\)\s*$",
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
    "swift": [
        r"\bExecuted\s+(\d+)\s+tests?,\s+with\s+0\s+failures?\b",
        r"(?m)^.*Test run with (\d+) tests? in \d+ suites? passed\b",
    ],
    # CTest's aggregate "100% tests passed" summary also appears when every
    # test is skipped via SKIP_RETURN_CODE. Count only per-test Passed records.
    "c": [r"(?m)^\s*\d+/\d+\s+Test\s+#\d+:\s+.*\.\.\.\s+Passed\s+\d+(?:\.\d+)?\s+sec\s*$"],
    "cpp": [r"(?m)^\s*\d+/\d+\s+Test\s+#\d+:\s+.*\.\.\.\s+Passed\s+\d+(?:\.\d+)?\s+sec\s*$"],
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
    tests_failed: int | None = None
    tests_skipped: int | None = None
    tests_total: int | None = None
    execution_evidence: str = ""
    project_root: str = ""


@dataclass
class LanguageConfig:
    commands: list[list[str]]
    test_patterns: list[str]


@dataclass(frozen=True)
class ProjectUnit:
    """One independently executable language project inside a repository."""

    root: Path
    language: str


PROJECT_MARKERS = {
    "python": {"pyproject.toml", "setup.py", "setup.cfg"},
    "rust": {"cargo.toml"},
    "go": {"go.mod"},
    "javascript": {"package.json"},
    "java": {"pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"},
    "csharp": {".csproj", ".sln"},
    "php": {"composer.json"},
    "ruby": {"gemfile", ".gemspec"},
    "swift": {"package.swift"},
}


def normalize_language(language: str) -> str:
    key = language.strip().lower()
    return LANGUAGE_ALIASES.get(key, key)


def _project_files(root: Path) -> list[Path]:
    """List project files while pruning generated/vendor trees during traversal."""
    import os

    root = root.resolve()
    found: list[Path] = []
    for current, directories, filenames in os.walk(root, topdown=True, followlinks=False):
        directories[:] = sorted(
            name for name in directories
            if name not in IGNORED_DIRS
            and not (Path(current) / name).is_symlink()
        )
        current_path = Path(current)
        found.extend(current_path / name for name in filenames)
    safe_files = []
    for path in found:
        if path.is_symlink():
            continue
        try:
            if _is_within(path.resolve(), root):
                safe_files.append(path)
        except (OSError, RuntimeError):
            continue
    return sorted(safe_files)


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


def _cmake_invocations(text: str) -> list[tuple[str, list[str]]]:
    """Extract CMake commands while treating comments and bracket arguments as opaque."""
    commands: list[tuple[str, list[str]]] = []

    def bracket_end(start: int) -> int | None:
        opener = re.match(r"\[(=*)\[", text[start:])
        if not opener:
            return None
        closer = f"]{opener.group(1)}]"
        closing_index = text.find(closer, start + len(opener.group(0)))
        return -1 if closing_index < 0 else closing_index + len(closer)

    def skip_comment(start: int) -> int:
        bracket_start = start + 1
        if bracket_start < len(text) and text[bracket_start] == "[":
            end = bracket_end(bracket_start)
            if end is not None:
                return len(text) if end < 0 else end
        newline = text.find("\n", start)
        return len(text) if newline < 0 else newline + 1

    index = 0
    while index < len(text):
        if text[index] == "#":
            index = skip_comment(index)
            continue
        if text[index] == "[":
            end = bracket_end(index)
            if end is not None:
                if end < 0:
                    break
                index = end
                continue
        match = re.match(r"[A-Za-z_][A-Za-z0-9_]*", text[index:])
        if not match:
            index += 1
            continue
        name = match.group(0).lower()
        cursor = index + len(match.group(0))
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        if cursor >= len(text) or text[cursor] != "(":
            index += len(match.group(0))
            continue
        cursor += 1
        start = cursor
        depth = 1
        quote = False
        escaped = False
        malformed = False
        while cursor < len(text) and depth:
            char = text[cursor]
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif quote:
                if char == '"':
                    quote = False
            elif char == '"':
                quote = True
            elif char == "[":
                end = bracket_end(cursor)
                if end is not None:
                    if end < 0:
                        malformed = True
                        cursor = len(text)
                        break
                    cursor = end
                    continue
            elif char == "#":
                cursor = skip_comment(cursor)
                continue
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            cursor += 1
        if depth or malformed:
            break
        try:
            arguments = shlex.split(text[start:cursor - 1], comments=True, posix=True)
        except ValueError:
            arguments = []
        commands.append((name, arguments))
        index = cursor
    return commands


_C_SOURCE_SUFFIXES = {".c"}
_CPP_SOURCE_SUFFIXES = {".cc", ".cpp", ".cxx", ".c++"}
_NATIVE_SOURCE_SUFFIXES = _C_SOURCE_SUFFIXES | _CPP_SOURCE_SUFFIXES


def _native_test_source(path: Path, language: str, root: Path) -> bool:
    raw_suffix = path.suffix
    suffix = raw_suffix.lower()
    content = _safe_read(path)
    in_test_tree = _in_test_tree(root, path)
    if language == "c":
        return (
            suffix in _C_SOURCE_SUFFIXES
            and raw_suffix != ".C"
            and in_test_tree
            and re.search(r"\b(?:test_\w+|main)\s*\(", content) is not None
            and "assert(" in content
        )
    return (
        (suffix in _CPP_SOURCE_SUFFIXES or raw_suffix == ".C")
        and (in_test_tree or "test" in path.name.lower())
        and (
            re.search(r"\b(?:TEST|TEST_F|TEST_P|TEST_CASE)\s*\(", content) is not None
            or (
                re.search(r"\bmain\s*\(", content) is not None
                and "assert(" in content
            )
        )
    )


def _cmake_native_test_targets(
    files: Iterable[Path], root: Path, language: str | None = None
) -> dict[str, set[str]]:
    """Map CTest-registered executable targets to languages using their test sources.

    Deliberately supports only explicit add_executable source lists and explicit
    add_test/GoogleTest/Catch2 target registration. Dynamic CMake expressions
    are not treated as proof; unsupported layouts fail closed.
    """
    all_files = list(files)
    cmake_files = [path for path in all_files if path.name.lower() == "cmakelists.txt"]
    test_sources = {
        (path.resolve(), "c")
        for path in all_files
        if _native_test_source(path, "c", root)
    } | {
        (path.resolve(), "cpp")
        for path in all_files
        if _native_test_source(path, "cpp", root)
    }
    targets: dict[str, set[str]] = {}
    registrations: set[str] = set()

    for cmake_file in cmake_files:
        for command, args in _cmake_invocations(_safe_read(cmake_file)):
            if not args:
                continue
            if command == "add_executable":
                target = args[0]
                if any(value.upper() in {"ALIAS", "IMPORTED"} for value in args[1:]):
                    continue
                for source in args[1:]:
                    if source.startswith(("$", "<", ">")) or "$<" in source:
                        continue
                    source_path = (cmake_file.parent / source).resolve()
                    for source_path_record, source_language in test_sources:
                        if source_path == source_path_record:
                            targets.setdefault(target, set()).add(source_language)
            elif command == "add_test":
                if args[0].upper() == "NAME":
                    try:
                        command_index = next(i for i, value in enumerate(args) if value.upper() == "COMMAND") + 1
                    except StopIteration:
                        continue
                else:
                    command_index = 1
                if command_index < len(args):
                    registrations.add(args[command_index])
            elif command in {"gtest_discover_tests", "catch_discover_tests"}:
                registrations.add(args[0])

    registered = {
        target: languages
        for target, languages in targets.items()
        if target in registrations and len(languages) == 1
    }
    if language is None:
        return registered
    return {target: languages for target, languages in registered.items() if language in languages}


def _ctest_language_evidence(
    language: str, output: str, files: Iterable[Path], root: Path
) -> tuple[int | None, str]:
    """Count verbose CTest passes only when their executable target proves the language."""
    if _has_skipped_test_output(language, output):
        return None, f"CTest reported skipped {language} test(s); skipped tests are not accepted."
    targets = _cmake_native_test_targets(files, root, language)
    if not targets:
        return None, f"No explicitly registered CMake test executable was mapped to {language} sources."

    starts = list(re.finditer(r"(?m)^\s*Start\s+\d+:\s*(.+?)\s*$", output))
    passed = 0
    for index, start in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(output)
        block = output[start.end():end]
        command_match = re.search(r"(?m)^\s*(?:\d+:\s*)?Test command:\s*(.+?)\s*$", block)
        has_pass_record = re.search(
            r"(?m)^\s*\d+/\d+\s+Test\s+#\d+:.*?\.{3,}\s+Passed\s+\d+(?:\.\d+)?\s+sec\s*$",
            block,
        )
        if not command_match or not has_pass_record:
            continue
        try:
            command_parts = shlex.split(command_match.group(1), posix=(os.name != "nt"))
        except ValueError:
            continue
        if not command_parts or not command_parts[0]:
            continue
        executable = Path(command_parts[0].strip('"')).name
        target_name = re.sub(r"\.exe$", "", executable, flags=re.I)
        if language in targets.get(target_name, set()):
            passed += 1

    if passed:
        return passed, f"CTest verbose records: {passed} passing test(s) executed by {language} test target(s)."
    return None, f"CTest did not report a passing test executed by a registered {language} target."


def _has_test(files: Iterable[Path], language: str, root: Path | None = None) -> bool:
    """Conservative source-level precheck; this is not execution evidence."""
    all_files = list(files)
    base = root.resolve() if root else None
    node_test_files = (
        _node_test_file_paths(base, all_files)
        if language == "javascript" and base is not None
        else set()
    )
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
        elif language == "javascript" and suffix in {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".cts", ".mts"}:
            relative = path.relative_to(base).as_posix() if base else ""
            is_test_file = (
                relative in node_test_files
                or in_test_tree
                or re.search(r"\.(test|spec)\.", name)
            )
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
        elif language in {"c", "cpp"} and suffix in _NATIVE_SOURCE_SUFFIXES:
            if _native_test_source(path, language, base or path.parent):
                if _cmake_native_test_targets(all_files, base or path.parent, language):
                    return True
    return False


def _python_test_paths(root: Path, files: Iterable[Path]) -> list[str]:
    """Return only test files owned by this project unit."""
    paths: list[str] = []
    for path in files:
        if path.suffix.lower() != ".py":
            continue
        name = path.name.lower()
        is_test_file = (
            _in_test_tree(root, path)
            or name.startswith("test_")
            or name.endswith("_test.py")
        )
        if is_test_file and re.search(
            r"\b(?:def\s+test_\w+\s*\(|class\s+Test\w*)",
            _safe_read(path),
        ):
            paths.append(path.relative_to(root).as_posix())
    return sorted(paths)


def _default_plans(root: Path, files: Iterable[Path] | None = None) -> dict[str, list[list[str]]]:
    files = list(files) if files is not None else _project_files(root)
    names = {p.name.lower() for p in files}
    plans: dict[str, list[list[str]]] = {}

    if any(p.suffix.lower() == ".py" for p in files):
        test_paths = _python_test_paths(root, files)
        source_paths = sorted(
            p.relative_to(root).as_posix() for p in files if p.suffix.lower() == ".py"
        )
        plans["python"] = [
            [sys.executable, "-m", "compileall", "-q", *source_paths],
            [sys.executable, "-m", "pytest", "-q", *test_paths],
        ]

    if "cargo.toml" in names or any(p.suffix.lower() == ".rs" for p in files):
        cargo_manifest = root / "Cargo.toml"
        try:
            cargo_data = tomllib.loads(_safe_read(cargo_manifest)) if cargo_manifest.exists() else {}
        except (ValueError, TypeError):
            cargo_data = {}
        workspace = cargo_data.get("workspace")
        if isinstance(workspace, dict):
            plans["rust"] = [["cargo", "test", "--workspace", "--all-targets"]]
        else:
            plans["rust"] = [["cargo", "test", "--all-targets"]]
    if "go.mod" in names:
        plans["go"] = [["go", "test", "-json", "./..."]]

    js_project = "package.json" in names and any(
        p.suffix.lower() in {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".cts", ".mts"} for p in files
    )
    if js_project:
        # Never delegate test execution to `npm test`: package.json scripts are
        # arbitrary shell programs and can forge a passing summary. Invoke a
        # recognized runner directly, or use Node's built-in test runner.
        runner_dependencies: set[str] = set()
        for package_file in (p for p in files if p.name.lower() == "package.json"):
            try:
                package_data = json.loads(_safe_read(package_file))
            except (ValueError, TypeError):
                continue
            if not isinstance(package_data, dict):
                continue
            for section in ("dependencies", "devDependencies", "optionalDependencies"):
                dependencies = package_data.get(section, {})
                if isinstance(dependencies, dict):
                    runner_dependencies.update(dependencies)
        runner_commands = {
            "vitest": ["npx", "--no-install", "vitest", "run"],
            "jest": ["npx", "--no-install", "jest", "--runInBand"],
            "mocha": [
                "npx", "--no-install", "mocha",
                "--no-dry-run", "--pass-on-failing-test-suite=false",
                "--forbid-pending", "--forbid-only", "--fail-zero",
            ],
            "tap": ["npx", "--no-install", "tap"],
        }
        js_commands = [
            runner_commands[name]
            for name in ("vitest", "jest", "mocha", "tap")
            if name in runner_dependencies
        ]
        if not js_commands:
            # Execute exactly the pre-discovered files that Polyguard snapshots
            # for file-level JUnit filtering. Do not let Node discover additional
            # tests in generated or otherwise-pruned directories such as dist/.
            node_test_files = sorted(_node_test_file_paths(root, files))
            if node_test_files:
                js_commands = [[
                    "node", "--test", "--test-reporter=junit", *(str((root / path).resolve()) for path in node_test_files),
                ]]
        if "tsconfig.json" in names:
            js_commands.append(["npx", "--no-install", "tsc", "--noEmit"])
        plans["javascript"] = js_commands

    java_build = names.intersection({
        "pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"
    })
    if java_build:
        windows = sys.platform.startswith("win")
        if "pom.xml" in names:
            wrapper = "mvnw.cmd" if windows else "./mvnw"
            command = (
                ["cmd", "/c", wrapper, "-B", "clean", "test"]
                if windows and "mvnw.cmd" in names
                else [wrapper, "-B", "clean", "test"]
                if "mvnw" in names or "mvnw.cmd" in names
                else ["mvn", "-B", "clean", "test"]
            )
        else:
            wrapper = "gradlew.bat" if windows else "./gradlew"
            command = (
                ["cmd", "/c", wrapper, "clean", "test", "--no-daemon"]
                if windows and "gradlew.bat" in names
                else [wrapper, "clean", "test", "--no-daemon"]
                if "gradlew" in names or "gradlew.bat" in names
                else ["gradle", "clean", "test", "--no-daemon"]
            )
        plans["java"] = [command]

    if any(p.suffix.lower() in {".csproj", ".sln"} for p in files):
        plans["csharp"] = [[
            "dotnet", "test",
            "--logger", "trx;LogFilePrefix=polyguard",
            "--results-directory", ".polyguard-results",
        ]]

    if "composer.json" in names:
        phpunit = root / "vendor" / "bin" / "phpunit"
        test_target = "tests" if (root / "tests").is_dir() else "."
        if phpunit.exists():
            plans["php"] = [[
                "php", str(phpunit), "--log-junit",
                ".polyguard-results/phpunit.xml", test_target,
            ]]
        else:
            plans["php"] = [[
                "phpunit", "--log-junit",
                ".polyguard-results/phpunit.xml", test_target,
            ]]

    if "gemfile" in names or any(p.suffix.lower() == ".gemspec" for p in files):
        if any(p.name.lower().endswith("_spec.rb") for p in files):
            plans["ruby"] = [["bundle", "exec", "rspec"]]
        else:
            plans["ruby"] = [[
                "ruby", "-Itest", "-e",
                "require 'minitest/autorun'; Dir.glob('test/**/*_test.rb').each { |file| require File.expand_path(file) }",
            ]]

    if "package.swift" in names:
        plans["swift"] = [["swift", "test", "--parallel"]]

    cmake_files = [p for p in files if p.name.lower() == "cmakelists.txt"]
    if cmake_files:
        cmake_commands = [
            ["cmake", "-S", ".", "-B", ".polyguard-build", "-DBUILD_TESTING=ON"],
            ["cmake", "--build", ".polyguard-build", "--parallel"],
            ["ctest", "--test-dir", ".polyguard-build", "--output-on-failure", "-V"],
        ]
        if any(p.suffix.lower() == ".c" for p in files):
            plans["c"] = cmake_commands
        if any(p.suffix.lower() in {".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx"} for p in files):
            plans["cpp"] = cmake_commands
    return plans


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _xml_local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _maven_module_roots(root: Path, files: list[Path]) -> set[Path]:
    module_roots: set[Path] = set()
    for pom in (path for path in files if path.name.lower() == "pom.xml"):
        try:
            document = ET.parse(pom).getroot()
        except (ET.ParseError, OSError):
            continue
        for node in document.iter():
            if _xml_local_name(node.tag) != "module" or not node.text or not node.text.strip():
                continue
            candidate = (pom.parent / node.text.strip()).resolve()
            if _is_within(candidate, root.resolve()) and candidate.is_dir():
                module_roots.add(candidate)
    return module_roots


def _cargo_workspace_member_roots(root: Path, files: list[Path]) -> set[Path]:
    member_roots: set[Path] = set()
    manifests = (path for path in files if path.name.lower() == "cargo.toml")
    for manifest in manifests:
        try:
            data = tomllib.loads(_safe_read(manifest))
        except (ValueError, TypeError):
            continue
        workspace = data.get("workspace", {})
        members = workspace.get("members", []) if isinstance(workspace, dict) else []
        if not isinstance(members, list):
            continue
        for pattern in members:
            if not isinstance(pattern, str) or ".." in Path(pattern).parts:
                continue
            try:
                matches = manifest.parent.glob(pattern)
            except (OSError, ValueError):
                continue
            for candidate in matches:
                candidate = candidate.resolve()
                if _is_within(candidate, root.resolve()) and (candidate / "Cargo.toml").is_file():
                    member_roots.add(candidate)
    return member_roots


def _npm_workspace_roots(root: Path, files: list[Path]) -> set[Path]:
    workspace_roots: set[Path] = set()
    package_files = (path for path in files if path.name.lower() == "package.json")
    for package_file in package_files:
        try:
            data = json.loads(_safe_read(package_file))
        except (ValueError, TypeError):
            continue
        spec = data.get("workspaces", [])
        patterns = spec.get("packages", []) if isinstance(spec, dict) else spec
        if not isinstance(patterns, list):
            continue
        for pattern in patterns:
            if not isinstance(pattern, str) or ".." in Path(pattern).parts:
                continue
            try:
                matches = package_file.parent.glob(pattern)
            except (OSError, ValueError):
                continue
            for candidate in matches:
                candidate = candidate.resolve()
                if (
                    candidate.is_dir()
                    and _is_within(candidate, root.resolve())
                    and (candidate / "package.json").is_file()
                ):
                    workspace_roots.add(candidate)
    return workspace_roots


def _gradle_member_roots(root: Path, files: list[Path]) -> set[Path]:
    """Resolve only projects explicitly included by Gradle settings files."""
    members: set[Path] = set()
    for settings in (path for path in files if path.name.lower() in {"settings.gradle", "settings.gradle.kts"}):
        content = _safe_read(settings)
        project_dirs: dict[str, str] = {}
        for match in re.finditer(
            r"project\s*\(\s*['\"](:[^'\"]+)['\"]\s*\)\s*\.projectDir\s*=\s*file\s*\(\s*['\"]([^'\"]+)['\"]\s*\)",
            content,
        ):
            project_dirs[match.group(1)] = match.group(2)
        include_arguments = [
            match.group(1)
            for match in re.finditer(r"\binclude\s*\((.*?)\)", content, re.DOTALL)
        ]
        include_arguments.extend(
            match.group(1)
            for match in re.finditer(r"\binclude\s+([^\n]+)", content)
        )
        for arguments in include_arguments:
            for project_path in re.findall(r"['\"](:[^'\"]+)['\"]", arguments):
                relative = project_dirs.get(project_path, project_path.lstrip(":").replace(":", "/"))
                candidate = (settings.parent / relative).resolve()
                if _is_within(candidate, root.resolve()) and candidate.is_dir():
                    members.add(candidate)
    return members


def _dotnet_solution_member_roots(root: Path, files: list[Path]) -> set[Path]:
    """Resolve C# projects actually listed in a Visual Studio solution."""
    members: set[Path] = set()
    project_line = re.compile(
        r'^Project\([^\r\n]*?\)\s*=\s*"[^"]+",\s*"([^"]+\.csproj)"',
        re.IGNORECASE | re.MULTILINE,
    )
    for solution in (path for path in files if path.suffix.lower() == ".sln"):
        content = _safe_read(solution)
        for relative in project_line.findall(content):
            candidate = (solution.parent / relative.replace("\\", "/")).resolve().parent
            project_file = candidate / Path(relative.replace("\\", "/")).name
            if _is_within(candidate, root.resolve()) and project_file.is_file():
                members.add(candidate)
    return members


def _discover_project_units(
    root: Path, configured: dict[str, LanguageConfig] | None = None
) -> list[ProjectUnit]:
    """Discover independently executable packages without double-running declared workspaces."""
    root = root.resolve()
    files = _project_files(root)
    marker_roots: dict[str, set[Path]] = {}

    for path in files:
        name = path.name.lower()
        language: str | None = None
        for candidate, markers in PROJECT_MARKERS.items():
            if name in markers:
                language = candidate
                break
            if any(marker.startswith(".") and path.suffix.lower() == marker for marker in markers):
                language = candidate
                break
        if name == "cmakelists.txt":
            marker_roots.setdefault("c", set()).add(path.parent.resolve())
            marker_roots.setdefault("cpp", set()).add(path.parent.resolve())
        elif language:
            marker_roots.setdefault(language, set()).add(path.parent.resolve())

    # Aggregator manifests own their declared nested packages. Run the native
    # workspace/solution command once at the aggregator root, not once per child.
    maven_modules = _maven_module_roots(root, files)
    cargo_members = _cargo_workspace_member_roots(root, files)
    npm_workspaces = _npm_workspace_roots(root, files)
    gradle_members = _gradle_member_roots(root, files)
    solution_members = _dotnet_solution_member_roots(root, files)

    units: set[ProjectUnit] = set()
    for language, project_roots in marker_roots.items():
        for project_root in project_roots:
            if language == "java":
                if project_root in maven_modules or project_root in gradle_members:
                    continue
            elif language == "rust" and project_root in cargo_members:
                continue
            elif language == "javascript" and project_root in npm_workspaces:
                continue
            elif language == "csharp" and project_root in solution_members:
                continue

            if language in {"c", "cpp"}:
                nested_cmake_roots = {
                    candidate for candidate in marker_roots.get(language, set())
                    if candidate != project_root and _is_within(candidate, project_root)
                }
                local_files = [
                    path for path in files
                    if _is_within(path, project_root)
                    and not any(_is_within(path, nested) for nested in nested_cmake_roots)
                ]
                if language == "c":
                    has_sources = any(path.suffix.lower() == ".c" for path in local_files)
                else:
                    has_sources = any(
                        path.suffix.lower() in {".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx"}
                        for path in local_files
                    )
                if has_sources:
                    units.add(ProjectUnit(project_root, language))
            else:
                units.add(ProjectUnit(project_root, language))

    # Preserve source-only Python/Rust projects without a package manifest.
    source_suffixes = {"python": {".py"}, "rust": {".rs"}}
    for language, suffixes in source_suffixes.items():
        covered_roots = marker_roots.get(language, set())
        uncovered = [
            path for path in files
            if path.suffix.lower() in suffixes
            and not any(_is_within(path, candidate) for candidate in covered_roots)
        ]
        if uncovered:
            units.add(ProjectUnit(root, language))

    # Keep every independently declared project root. A parent manifest is not
    # evidence of a workspace/aggregator: if it has no local tests, run_checks()
    # must report that project as FAIL instead of silently dropping it merely
    # because a nested package has tests. Known native aggregators were handled
    # explicitly above.
    return sorted(units, key=lambda unit: (str(unit.root), unit.language))

def _files_for_unit(
    root: Path, unit: ProjectUnit, units: list[ProjectUnit], files: list[Path] | None = None
) -> list[Path]:
    files = files if files is not None else _project_files(root)
    nested_roots = {
        other.root for other in units
        if other.root != unit.root
        and other.language == unit.language
        and _is_within(other.root, unit.root)
    }
    return [
        path for path in files
        if _is_within(path, unit.root)
        and _is_within(path.resolve(), unit.root.resolve())
        and not any(_is_within(path, nested) for nested in nested_roots)
    ]


def discover(root: Path) -> list[tuple[str, list[list[str]]]]:
    """Return detected language projects and their default commands."""
    root = root.expanduser().resolve()
    units = _discover_project_units(root)
    files = _project_files(root)
    return [
        (unit.language, _default_plans(unit.root, _files_for_unit(root, unit, units, files)).get(unit.language, []))
        for unit in units
    ]


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
        allowed = {"commands", "test_patterns"}
        if not isinstance(section, dict) or set(section) - allowed:
            if isinstance(section, dict) and "success_patterns" in section:
                raise ValueError(
                    f"[checks.{raw_language}].success_patterns is no longer supported: "
                    "custom output regexes can manufacture a false PASS. "
                    "Use a built-in runner with recognized execution evidence."
                )
            raise ValueError(
                f"[checks.{raw_language}] supports only 'commands' and 'test_patterns'."
            )
        commands = section.get("commands")
        patterns = section.get("test_patterns")
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
        configured[language] = LanguageConfig(normalized_commands, patterns)
        required.add(language)

    return required, configured


def _matching_config_tests(
    root: Path, patterns: list[str], files: Iterable[Path] | None = None
) -> bool:
    allowed = {path.resolve() for path in files} if files is not None else None
    for pattern in patterns:
        try:
            for path in root.glob(pattern):
                if not path.is_file():
                    continue
                if allowed is None or path.resolve() in allowed:
                    return True
        except (OSError, ValueError, RuntimeError):
            continue
    return False


def _node_test_file_paths(root: Path, files: Iterable[Path]) -> set[str]:
    """Snapshot files matched by Node's documented default test discovery globs."""
    matched: set[str] = set()
    supported_suffixes = {".js", ".cjs", ".mjs", ".ts", ".cts", ".mts"}
    for path in files:
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:
            continue
        parts = relative.replace("\\", "/").split("/")
        filename = parts[-1]
        suffix = Path(filename).suffix
        if suffix not in supported_suffixes:
            continue
        if "test" in parts[:-1]:
            matched.add(relative)
            continue
        stem = filename[:-len(suffix)]
        if (
            stem == "test"
            or stem.startswith("test-")
            or stem.endswith((".test", "-test", "_test"))
        ):
            matched.add(relative)
    return matched


def _has_skipped_test_output(language: str, output: str) -> bool:
    """Detect runner-reported skipped, pending, or ignored tests in text output."""
    patterns = {
        "python": [r"\b[1-9]\d*\s+(?:skipped|deselected)\b"],
        "rust": [r"\b[1-9]\d*\s+(?:ignored|filtered out)\b"],
        "javascript": [
            r"\b[1-9]\d*\s+(?:skipped|pending|todo|deselected)\b",
            r"(?m)^\s*#\s*(?:skip|skipped|todo)\s+[1-9]\d*\s*$",
            r"(?im)^\s*ok\s+\d+[^\r\n]*#\s*(?:SKIP|TODO)\b",
        ],
        "java": [r"\bSkipped:\s*[1-9]\d*\b", r"\b[1-9]\d*\s+skipped\b"],
        "csharp": [r"\bSkipped:\s*[1-9]\d*\b", r"\b[1-9]\d*\s+skipped\b"],
        "php": [
            r"\b[1-9]\d*\s+skipped\b",
            r"\b[1-9]\d*\s+skips?\b",
            r"\bSkipped:\s*[1-9]\d*\b",
            r"\bIncomplete:\s*[1-9]\d*\b",
        ],
        "ruby": [
            r"\b[1-9]\d*\s+(?:skips?|skipped|pending)\b",
        ],
        "swift": [
            r"\b[1-9]\d*\s+(?:tests?\s+)?skipped\b",
            r"\bTest(?:\s+Case)?\b[^\r\n]*\bskipped\b",
        ],
        "c": [
            r"(?im)^\s*\d+/\d+\s+Test\s+#\d+:.*\.{3,}\s+\*{3}(?:Skipped|Not Run)\b",
            r"(?im)^\s*The following tests did not run:\s*$",
        ],
        "cpp": [
            r"(?im)^\s*\d+/\d+\s+Test\s+#\d+:.*\.{3,}\s+\*{3}(?:Skipped|Not Run)\b",
            r"(?im)^\s*The following tests did not run:\s*$",
        ],
    }
    return any(
        re.search(pattern, output, re.IGNORECASE | re.MULTILINE)
        for pattern in patterns.get(language, [])
    )


def _javascript_junit_evidence(
    output: str,
    project_root: Path | None,
    node_test_files: set[str],
) -> tuple[int | None, str]:
    """Count actual Node test cases, not the file-level pass emitted for empty suites."""
    xml_start = output.index("<?xml")
    try:
        document = ET.fromstring(output[xml_start:])
    except ET.ParseError as exc:
        return None, f"Node test runner emitted invalid JUnit XML: {exc}"
    if _xml_local_name(document.tag) not in {"testsuites", "testsuite"}:
        return None, "Node JUnit report has an unexpected root element."

    for element in document.iter():
        try:
            skipped_count = int(element.attrib.get("skipped", "0"))
        except (TypeError, ValueError):
            return None, "Node JUnit report contains an invalid skipped-test count."
        if skipped_count > 0:
            return None, "Node JUnit report contains skipped test case(s)."

    cases = [node for node in document.iter() if _xml_local_name(node.tag) == "testcase"]
    if not cases:
        return None, "Node JUnit report contains no test-case records."

    passed = 0
    for case in cases:
        name = case.attrib.get("name", "")
        if not name or not name.strip():
            return None, "Node JUnit report contains a testcase with a missing or empty name."
        # Node emits file-level testcase records for empty test files. Use
        # the file list captured before execution, because a test may delete
        # itself before its JUnit report is parsed.
        normalized_name = name.replace("\\", "/").strip()
        candidate = Path(normalized_name)
        if project_root is not None and candidate.is_absolute():
            try:
                normalized_name = candidate.relative_to(project_root.resolve()).as_posix()
            except ValueError:
                normalized_name = candidate.as_posix()
        while normalized_name.startswith("./"):
            normalized_name = normalized_name[2:]
        if normalized_name in node_test_files:
            continue

        child_names = {_xml_local_name(child.tag) for child in list(case)}
        if "skipped" in child_names:
            return None, "Node JUnit report contains skipped test case(s)."
        if {"failure", "error"}.intersection(child_names):
            continue
        passed += 1

    if passed:
        return passed, f"Node JUnit report: {passed} passing test case(s)."
    return None, "Node JUnit report contained no passing test cases outside test-file records."


def _execution_evidence(
    language: str,
    output: str,
    project_root: Path | None = None,
    node_test_files: set[str] | None = None,
) -> tuple[int | None, str]:
    """Parse a positive successful-test count from a built-in runner summary."""
    if language == "javascript" and "<?xml" in output:
        return _javascript_junit_evidence(output, project_root, node_test_files or set())
    if language != "go" and _has_skipped_test_output(language, output):
        return None, f"{language} runner reported skipped, pending, or ignored tests."
    if language == "go":
        passed = 0
        skipped = False
        for line in output.splitlines():
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(event, dict):
                return None, "go test JSON event must be an object."
            action = event.get("Action")
            if not isinstance(action, str) or action not in {
                "start", "run", "pause", "cont", "pass", "bench", "fail",
                "output", "skip",
            }:
                return None, "go test JSON event has an invalid Action field."
            if "Package" in event and not isinstance(event["Package"], str):
                return None, "go test JSON event has an invalid Package field."
            if "Test" in event and not isinstance(event["Test"], str):
                return None, "go test JSON event has an invalid Test field."
            if "Elapsed" in event and (
                isinstance(event["Elapsed"], bool)
                or not isinstance(event["Elapsed"], (int, float))
                or not 0 <= event["Elapsed"] < float("inf")
            ):
                return None, "go test JSON event has an invalid Elapsed field."
            if action == "skip":
                skipped = True
            if action == "pass" and isinstance(event.get("Test"), str) and event.get("Test"):
                passed += 1
        if skipped:
            return None, "go test JSON reported skipped test(s)."
        if passed:
            return passed, f"go test JSON: {passed} passing test event(s)"
        return None, "go test JSON contained no passing test events."

    patterns = DEFAULT_SUCCESS_PATTERNS.get(language, [])
    counts: list[tuple[str, int]] = []
    for pattern in patterns:
        count_for_pattern = 0
        for match in re.finditer(pattern, output):
            try:
                count = int(match.group(1)) if match.lastindex else 1
            except (TypeError, ValueError):
                count = 0
            if count > 0:
                count_for_pattern += count
        if count_for_pattern > 0:
            counts.append((pattern, count_for_pattern))
    if not counts:
        return None, "No positive successful-test count was found in runner output."
    # Alternative summary and per-test output patterns may describe the same
    # tests; take the strongest single count rather than double-counting them.
    total = max(count for _, count in counts)
    return total, f"Matched {len(counts)} execution-evidence pattern(s); {total} test(s) reported passing."


JS_TEST_RUNNERS = frozenset({"vitest", "jest", "mocha", "tap"})


def _javascript_runner_evidence(
    runner_name: str,
    output: str,
    project_root: Path,
    node_test_files: set[str],
) -> tuple[int | None, str]:
    """Require independent positive evidence and reject any reported test failures."""
    if runner_name not in JS_TEST_RUNNERS:
        return None, f"Unsupported JavaScript test runner: {runner_name}."
    passed, evidence = _execution_evidence(
        "javascript", output, project_root, node_test_files
    )
    if passed is None:
        return None, f"{runner_name} did not report a positive passing-test count: {evidence}"
    if re.search(r"\b[1-9]\d*\s+(?:failed|failing)\b", output, re.IGNORECASE) or re.search(
        r"(?m)^\s*#\s*fail\s+[1-9]\d*\s*$", output
    ):
        return None, f"{runner_name} reported one or more failing tests."
    return passed, f"{runner_name}: {evidence}"


MAX_COMMAND_OUTPUT_BYTES = 256 * 1024


NATIVE_REPORT_LANGUAGES = {"java", "csharp", "php"}


def _as_nonnegative_int(value: object) -> int:
    try:
        return max(0, int(str(value)))
    except (TypeError, ValueError):
        return 0


def _parse_junit_xml(path: Path) -> tuple[int, int, int]:
    """Return (tests, failures+errors, skipped) from concrete JUnit/xUnit cases.

    Testcase records are authoritative. Aggregate suite counters alone are not
    accepted because they can describe an empty, stale, or skip-only suite.
    """
    root = ET.parse(path).getroot()
    root_name = _xml_local_name(root.tag)
    if root_name not in {"testsuite", "testsuites"}:
        raise ValueError(f"Unexpected JUnit root element: {root_name}.")
    suites = [root] if root_name == "testsuite" else [
        node for node in list(root) if _xml_local_name(node.tag) == "testsuite"
    ]
    if not suites:
        raise ValueError("JUnit report contains no testsuite elements.")
    cases = []
    for suite in suites:
        counters = {}
        for key in ("tests", "failures", "errors", "skipped"):
            raw = suite.attrib.get(key)
            if raw is None or not raw.isdigit():
                raise ValueError(f"JUnit testsuite has an invalid {key} counter.")
            counters[key] = int(raw)
        suite_cases = [
            node for node in suite.iter() if _xml_local_name(node.tag) == "testcase"
        ]
        if counters["tests"] != len(suite_cases):
            raise ValueError("JUnit testsuite test counter does not match testcase records.")
        actual_failures = sum(
            any(_xml_local_name(child.tag) == "failure" for child in list(case))
            for case in suite_cases
        )
        actual_errors = sum(
            any(_xml_local_name(child.tag) == "error" for child in list(case))
            for case in suite_cases
        )
        actual_skipped = sum(
            any(_xml_local_name(child.tag) == "skipped" for child in list(case))
            for case in suite_cases
        )
        if counters["failures"] != actual_failures or counters["errors"] != actual_errors:
            raise ValueError("JUnit failure/error counters do not match testcase records.")
        if counters["skipped"] != actual_skipped:
            raise ValueError("JUnit skipped counter does not match testcase records.")
        cases.extend(suite_cases)
    tests = len(cases)
    failures = skipped = 0
    for case in cases:
        child_names = {_xml_local_name(child.tag) for child in list(case)}
        if "failure" in child_names or "error" in child_names:
            failures += 1
        elif "skipped" in child_names:
            skipped += 1
    return tests, failures, skipped


def _parse_trx(path: Path) -> tuple[int, int, int]:
    """Return (tests, failures+errors, skipped) from a VSTest TRX file."""
    root = ET.parse(path).getroot()
    if _xml_local_name(root.tag) != "testrun":
        raise ValueError("TRX root element must be TestRun.")
    summaries = [node for node in list(root) if _xml_local_name(node.tag) == "resultsummary"]
    if len(summaries) != 1:
        raise ValueError("TRX report must contain exactly one ResultSummary.")
    counter_nodes = [node for node in list(summaries[0]) if _xml_local_name(node.tag) == "counters"]
    if len(counter_nodes) != 1:
        raise ValueError("TRX ResultSummary must contain exactly one Counters element.")
    counters = counter_nodes[0]
    attrs = {key.lower(): value for key, value in counters.attrib.items()}
    if any(not attrs.get(key, "").isdigit() for key in ("total", "passed", "failed")):
        raise ValueError("TRX required counters are missing or invalid.")
    if any(not value.isdigit() for value in attrs.values()):
        raise ValueError("TRX counters contain a negative or non-integer value.")
    total = _as_nonnegative_int(attrs.get("total"))
    passed = _as_nonnegative_int(attrs.get("passed"))
    failed = sum(
        _as_nonnegative_int(attrs.get(key))
        for key in ("failed", "error", "aborted", "timeout", "passedbutrunaborted", "disconnected")
    )
    skipped = sum(
        _as_nonnegative_int(attrs.get(key))
        for key in ("notexecuted", "inconclusive", "notrunnable")
    )
    if total <= 0:
        raise ValueError("TRX report contains zero tests.")
    if passed + failed + skipped != total:
        raise ValueError(
            "TRX counters do not reconcile: "
            f"total={total}, passed={passed}, failed={failed}, skipped={skipped}."
        )
    result_collections = [node for node in list(root) if _xml_local_name(node.tag) == "results"]
    if len(result_collections) != 1:
        raise ValueError("TRX TestRun must contain exactly one Results collection.")
    result_nodes = list(result_collections[0])
    if any(_xml_local_name(node.tag) != "unittestresult" for node in result_nodes):
        raise ValueError("TRX Results contains an unexpected child element.")
    actual = {"passed": 0, "failed": 0, "skipped": 0}
    for result in result_nodes:
        outcome = result.attrib.get("outcome", "").lower()
        if outcome == "passed":
            actual["passed"] += 1
        elif outcome in {"failed", "error", "aborted", "timeout", "passedbutrunaborted", "disconnected"}:
            actual["failed"] += 1
        elif outcome in {"notexecuted", "inconclusive", "notrunnable"}:
            actual["skipped"] += 1
        else:
            raise ValueError(f"TRX UnitTestResult has an unknown outcome: {outcome!r}.")
    if len(result_nodes) != total or (
        actual["passed"] != passed or actual["failed"] != failed or actual["skipped"] != skipped
    ):
        raise ValueError("TRX UnitTestResult records do not match Counters.")
    return total, failed, skipped


def _prepare_result_area(root: Path, language: str) -> None:
    """Create the result directory and remove only stale Polyguard report files."""
    if language not in NATIVE_REPORT_LANGUAGES:
        return
    report_dir = root / ".polyguard-results"
    if report_dir.is_symlink():
        raise OSError("Refusing to write through a symlink at .polyguard-results.")
    if report_dir.exists() and not report_dir.is_dir():
        raise OSError(".polyguard-results exists but is not a directory.")
    report_dir.mkdir(parents=True, exist_ok=True)

    owned_patterns = {
        "csharp": ("polyguard*.trx",),
        "php": ("phpunit.xml",),
    }.get(language, ())
    for pattern in owned_patterns:
        for stale_report in report_dir.glob(pattern):
            if stale_report.is_symlink() or not stale_report.is_file():
                raise OSError(f"Refusing to remove non-regular report path: {stale_report.name}")
            stale_report.unlink()



def _native_report_paths(language: str, root: Path) -> list[Path]:
    """Return report locations owned by the native adapter for this language."""
    if language == "java":
        return [
            path for path in root.rglob("TEST-*.xml")
            if "surefire-reports" in path.parts or "test-results" in path.parts
        ]
    if language == "csharp":
        return list((root / ".polyguard-results").rglob("*.trx"))
    if language == "php":
        return [root / ".polyguard-results" / "phpunit.xml"]
    return []


def _report_fingerprint(path: Path) -> tuple[int, int, int, int, str]:
    stat = path.stat()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return (stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, digest)


def _snapshot_native_reports(language: str, root: Path) -> dict[Path, tuple[int, int, int, int, str]]:
    """Capture report identities before execution; avoids filesystem clock precision bugs."""
    snapshot: dict[Path, tuple[int, int, int, int, str]] = {}
    for path in _native_report_paths(language, root):
        try:
            if path.is_file() and not path.is_symlink():
                snapshot[path.resolve()] = _report_fingerprint(path)
        except OSError:
            continue
    return snapshot

def _native_report_evidence(
    language: str,
    root: Path,
    unit: ProjectUnit,
    units: list[ProjectUnit],
    baseline_reports: dict[Path, tuple[int, int, int, int, str]],
) -> tuple[int | None, int | None, int | None, int | None, str]:
    """Read fresh native runner reports; never infer a pass count from a file name."""
    if language not in NATIVE_REPORT_LANGUAGES:
        return None, None, None, None, "No native report adapter is registered."
    candidates = _native_report_paths(language, root)

    nested_roots = {
        other.root for other in units
        if other.root != unit.root
        and other.language == unit.language
        and _is_within(other.root, unit.root)
    }
    fresh_candidates: list[Path] = []
    for path in candidates:
        try:
            if not path.is_file() or path.is_symlink():
                continue
            resolved = path.resolve()
            if not _is_within(resolved, unit.root.resolve()):
                continue
            if any(_is_within(resolved, nested) for nested in nested_roots):
                continue
            if baseline_reports.get(resolved) != _report_fingerprint(path):
                fresh_candidates.append(path)
        except OSError:
            continue
    candidates = fresh_candidates
    if not candidates:
        return None, None, None, None, f"No fresh {language} test report was produced."

    total = failed = skipped = 0
    for report_path in sorted(set(candidates)):
        try:
            report_total, report_failed, report_skipped = (
                _parse_trx(report_path) if language == "csharp" else _parse_junit_xml(report_path)
            )
        except (ET.ParseError, OSError, ValueError) as exc:
            return None, None, None, None, f"Invalid {language} test report {report_path.name}: {exc}"
        total += report_total
        failed += report_failed
        skipped += report_skipped

    passed = max(0, total - failed - skipped)
    relative_reports = [
        path.resolve().relative_to(unit.root.resolve()).as_posix()
        for path in sorted(set(candidates))
    ]
    evidence = (
        f"{language} XML report(s): {', '.join(relative_reports)}; "
        f"passed={passed}, failed={failed}, skipped={skipped}, total={total}."
    )
    return passed, failed, skipped, total, evidence


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    """Terminate the command and descendants created inside its process group/tree."""
    if os.name == "posix":
        try:
            # _run_command starts each POSIX command in a fresh session, so the
            # process PID is also the process-group ID. Kill the group even when
            # its leader exited but a child still holds an inherited output pipe.
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            if process.poll() is None:
                process.kill()
        return

    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    if process.poll() is None:
        process.kill()


def _run_command(
    command: list[str],
    *,
    cwd: Path,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    """Run one command with bounded output capture and process-tree timeout cleanup."""
    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        shell=False,
        start_new_session=(os.name == "posix"),
    )
    output_tail = bytearray()
    reader_errors: list[OSError] = []

    def drain_output() -> None:
        assert process.stdout is not None
        try:
            while True:
                chunk = process.stdout.read(8192)
                if not chunk:
                    break
                output_tail.extend(chunk)
                overflow = len(output_tail) - MAX_COMMAND_OUTPUT_BYTES
                if overflow > 0:
                    del output_tail[:overflow]
        except (OSError, ValueError) as exc:
            reader_errors.append(OSError(str(exc)))

    reader = Thread(target=drain_output, name="polyguard-output-reader", daemon=True)
    reader.start()
    deadline = time.monotonic() + timeout
    try:
        process.wait(timeout=timeout)
        reader.join(timeout=max(0.0, deadline - time.monotonic()))
        if reader.is_alive():
            # The main process may have exited while a descendant still holds
            # the inherited output pipe open. This remains a command timeout.
            raise subprocess.TimeoutExpired(command, timeout)
    except subprocess.TimeoutExpired as exc:
        _terminate_process_tree(process)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        reader.join(timeout=10)
        if reader.is_alive() and process.stdout is not None:
            process.stdout.close()
            reader.join(timeout=1)
        output = output_tail.decode("utf-8", errors="replace")
        raise subprocess.TimeoutExpired(command, timeout, output=output) from exc

    if reader_errors:
        raise OSError(f"Could not capture command output: {reader_errors[0]}")
    output = output_tail.decode("utf-8", errors="replace")
    return subprocess.CompletedProcess(command, process.returncode, output)


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

    units = _discover_project_units(root, configured)
    for language in required:
        if not any(unit.language == language for unit in units):
            units.append(ProjectUnit(root, language))
    units = sorted(set(units), key=lambda unit: (str(unit.root), unit.language))
    if not units:
        return _failure_report("No supported language project was detected.", "project")

    results: list[CheckResult] = []
    all_files = _project_files(root)
    for unit in units:
        language = unit.language
        unit_files = _files_for_unit(root, unit, units, all_files)
        node_test_files = (
            _node_test_file_paths(unit.root, unit_files)
            if language == "javascript"
            else set()
        )
        default_commands = _default_plans(unit.root, unit_files).get(language, [])
        language_config = configured.get(language)
        commands = language_config.commands if language_config else default_commands
        # User-defined commands are executable project input, not trusted test
        # evidence. Only the built-in command plan may earn PASS from recognized
        # stdout summaries or native reports; otherwise a command can simply
        # print a forged "1 passed" line and exit zero.
        custom_commands = language_config is not None and commands != default_commands
        project_root = str(unit.root)

        if language_config:
            has_tests = _matching_config_tests(unit.root, language_config.test_patterns, unit_files)
            test_reason = "Configured test_patterns did not match any files."
        else:
            has_tests = _has_test(unit_files, language, unit.root)
            test_reason = "No test suite was detected; fail-closed policy rejects untested code."
        if not has_tests:
            results.append(CheckResult(
                language, "FAIL", commands, 2, 0.0, test_reason,
                project_root=project_root,
            ))
            continue

        if not commands:
            results.append(CheckResult(
                language, "FAIL", [], 2, 0.0,
                "Language was required by configuration but no check commands were provided.",
                project_root=project_root,
            ))
            continue

        try:
            _prepare_result_area(unit.root, language)
        except OSError as exc:
            results.append(CheckResult(
                language, "FAIL", commands, 126, 0.0,
                f"Could not prepare Polyguard result directory: {exc}",
                project_root=project_root,
            ))
            continue

        report_baseline = _snapshot_native_reports(language, unit.root)
        started = time.monotonic()
        last_code: int | None = 0
        output_parts: list[str] = []
        js_runner_evidence: list[tuple[str, int, str]] = []
        failure_reason = ""
        for command in commands:
            executable = command[0]
            executable_path: Path | None = None
            if Path(executable).is_absolute():
                executable_path = Path(executable)
            elif "/" in executable or (executable.startswith(".") and "\\" in executable):
                executable_path = (unit.root / executable).resolve()
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
                completed = _run_command(command, cwd=unit.root, timeout=timeout)
                last_code = completed.returncode
                if completed.stdout:
                    output_parts.append(completed.stdout[-12000:])
                if last_code != 0:
                    failure_reason = f"Command failed with exit code {last_code}: {' '.join(command)}"
                    break
                if (
                    language == "javascript"
                    and not custom_commands
                    and len(command) > 2
                    and command[0] == "npx"
                    and command[1] == "--no-install"
                    and command[2] in JS_TEST_RUNNERS
                ):
                    runner_passed, runner_evidence = _javascript_runner_evidence(
                        command[2], completed.stdout, unit.root, node_test_files
                    )
                    if runner_passed is None:
                        failure_reason = (
                            f"JavaScript {command[2]} did not independently verify its test run: "
                            f"{runner_evidence}"
                        )
                        break
                    js_runner_evidence.append(
                        (command[2], runner_passed, runner_evidence)
                    )
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
        if passed and custom_commands:
            passed = False
            failure_reason = (
                "Custom commands cannot establish trusted test-execution evidence. "
                "Use the built-in command plan; custom command output and generated "
                "reports are not accepted as proof of test execution."
            )
        tests_passed: int | None = None
        tests_failed: int | None = None
        tests_skipped: int | None = None
        tests_total: int | None = None
        execution_evidence = ""
        if passed:
            if language in NATIVE_REPORT_LANGUAGES:
                (
                    tests_passed, tests_failed, tests_skipped, tests_total,
                    execution_evidence,
                ) = _native_report_evidence(
                    language, unit.root, unit, units, report_baseline
                )
                if tests_passed is None or tests_total is None:
                    passed = False
                    failure_reason = (
                        execution_evidence
                        or f"Could not verify native {language} test report."
                    )
                elif tests_passed <= 0 or tests_failed > 0 or tests_skipped > 0:
                    passed = False
                    failure_reason = (
                        f"{language} report did not establish a passing test suite: "
                        f"passed={tests_passed}, failed={tests_failed}, skipped={tests_skipped}, "
                        f"total={tests_total}."
                    )
            else:
                if language == "javascript" and js_runner_evidence:
                    tests_passed = sum(item[1] for item in js_runner_evidence)
                    execution_evidence = "Independent runner evidence: " + "; ".join(
                        item[2] for item in js_runner_evidence
                    )
                elif language in {"c", "cpp"}:
                    tests_passed, execution_evidence = _ctest_language_evidence(
                        language, combined_output, unit_files, unit.root
                    )
                else:
                    tests_passed, execution_evidence = _execution_evidence(
                        language,
                        combined_output,
                        unit.root,
                        node_test_files,
                    )
                if tests_passed is None:
                    passed = False
                    failure_reason = (
                        "Commands exited successfully, but Polyguard could not verify that any tests "
                        "passed. Use a built-in runner output format or a native structured report. "
                        "Custom success_patterns are not accepted because output regexes cannot prove test execution."
                    )
        check_result = CheckResult(
            language=language,
            status="PASS" if passed else "FAIL",
            commands=commands,
            exit_code=last_code,
            elapsed_seconds=round(elapsed, 3),
            reason=failure_reason,
            output=combined_output,
            tests_passed=tests_passed,
            tests_failed=tests_failed,
            tests_skipped=tests_skipped,
            tests_total=tests_total,
            execution_evidence=execution_evidence,
            project_root=project_root,
        )
        results.append(check_result)

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
        if item.get("project_root"):
            lines.append(f"  Project root: {item['project_root']}")
        if item.get("tests_passed") is not None:
            lines.append(f"  Tests passed: {item['tests_passed']}")
        if item.get("tests_total") is not None:
            lines.append(
                f"  Tests total: {item['tests_total']}; failed: {item.get('tests_failed', 0)}; "
                f"skipped: {item.get('tests_skipped', 0)}"
            )
        if item.get("execution_evidence"):
            lines.append(f"  Evidence: {item['execution_evidence']}")
        if item["reason"]:
            lines.append(f"  {item['reason']}")
        if item["output"]:
            lines.append(item["output"])
    return "\n".join(lines)
