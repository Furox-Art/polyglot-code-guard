"""Real compiler/test-runner integration checks.

Each test builds a disposable project and executes Polyguard against it.
Missing local toolchains are skipped; CI installs each toolchain explicitly.
"""

from __future__ import annotations

import json
import shutil
import subprocess
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
        "java": ["java", "javac", "mvn"],
        "csharp": ["dotnet"],
        "php": ["php", "composer"],
        "swift": ["swift"],
    }
    return all(shutil.which(tool) for tool in requirements[language])


def _make_project(root: Path, language: str, failing: bool = False) -> None:
    if language == "python":
        _write(root, "app.py", "def add(a, b): return a + b\n")
        assertion = "assert add(2, 3) == 99, 'POLYGUARD_EXPECTED_ASSERTION_FAILURE'" if failing else "assert add(2, 3) == 5"
        _write(root, "tests/test_app.py", f"from app import add\ndef test_add(): {assertion}\n")
    elif language == "rust":
        _write(root, "Cargo.toml", '[package]\nname = "polyguard_fixture"\nversion = "0.1.0"\nedition = "2021"\n')
        assertion = 'assert_eq!(2 + 3, 99, "POLYGUARD_EXPECTED_ASSERTION_FAILURE");' if failing else "assert_eq!(2 + 3, 5);"
        _write(root, "src/lib.rs", f"#[cfg(test)] mod tests {{ #[test] fn addition_works() {{ {assertion} }} }}\n")
    elif language == "javascript":
        _write(root, "package.json", '{"name":"polyguard-fixture","version":"1.0.0","scripts":{"test":"node --test"}}\n')
        assertion = 'assert.equal(2 + 3, 99, "POLYGUARD_EXPECTED_ASSERTION_FAILURE");' if failing else "assert.equal(2 + 3, 5);"
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
        failure_message = "POLYGUARD_EXPECTED_ASSERTION_FAILURE" if failing else "unexpected sum"
        _write(
            root, "main_test.go",
            'package main\nimport "testing"\n'
            f'func TestAdd(t *testing.T) {{ if Add(2, 3) != {assertion} {{ t.Fatal("{failure_message}") }} }}\n',
        )
    elif language == "c":
        _write(root, "CMakeLists.txt",
            "cmake_minimum_required(VERSION 3.16)\n"
            "project(PolyguardCFixture C)\n"
            "enable_testing()\n"
            "add_executable(test_math tests/test_math.c)\n"
            "add_test(NAME c_math COMMAND test_math)\n",
        )
        expected = 99 if failing else 5
        failure_signal = 'fprintf(stderr, "POLYGUARD_EXPECTED_ASSERTION_FAILURE\\n"); ' if failing else ""
        assertion = f"{failure_signal}assert(2 + 3 == {expected});"
        _write(root, "tests/test_math.c", f"#include <assert.h>\n#include <stdio.h>\nint main(void) {{ {assertion} return 0; }}\n")
    elif language == "cpp":
        _write(root, "CMakeLists.txt",
            "cmake_minimum_required(VERSION 3.16)\n"
            "project(PolyguardCppFixture CXX)\n"
            "enable_testing()\n"
            "add_executable(test_math tests/test_math.cpp)\n"
            "add_test(NAME cpp_math COMMAND test_math)\n",
        )
        expected = 99 if failing else 5
        failure_signal = 'std::cerr << "POLYGUARD_EXPECTED_ASSERTION_FAILURE" << std::endl; ' if failing else ""
        assertion = f"{failure_signal}assert(2 + 3 == {expected});"
        _write(root, "tests/test_math.cpp", f"#include <cassert>\n#include <iostream>\nint main() {{ {assertion} return 0; }}\n")
    elif language == "ruby":
        _write(root, "Gemfile", 'source "https://rubygems.org"\n')
        assertion = 'assert_equal 99, 2 + 3, "POLYGUARD_EXPECTED_ASSERTION_FAILURE"' if failing else "assert_equal 5, 2 + 3"
        _write(
            root, "test/test_math_test.rb",
            "require 'minitest/autorun'\n"
            "class MathTest < Minitest::Test\n"
            f"  def test_addition\n    {assertion}\n  end\nend\n",
        )
    elif language == "java":
        _write(
            root, "pom.xml",
            """<project xmlns="http://maven.apache.org/POM/4.0.0"
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
  xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 https://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <groupId>org.polyguard</groupId><artifactId>fixture</artifactId><version>1.0-SNAPSHOT</version>
  <properties><maven.compiler.source>17</maven.compiler.source><maven.compiler.target>17</maven.compiler.target><project.build.sourceEncoding>UTF-8</project.build.sourceEncoding></properties>
  <dependencies><dependency><groupId>org.junit.jupiter</groupId><artifactId>junit-jupiter</artifactId><version>5.11.4</version><scope>test</scope></dependency></dependencies>
  <build><plugins><plugin><groupId>org.apache.maven.plugins</groupId><artifactId>maven-surefire-plugin</artifactId><version>3.5.2</version></plugin></plugins></build>
</project>
""",
        )
        _write(root, "src/main/java/org/polyguard/Math.java",
               "package org.polyguard; public final class Math { public static int add(int a, int b) { return a + b; } }\n")
        expected = 99 if failing else 5
        _write(
            root, "src/test/java/org/polyguard/MathTest.java",
            "package org.polyguard; import org.junit.jupiter.api.Test; import static org.junit.jupiter.api.Assertions.assertEquals; "
            f'public class MathTest {{ @Test void additionWorks() {{ assertEquals({expected}, Math.add(2, 3), "POLYGUARD_EXPECTED_ASSERTION_FAILURE"); }} }}\n',
        )
    elif language == "csharp":
        _write(
            root, "PolyguardFixture.Tests.csproj",
            """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup><TargetFramework>net8.0</TargetFramework><IsPackable>false</IsPackable><IsTestProject>true</IsTestProject></PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Microsoft.NET.Test.Sdk" Version="17.12.0" />
    <PackageReference Include="xunit" Version="2.9.2" />
    <PackageReference Include="xunit.runner.visualstudio" Version="2.8.2"><IncludeAssets>runtime; build; native; contentfiles; analyzers; buildtransitive</IncludeAssets><PrivateAssets>all</PrivateAssets></PackageReference>
  </ItemGroup>
</Project>
""",
        )
        expected = 99 if failing else 5
        _write(root, "MathTests.cs",
               f'using Xunit; public class MathTests {{ [Fact] public void AdditionWorks() {{ Assert.True(2 + 3 == {expected}, "POLYGUARD_EXPECTED_ASSERTION_FAILURE"); }} }}\n')
    elif language == "php":
        _write(root, "composer.json",
               '{"name":"polyguard/fixture","require-dev":{"phpunit/phpunit":"^12.0"}}\n')
        _write(root, "src/Math.php",
               "<?php namespace Polyguard; final class Math { public static function add(int $a, int $b): int { return $a + $b; } }\n")
        expected = 99 if failing else 5
        _write(
            root, "tests/MathTest.php",
            "<?php require_once __DIR__ . '/../src/Math.php'; "
            f"final class MathTest extends \\PHPUnit\\Framework\\TestCase {{ public function testAddition(): void {{ "
            f"self::assertSame({expected}, \\Polyguard\\Math::add(2, 3), 'POLYGUARD_EXPECTED_ASSERTION_FAILURE'); }} }}\n",
        )
    elif language == "swift":
        _write(
            root, "Package.swift",
            """// swift-tools-version: 5.9
import PackageDescription
let package = Package(
    name: "PolyguardFixture",
    products: [.library(name: "PolyguardFixture", targets: ["PolyguardFixture"])],
    targets: [
        .target(name: "PolyguardFixture"),
        .testTarget(name: "PolyguardFixtureTests", dependencies: ["PolyguardFixture"])
    ]
)
""",
        )
        _write(root, "Sources/PolyguardFixture/Math.swift",
               "public enum Math { public static func add(_ a: Int, _ b: Int) -> Int { a + b } }\n")
        expected = 99 if failing else 5
        _write(
            root, "Tests/PolyguardFixtureTests/MathTests.swift",
            f"""import Testing
@testable import PolyguardFixture

@Test func additionWorks() {{
    #expect(Math.add(2, 3) == {expected}, "POLYGUARD_EXPECTED_ASSERTION_FAILURE")
}}
""",
        )
    else:
        raise ValueError(f"Unsupported integration fixture: {language}")


