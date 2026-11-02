"""Real compiler/test-runner integration checks.

Each test builds a small disposable project and executes Polyguard against it.
Missing local toolchains are skipped here; the CI job installs its required
toolchains and exercises this fixture matrix.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from polyguard.runner import run_checks


def _write(root: Path, relative: str, content: str) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _toolchain_available(language: str) -> bool:
    requirements = {
        "python": ["python3"],
        "rust": ["cargo", "rustc"],
        "javascript": ["node", "npm"],
        "go": ["go"],
        "c": ["cmake", "cc"],
        "cpp": ["cmake", "c++"],
        "ruby": ["ruby"],
    }
    return all(shutil.which(tool) for tool in requirements[language])


def _make_project(root: Path, language: str, failing: bool = False) -> None:
    if language == "python":
        _write(root, "app.py", "def add(a, b): return a + b\n")
        assertion = "assert add(2, 3) == 99" if failing else "assert add(2, 3) == 5"
        _write(root, "tests/test_app.py", f"from app import add\ndef test_add(): {assertion}\n")
    elif language == "rust":
        _write(root, "Cargo.toml", '[package]\nname = "polyguard_fixture"\nversion = "0.1.0"\nedition = "2021"\n')
        assertion = "assert_eq!(2 + 3, 99);" if failing else "assert_eq!(2 + 3, 5);"
        _write(root, "src/lib.rs", f"#[cfg(test)] mod tests {{ #[test] fn addition_works() {{ {assertion} }} }}\n")
    elif language == "javascript":
        _write(root, "package.json", '{"name":"polyguard-fixture","version":"1.0.0","scripts":{"test":"node --test"}}\n')
        assertion = "assert.equal(2 + 3, 99);" if failing else "assert.equal(2 + 3, 5);"
        _write(
            root, "test/math.test.js",
            "const test = require('node:test');\n"
            "const assert = require('node:assert/strict');\n"
            f"test('addition works', () => {{ {assertion} }});\n",
        )
    elif language == "go":
        _write(root, "go.mod", "module example.com/polyguard-fixture\ngo 1.22\n")
        _write(root, "main.go", "package main\nfunc Add(a, b int) int { return a + b }\n")
        assertion = "99" if failing else "5"
        _write(
            root, "main_test.go",
            "package main\nimport \"testing\"\n"
            f"func TestAdd(t *testing.T) {{ if Add(2, 3) != {assertion} {{ t.Fatal(\"unexpected sum\") }} }}\n",
        )
    elif language == "c":
        _write(root, "CMakeLists.txt",
            "cmake_minimum_required(VERSION 3.16)\n"
            "project(PolyguardCFixture C)\n"
            "enable_testing()\n"
            "add_executable(test_math tests/test_math.c)\n"
            "add_test(NAME c_math COMMAND test_math)\n",
        )
        assertion = "assert(2 + 3 == 99);" if failing else "assert(2 + 3 == 5);"
        _write(root, "tests/test_math.c", f"#include <assert.h>\nint main(void) {{ {assertion} return 0; }}\n")
    elif language == "cpp":
        _write(root, "CMakeLists.txt",
            "cmake_minimum_required(VERSION 3.16)\n"
            "project(PolyguardCppFixture CXX)\n"
            "enable_testing()\n"
            "add_executable(test_math tests/test_math.cpp)\n"
            "add_test(NAME cpp_math COMMAND test_math)\n",
        )
        assertion = "assert(2 + 3 == 99);" if failing else "assert(2 + 3 == 5);"
        _write(root, "tests/test_math.cpp", f"#include <cassert>\nint main() {{ {assertion} return 0; }}\n")
    elif language == "ruby":
        _write(root, "Gemfile", 'source "https://rubygems.org"\n')
        assertion = "assert_equal 99, 2 + 3" if failing else "assert_equal 5, 2 + 3"
        _write(
            root, "test/test_math_test.rb",
            "require 'minitest/autorun'\n"
            "class MathTest < Minitest::Test\n"
            f"  def test_addition\n    {assertion}\n  end\nend\n",
        )
    else:
        raise ValueError(f"Unsupported integration fixture: {language}")


LANGUAGES = ["python", "rust", "javascript", "go", "c", "cpp", "ruby"]


@pytest.mark.parametrize("language", LANGUAGES)
def test_real_toolchain_fixture_passes(tmp_path: Path, language: str) -> None:
    if not _toolchain_available(language):
        pytest.skip(f"Required local toolchain for {language} is not installed.")
    _make_project(tmp_path, language)
    report = run_checks(tmp_path, timeout=180)
    assert report["status"] == "PASS", json.dumps(report, indent=2)
    assert len(report["results"]) == 1
    assert report["results"][0]["tests_passed"] and report["results"][0]["tests_passed"] > 0
    assert report["results"][0]["execution_evidence"]


@pytest.mark.parametrize("language", LANGUAGES)
def test_real_toolchain_fixture_fails_on_assertion(tmp_path: Path, language: str) -> None:
    if not _toolchain_available(language):
        pytest.skip(f"Required local toolchain for {language} is not installed.")
    _make_project(tmp_path, language, failing=True)
    report = run_checks(tmp_path, timeout=180)
    assert report["status"] == "FAIL", json.dumps(report, indent=2)
    assert report["results"][0]["status"] == "FAIL", json.dumps(report, indent=2)


def test_repository_fixture_directory_is_not_misdetected(tmp_path: Path) -> None:
    from polyguard.runner import _project_files

    fixture_dir = tmp_path / ".polyguard-fixtures" / "rust"
    fixture_dir.mkdir(parents=True)
    (fixture_dir / "Cargo.toml").write_text("[package]", encoding="utf-8")
    assert _project_files(tmp_path) == []