LANGUAGES = ["python", "rust", "javascript", "go", "c", "cpp", "ruby", "java", "csharp", "php", "swift"]


def _prepare_external_fixture_dependencies(root: Path, language: str) -> None:
    if language == "php":
        completed = subprocess.run(
            ["composer", "install", "--no-interaction", "--prefer-dist", "--no-progress"],
            cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=240, check=False,
        )
        if completed.returncode:
            pytest.fail(f"Composer fixture setup failed:\n{completed.stdout}")


@pytest.mark.parametrize("language", LANGUAGES)
def test_real_toolchain_fixture_passes(tmp_path: Path, language: str) -> None:
    if not _toolchain_available(language):
        pytest.skip(f"Required local toolchain for {language} is not installed.")
    _make_project(tmp_path, language)
    _prepare_external_fixture_dependencies(tmp_path, language)
    report = run_checks(tmp_path, timeout=240)
    assert report["status"] == "PASS", json.dumps(report, indent=2)
    assert len(report["results"]) == 1
    assert report["results"][0]["tests_passed"] and report["results"][0]["tests_passed"] > 0
    assert report["results"][0]["execution_evidence"]


@pytest.mark.parametrize("language", LANGUAGES)
def test_real_toolchain_fixture_fails_on_assertion(tmp_path: Path, language: str) -> None:
    if not _toolchain_available(language):
        pytest.skip(f"Required local toolchain for {language} is not installed.")
    _make_project(tmp_path, language, failing=True)
    _prepare_external_fixture_dependencies(tmp_path, language)
    report = run_checks(tmp_path, timeout=240)
    assert report["status"] == "FAIL", json.dumps(report, indent=2)
    result = report["results"][0]
    assert result["status"] == "FAIL", json.dumps(report, indent=2)
    assert result["exit_code"] is not None and result["exit_code"] != 0, json.dumps(report, indent=2)
    assert "POLYGUARD_EXPECTED_ASSERTION_FAILURE" in result["output"], json.dumps(report, indent=2)


def test_repository_fixture_directory_is_not_misdetected(tmp_path: Path) -> None:
    from polyguard.runner import _project_files

    fixture_dir = tmp_path / ".polyguard-fixtures" / "rust"
    fixture_dir.mkdir(parents=True)
    (fixture_dir / "Cargo.toml").write_text("[package]", encoding="utf-8")
    assert _project_files(tmp_path) == []


def test_real_python_go_rust_monorepo_fixtures(tmp_path: Path) -> None:
    languages = ["python", "go", "rust"]
    missing = [language for language in languages if not _toolchain_available(language)]
    if missing:
        pytest.skip(f"Required local toolchains are not installed: {', '.join(missing)}")

    for language, directory in zip(languages, ["apps/api", "services/worker", "libs/core"]):
        package_root = tmp_path / directory
        if language == "python":
            _write(package_root, "pyproject.toml", "[project]\nname = 'api-fixture'\n")
        _make_project(package_root, language)

    report = run_checks(tmp_path, timeout=240)
    assert report["status"] == "PASS", report
    assert {(item["language"], Path(item["project_root"]).relative_to(tmp_path).as_posix())
            for item in report["results"]} == {
        ("python", "apps/api"),
        ("go", "services/worker"),
        ("rust", "libs/core"),
    }


def test_real_python_rust_monorepo_fixtures(tmp_path: Path) -> None:
    languages = ["python", "rust"]
    missing = [language for language in languages if not _toolchain_available(language)]
    if missing:
        pytest.skip(f"Required local toolchains are not installed: {', '.join(missing)}")

    for language, directory in zip(languages, ["apps/api", "libs/core"]):
        package_root = tmp_path / directory
        package_root.mkdir(parents=True)
        if language == "python":
            _write(package_root, "pyproject.toml", "[project]\nname = 'api-fixture'\n")
        _make_project(package_root, language)

    report = run_checks(tmp_path, timeout=240)
    assert report["status"] == "PASS", report
    assert {(item["language"], Path(item["project_root"]).relative_to(tmp_path).as_posix())
            for item in report["results"]} == {
        ("python", "apps/api"),
        ("rust", "libs/core"),
    }


@pytest.mark.parametrize("language", ["java", "csharp", "php", "swift"])
def test_extended_language_fixture_detection(tmp_path: Path, language: str) -> None:
    from polyguard.runner import _has_test, _project_files, discover

    _make_project(tmp_path, language)
    files = _project_files(tmp_path)
    assert _has_test(files, language, tmp_path)
    plans = discover(tmp_path)
    assert len(plans) == 1
    assert plans[0][0] == language
    assert plans[0][1]
