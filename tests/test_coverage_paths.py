from __future__ import annotations

import builtins
import json
import os
import runpy
import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

import polyguard.runner as runner


@pytest.mark.parametrize("language,filename,content", [
    ("python", "test_empty.py", "value = 1"),
    ("rust", "lib.rs", "fn ordinary() {}"),
    ("javascript", "thing.test.js", "const value = 1;"),
    ("go", "main_test.go", "package main"),
    ("java", "ThingTest.java", "class ThingTest {}"),
    ("csharp", "ThingTests.cs", "public class ThingTests {}"),
    ("php", "testThing.php", "<?php echo 'x';"),
    ("ruby", "thing_spec.rb", "value = 1"),
    ("swift", "Tests/Thing.swift", "let value = 1"),
    ("c", "tests/test_thing.c", "int main(void) { return 0; }"),
    ("cpp", "tests/test_thing.cpp", "int main() { return 0; }"),
])
def test_all_language_test_detectors_reject_non_tests(tmp_path, language, filename, content):
    path = tmp_path / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    assert not runner._has_test([path], language, tmp_path)


def test_in_test_tree_handles_paths_outside_root_and_unreadable_files(tmp_path):
    outside = tmp_path.parent / "outside_test.py"
    assert not runner._in_test_tree(tmp_path, outside)
    missing = tmp_path / "missing.py"
    assert runner._safe_read(missing) == ""


def test_project_files_ignores_symlink_loops_and_ignored_directories(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("x = 1", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "ignored.js").write_text("x", encoding="utf-8")
    loop = tmp_path / "loop.py"
    try:
        loop.symlink_to(loop)
    except OSError:
        pytest.skip("Symlinks are unavailable on this filesystem.")
    found = runner._project_files(tmp_path)
    assert tmp_path / "src" / "main.py" in found
    assert tmp_path / "node_modules" / "ignored.js" not in found
    assert loop not in found


def test_python_test_paths_filter_test_files_and_non_test_files(tmp_path):
    source = tmp_path / "app.py"
    source.write_text("x = 1", encoding="utf-8")
    valid = tmp_path / "tests" / "test_app.py"
    valid.parent.mkdir()
    valid.write_text("def test_ok(): pass", encoding="utf-8")
    empty = tmp_path / "tests" / "test_empty.py"
    empty.write_text("# no tests", encoding="utf-8")
    assert runner._python_test_paths(tmp_path, [source, valid, empty]) == ["tests/test_app.py"]


def test_default_plans_handles_bad_cargo_manifest_and_non_workspace(tmp_path):
    (tmp_path / "Cargo.toml").write_text("[", encoding="utf-8")
    (tmp_path / "lib.rs").write_text("pub fn f() {}", encoding="utf-8")
    assert runner._default_plans(tmp_path)["rust"] == [["cargo", "test", "--all-targets"]]
    (tmp_path / "Cargo.toml").write_text("[package]\nname='demo'\n", encoding="utf-8")
    assert runner._default_plans(tmp_path)["rust"] == [["cargo", "test", "--all-targets"]]


def test_default_plans_handles_workspace_manifest(tmp_path):
    (tmp_path / "Cargo.toml").write_text("[workspace]\nmembers=[]\n", encoding="utf-8")
    (tmp_path / "src.rs").write_text("", encoding="utf-8")
    assert runner._default_plans(tmp_path)["rust"] == [["cargo", "test", "--workspace", "--all-targets"]]


def test_default_plans_skips_invalid_javascript_packages_and_uses_node_test(tmp_path):
    (tmp_path / "package.json").write_text("{broken", encoding="utf-8")
    (tmp_path / "app.js").write_text("module.exports = 1", encoding="utf-8")
    assert runner._default_plans(tmp_path)["javascript"] == [["node", "--test"]]
    (tmp_path / "package.json").write_text("[]", encoding="utf-8")
    assert runner._default_plans(tmp_path)["javascript"] == [["node", "--test"]]


def test_default_plans_recognizes_javascript_runners_and_typescript(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({
        "devDependencies": {"vitest": "*", "jest": "*", "mocha": "*", "tap": "*"},
        "dependencies": [],
    }), encoding="utf-8")
    (tmp_path / "app.ts").write_text("export const n: number = 1", encoding="utf-8")
    (tmp_path / "tsconfig.json").write_text("{}", encoding="utf-8")
    plans = runner._default_plans(tmp_path)
    assert plans["javascript"] == [
        ["npx", "--no-install", "vitest", "run"],
        ["npx", "--no-install", "jest", "--runInBand"],
        ["npx", "--no-install", "mocha"],
        ["npx", "--no-install", "tap"],
        ["npx", "--no-install", "tsc", "--noEmit"],
    ]


def test_default_plans_php_vendor_and_fallback(tmp_path):
    (tmp_path / "composer.json").write_text("{}", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "vendor" / "bin").mkdir(parents=True)
    phpunit = tmp_path / "vendor" / "bin" / "phpunit"
    phpunit.write_text("#!/bin/sh\n", encoding="utf-8")
    plan = runner._default_plans(tmp_path)["php"][0]
    assert plan[:2] == ["php", str(phpunit)]
    assert plan[-1] == "tests"
    phpunit.unlink()
    assert runner._default_plans(tmp_path)["php"][0][0] == "phpunit"


def test_default_plans_ruby_rspec_and_minitest(tmp_path):
    (tmp_path / "Gemfile").write_text("", encoding="utf-8")
    (tmp_path / "spec").mkdir()
    (tmp_path / "spec" / "thing_spec.rb").write_text("describe 'thing' do end", encoding="utf-8")
    assert runner._default_plans(tmp_path)["ruby"] == [["bundle", "exec", "rspec"]]
    (tmp_path / "spec" / "thing_spec.rb").unlink()
    assert runner._default_plans(tmp_path)["ruby"][0][:2] == ["ruby", "-Itest"]


def test_maven_module_roots_rejects_invalid_xml_and_external_or_missing_modules(tmp_path):
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "pom.xml").write_text("<project>", encoding="utf-8")
    external = tmp_path.parent / "external-module"
    external.mkdir(exist_ok=True)
    (tmp_path / "pom.xml").write_text(
        f"<project><modules><module>{external}</module><module>absent</module></modules></project>",
        encoding="utf-8",
    )
    assert runner._maven_module_roots(tmp_path, [bad / "pom.xml", tmp_path / "pom.xml"]) == set()


def test_maven_module_roots_ignores_empty_and_non_module_nodes(tmp_path):
    child = tmp_path / "child"
    child.mkdir()
    pom = tmp_path / "pom.xml"
    pom.write_text("<project><name>parent</name><module>child</module><module> </module></project>", encoding="utf-8")
    assert runner._maven_module_roots(tmp_path, [pom]) == {child}


def test_cargo_workspace_roots_reject_invalid_manifests_members_and_escape_paths(tmp_path):
    invalid = tmp_path / "bad"
    invalid.mkdir()
    (invalid / "Cargo.toml").write_text("[workspace\n", encoding="utf-8")
    root_manifest = tmp_path / "Cargo.toml"
    root_manifest.write_text("[workspace]\nmembers='../escape'\n", encoding="utf-8")
    assert runner._cargo_workspace_member_roots(tmp_path, [invalid / "Cargo.toml", root_manifest]) == set()
    root_manifest.write_text("[workspace]\nmembers=4\n", encoding="utf-8")
    assert runner._cargo_workspace_member_roots(tmp_path, [root_manifest]) == set()


def test_cargo_workspace_roots_rejects_non_string_member_patterns(tmp_path):
    (tmp_path / "Cargo.toml").write_text("[workspace]\nmembers=[4, false]\n", encoding="utf-8")
    assert runner._cargo_workspace_member_roots(tmp_path, [tmp_path / "Cargo.toml"]) == set()


def test_npm_workspace_roots_reject_bad_json_shapes_and_escape_paths(tmp_path):
    malformed = tmp_path / "bad"
    malformed.mkdir()
    (malformed / "package.json").write_text("{", encoding="utf-8")
    root_package = tmp_path / "package.json"
    root_package.write_text(json.dumps({"workspaces": {"packages": "../escape"}}), encoding="utf-8")
    assert runner._npm_workspace_roots(tmp_path, [malformed / "package.json", root_package]) == set()
    root_package.write_text(json.dumps({"workspaces": [3, None]}), encoding="utf-8")
    assert runner._npm_workspace_roots(tmp_path, [root_package]) == set()


def test_gradle_and_dotnet_roots_ignore_missing_members(tmp_path):
    settings = tmp_path / "settings.gradle"
    settings.write_text("include(':missing')\n", encoding="utf-8")
    solution = tmp_path / "demo.sln"
    solution.write_text(
        'Project("{GUID}") = "Demo", "missing/Demo.csproj", "{PROJECT}"\n',
        encoding="utf-8",
    )
    assert runner._gradle_member_roots(tmp_path, [settings]) == set()
    assert runner._dotnet_solution_member_roots(tmp_path, [solution]) == set()


def test_language_alias_normalization_and_path_helpers():
    assert runner.normalize_language("  Py  ") == "python"
    assert runner.normalize_language(" CXX ") == "cpp"
    assert runner.normalize_language(" unknown ") == "unknown"
    assert runner._is_within(Path("/tmp/a/b"), Path("/tmp/a"))
    assert not runner._is_within(Path("/tmp/ab"), Path("/tmp/a"))
    assert runner._xml_local_name("{urn:test}TestCase") == "testcase"


def test_load_config_defaults_aliases_and_relative_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    required, configured = runner._load_config(tmp_path, None)
    assert required == set()
    assert configured == {}
    config = tmp_path / "custom.toml"
    config.write_text(
        "[project]\nlanguages=['py','js']\n"
        "[checks.cc]\ncommands=[['ctest']]\ntest_patterns=['tests/**']\n",
        encoding="utf-8",
    )
    required, configured = runner._load_config(tmp_path, "custom.toml")
    assert required == {"python", "javascript", "cpp"}
    assert configured["cpp"].commands == [["ctest"]]


@pytest.mark.parametrize("config_text", [
    "[unknown]\nvalue=1\n",
    "[project]\nother=1\n",
    "[project]\nlanguages=1\n",
    "checks=1\n",
    "[project]\nlanguages=['not-a-language']\n",
    "[checks.scala]\ncommands=[['scala']]\ntest_patterns=['tests/**']\n",
    "[checks.python]\nother=1\n",
    "[checks.python]\ncommands=[]\ntest_patterns=['tests/**']\n",
    "[checks.python]\ncommands=[[]]\ntest_patterns=['tests/**']\n",
    "[checks.python]\ncommands=[['python','']]\ntest_patterns=['tests/**']\n",
    "[checks.python]\ncommands=[['python']]\ntest_patterns=[]\n",
    "[checks.python]\ncommands=[['python']]\ntest_patterns=['']\n",
    "[checks.python]\ncommands=[['python']]\ntest_patterns=[1]\n",
    "[checks.python]\ncommands=[['python']]\ntest_patterns=['../outside/**']\n",
    "[checks.python]\nsuccess_patterns=['passed']\n",
])
def test_load_config_rejects_invalid_shapes(tmp_path, config_text):
    config = tmp_path / "config.toml"
    config.write_text(config_text, encoding="utf-8")
    with pytest.raises(ValueError):
        runner._load_config(tmp_path, config)


def test_matching_config_tests_checks_files_membership_and_glob_errors(tmp_path, monkeypatch):
    directory = tmp_path / "tests"
    directory.mkdir()
    test_file = directory / "test_marker.txt"
    test_file.write_text("marker", encoding="utf-8")
    assert not runner._matching_config_tests(tmp_path, ["tests"])
    assert runner._matching_config_tests(tmp_path, ["tests/*.txt"])
    assert not runner._matching_config_tests(tmp_path, ["tests/*.txt"], files=[])
    assert runner._matching_config_tests(tmp_path, ["tests/*.txt"], files=[test_file])
    monkeypatch.setattr(Path, "glob", lambda self, pattern: (_ for _ in ()).throw(ValueError("bad glob")))
    assert not runner._matching_config_tests(tmp_path, ["["])


@pytest.mark.parametrize("output", [
    "not json",
    "null",
    json.dumps({"Action": "bad"}),
    json.dumps({"Action": "pass", "Test": "T", "Elapsed": "1"}),
])
def test_go_json_invalid_or_empty_output_fails_closed(output):
    assert runner._execution_evidence("go", output)[0] is None


def test_non_go_evidence_unknown_and_zero_patterns_fail_closed():
    assert runner._execution_evidence("unknown-language", "1 passed")[0] is None
    assert runner._execution_evidence("python", "0 passed")[0] is None
    assert runner._execution_evidence("python", "2 passed")[0] == 2


def _write_junit(tmp_path, xml):
    path = tmp_path / "report.xml"
    path.write_text(xml, encoding="utf-8")
    return path


def test_junit_parser_accepts_namespaced_suite_with_all_outcomes(tmp_path):
    path = _write_junit(tmp_path, """
    <testsuite xmlns="urn:junit" tests="3" failures="1" errors="1" skipped="1">
      <testcase name="fail"><failure/></testcase>
      <testcase name="error"><error/></testcase>
      <testcase name="skip"><skipped/></testcase>
    </testsuite>
    """)
    assert runner._parse_junit_xml(path) == (3, 2, 1)


def test_junit_parser_accepts_testsuites_collection(tmp_path):
    path = _write_junit(tmp_path, """
    <testsuites>
      <testsuite tests="1" failures="0" errors="0" skipped="0"><testcase name="ok"/></testsuite>
      <testsuite tests="1" failures="0" errors="0" skipped="1"><testcase name="skip"><skipped/></testcase></testsuite>
    </testsuites>
    """)
    assert runner._parse_junit_xml(path) == (2, 0, 1)


@pytest.mark.parametrize("xml", [
    "<not-junit/>",
    "<testsuites/>",
    "<testsuite tests='x' failures='0' errors='0' skipped='0'/>",
    "<testsuite failures='0' errors='0' skipped='0'/>",
    "<testsuite tests='1' failures='0' errors='0' skipped='0'/>",
    "<testsuite tests='1' failures='1' errors='0' skipped='0'><testcase/></testsuite>",
    "<testsuite tests='1' failures='0' errors='1' skipped='0'><testcase/></testsuite>",
    "<testsuite tests='1' failures='0' errors='0' skipped='1'><testcase/></testsuite>",
])
def test_junit_parser_rejects_invalid_structure_and_counter_mismatches(tmp_path, xml):
    with pytest.raises(ValueError):
        runner._parse_junit_xml(_write_junit(tmp_path, xml))


def test_as_nonnegative_int_handles_negative_and_unparseable_values():
    assert runner._as_nonnegative_int("4") == 4
    assert runner._as_nonnegative_int("-3") == 0
    assert runner._as_nonnegative_int("not-a-number") == 0
    assert runner._as_nonnegative_int(None) == 0


def _trx_xml(outcomes, counters=None, results_tag="Results"):
    counts = counters or {
        "total": str(len(outcomes)),
        "passed": str(sum(x == "Passed" for x in outcomes)),
        "failed": str(sum(x in {"Failed", "Error", "Aborted", "Timeout", "PassedButRunAborted", "Disconnected"} for x in outcomes)),
        "notExecuted": str(sum(x in {"NotExecuted", "Inconclusive", "NotRunnable"} for x in outcomes)),
    }
    attrs = " ".join(f'{key}="{value}"' for key, value in counts.items())
    results = "".join(f'<UnitTestResult outcome="{x}"/>' for x in outcomes)
    return f'<TestRun><ResultSummary><Counters {attrs}/></ResultSummary><{results_tag}>{results}</{results_tag}></TestRun>'


def test_trx_parser_accepts_all_supported_outcome_categories(tmp_path):
    outcomes = [
        "Passed", "Failed", "Error", "Aborted", "Timeout", "PassedButRunAborted",
        "Disconnected", "NotExecuted", "Inconclusive", "NotRunnable",
    ]
    path = _write_junit(tmp_path, _trx_xml(outcomes))
    assert runner._parse_trx(path) == (10, 6, 3)


@pytest.mark.parametrize("xml", [
    "<not-test-run/>",
    "<TestRun/>",
    "<TestRun><ResultSummary/><ResultSummary/></TestRun>",
    "<TestRun><ResultSummary><Counters total='1' passed='1' failed='0'/><Counters total='1' passed='1' failed='0'/></ResultSummary></TestRun>",
    "<TestRun><ResultSummary><Counters total='x' passed='1' failed='0'/></ResultSummary></TestRun>",
    "<TestRun><ResultSummary><Counters total='1' passed='1' failed='0' custom='-1'/></ResultSummary></TestRun>",
    "<TestRun><ResultSummary><Counters total='0' passed='0' failed='0'/></ResultSummary><Results/></TestRun>",
    "<TestRun><ResultSummary><Counters total='2' passed='1' failed='0'/></ResultSummary><Results><UnitTestResult outcome='Passed'/></Results></TestRun>",
    "<TestRun><ResultSummary><Counters total='1' passed='1' failed='0'/></ResultSummary><OtherResults/></TestRun>",
    "<TestRun><ResultSummary><Counters total='1' passed='1' failed='0'/></ResultSummary><Results><Unknown/></Results></TestRun>",
    "<TestRun><ResultSummary><Counters total='1' passed='1' failed='0'/></ResultSummary><Results><UnitTestResult outcome='Mystery'/></Results></TestRun>",
])
def test_trx_parser_rejects_malformed_reports(tmp_path, xml):
    with pytest.raises(ValueError):
        runner._parse_trx(_write_junit(tmp_path, xml))


def test_trx_parser_rejects_counter_record_disagreement(tmp_path):
    xml = _trx_xml(["Passed"], counters={"total": "1", "passed": "0", "failed": "1"})
    with pytest.raises(ValueError, match="records do not match"):
        runner._parse_trx(_write_junit(tmp_path, xml))


def test_prepare_result_area_cleans_only_owned_reports_and_ignores_other_languages(tmp_path):
    runner._prepare_result_area(tmp_path, "python")
    assert not (tmp_path / ".polyguard-results").exists()
    runner._prepare_result_area(tmp_path, "php")
    results = tmp_path / ".polyguard-results"
    stale = results / "phpunit.xml"
    unrelated = results / "keep.xml"
    stale.write_text("<old/>", encoding="utf-8")
    unrelated.write_text("<keep/>", encoding="utf-8")
    runner._prepare_result_area(tmp_path, "php")
    assert not stale.exists()
    assert unrelated.exists()
    runner._prepare_result_area(tmp_path, "csharp")


def test_prepare_result_area_rejects_non_directory_and_symlink(tmp_path):
    file_path = tmp_path / ".polyguard-results"
    file_path.write_text("file", encoding="utf-8")
    with pytest.raises(OSError, match="not a directory"):
        runner._prepare_result_area(tmp_path, "php")
    file_path.unlink()
    target = tmp_path / "target"
    target.mkdir()
    try:
        file_path.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks are unavailable on this filesystem.")
    with pytest.raises(OSError, match="symlink"):
        runner._prepare_result_area(tmp_path, "php")


def test_prepare_result_area_refuses_stale_symlink_report(tmp_path):
    runner._prepare_result_area(tmp_path, "csharp")
    results = tmp_path / ".polyguard-results"
    target = tmp_path / "outside.trx"
    target.write_text("keep", encoding="utf-8")
    link = results / "polyguard-old.trx"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("Symlinks are unavailable on this filesystem.")
    with pytest.raises(OSError, match="non-regular"):
        runner._prepare_result_area(tmp_path, "csharp")
    assert target.exists()


def test_native_report_paths_select_language_owned_paths(tmp_path):
    surefire = tmp_path / "target" / "surefire-reports"
    unrelated = tmp_path / "elsewhere"
    surefire.mkdir(parents=True)
    unrelated.mkdir()
    (surefire / "TEST-a.xml").write_text("<testsuite/>", encoding="utf-8")
    (unrelated / "TEST-b.xml").write_text("<testsuite/>", encoding="utf-8")
    (tmp_path / "result.trx").write_text("<TestRun/>", encoding="utf-8")
    assert len(runner._native_report_paths("java", tmp_path)) == 1
    assert runner._native_report_paths("csharp", tmp_path) == []
    assert runner._native_report_paths("php", tmp_path) == [tmp_path / ".polyguard-results" / "phpunit.xml"]
    assert runner._native_report_paths("ruby", tmp_path) == []


def test_snapshot_native_reports_skips_fingerprint_oserrors(tmp_path, monkeypatch):
    report_dir = tmp_path / "target" / "surefire-reports"
    report_dir.mkdir(parents=True)
    (report_dir / "TEST-a.xml").write_text("<testsuite/>", encoding="utf-8")
    monkeypatch.setattr(runner, "_report_fingerprint", lambda path: (_ for _ in ()).throw(OSError("gone")))
    assert runner._snapshot_native_reports("java", tmp_path) == {}


def test_c_and_cpp_test_detection_requires_ctest_registration(tmp_path):
    cmake = tmp_path / "CMakeLists.txt"
    c_test = tmp_path / "tests" / "test_c.c"
    cpp_test = tmp_path / "tests" / "test_cpp.cpp"
    c_test.parent.mkdir()
    c_test.write_text("#include <assert.h>\nint main(){assert(1);}", encoding="utf-8")
    cpp_test.write_text("TEST(Math, Works) {}", encoding="utf-8")
    cmake.write_text("project(demo)\n", encoding="utf-8")
    assert not runner._has_test([cmake, c_test], "c", tmp_path)
    assert not runner._has_test([cmake, cpp_test], "cpp", tmp_path)
    cmake.write_text("enable_testing()\n", encoding="utf-8")
    assert not runner._has_test([cmake, cpp_test], "cpp", tmp_path)


def test_cargo_and_npm_workspace_glob_errors_are_skipped(tmp_path, monkeypatch):
    cargo = tmp_path / "Cargo.toml"
    cargo.write_text("[workspace]\nmembers=['*']\n", encoding="utf-8")
    package = tmp_path / "package.json"
    package.write_text(json.dumps({"workspaces": ["*"]}), encoding="utf-8")
    original_glob = Path.glob

    def raising_glob(self, pattern):
        if self == tmp_path:
            raise ValueError("malformed pattern")
        return original_glob(self, pattern)

    monkeypatch.setattr(Path, "glob", raising_glob)
    assert runner._cargo_workspace_member_roots(tmp_path, [cargo]) == set()
    assert runner._npm_workspace_roots(tmp_path, [package]) == set()


def test_cargo_and_npm_workspaces_reject_symlink_members_outside_root(tmp_path):
    external = tmp_path.parent / (tmp_path.name + "-external")
    external.mkdir()
    (external / "Cargo.toml").write_text("[package]\nname='external'\n", encoding="utf-8")
    (external / "package.json").write_text("{}", encoding="utf-8")
    cargo_member = tmp_path / "cargo-member"
    npm_member = tmp_path / "npm-member"
    try:
        cargo_member.symlink_to(external, target_is_directory=True)
        npm_member.symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks are unavailable on this filesystem.")
    cargo = tmp_path / "Cargo.toml"
    cargo.write_text("[workspace]\nmembers=['cargo-member']\n", encoding="utf-8")
    package = tmp_path / "package.json"
    package.write_text(json.dumps({"workspaces": ["npm-member"]}), encoding="utf-8")
    assert runner._cargo_workspace_member_roots(tmp_path, [cargo]) == set()
    assert runner._npm_workspace_roots(tmp_path, [package]) == set()


def test_execution_evidence_handles_unexpected_numeric_capture(monkeypatch):
    monkeypatch.setitem(runner.DEFAULT_SUCCESS_PATTERNS, "python", [r"([a-z])"])
    assert runner._execution_evidence("python", "x")[0] is None


def test_native_report_evidence_rejects_non_native_language(tmp_path):
    unit = runner.ProjectUnit(tmp_path, "python")
    assert runner._native_report_evidence("python", tmp_path, unit, [unit], {}) == (
        None, None, None, None, "No native report adapter is registered."
    )


def test_native_report_evidence_rejects_stale_symlink_nested_and_outside_reports(tmp_path, monkeypatch):
    report_dir = tmp_path / "target" / "surefire-reports"
    report_dir.mkdir(parents=True)
    valid = report_dir / "TEST-valid.xml"
    valid.write_text('<testsuite tests="1" failures="0" errors="0" skipped="0"><testcase/></testsuite>', encoding="utf-8")
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    external_report = outside / "TEST-external.xml"
    external_report.write_text(valid.read_text(encoding="utf-8"), encoding="utf-8")
    nested_dir = tmp_path / "nested" / "target" / "surefire-reports"
    nested_dir.mkdir(parents=True)
    nested_report = nested_dir / "TEST-nested.xml"
    nested_report.write_text(valid.read_text(encoding="utf-8"), encoding="utf-8")
    symlink = report_dir / "TEST-link.xml"
    try:
        symlink.symlink_to(external_report)
    except OSError:
        pytest.skip("Symlinks are unavailable on this filesystem.")
    unit = runner.ProjectUnit(tmp_path, "java")
    nested_unit = runner.ProjectUnit(tmp_path / "nested", "java")
    monkeypatch.setattr(runner, "_native_report_paths", lambda language, root: [
        valid, external_report, nested_report, symlink,
    ])
    result = runner._native_report_evidence("java", tmp_path, unit, [unit, nested_unit], {})
    assert result[:4] == (1, 0, 0, 1)
    assert "TEST-valid.xml" in result[4]
    assert "TEST-nested.xml" not in result[4]


def test_native_report_evidence_returns_no_fresh_report_and_handles_oserror(tmp_path, monkeypatch):
    report = tmp_path / "TEST-report.xml"
    report.write_text('<testsuite tests="1" failures="0" errors="0" skipped="0"><testcase/></testsuite>', encoding="utf-8")
    unit = runner.ProjectUnit(tmp_path, "java")
    monkeypatch.setattr(runner, "_native_report_paths", lambda language, root: [report])
    unchanged = runner._report_fingerprint(report)
    assert runner._native_report_evidence("java", tmp_path, unit, [unit], {report: unchanged})[0] is None
    monkeypatch.setattr(runner, "_report_fingerprint", lambda path: (_ for _ in ()).throw(OSError("unreadable")))
    assert runner._native_report_evidence("java", tmp_path, unit, [unit], {})[0] is None


def test_native_report_evidence_reports_invalid_xml_and_fingerprint_errors(tmp_path, monkeypatch):
    report = tmp_path / "TEST-report.xml"
    report.write_text("<broken", encoding="utf-8")
    unit = runner.ProjectUnit(tmp_path, "java")
    monkeypatch.setattr(runner, "_native_report_paths", lambda language, root: [report])
    result = runner._native_report_evidence("java", tmp_path, unit, [unit], {})
    assert result[0] is None
    assert "Invalid java test report" in result[4]


def test_native_report_evidence_skips_paths_that_raise_oserror(tmp_path, monkeypatch):
    report = tmp_path / "TEST-report.xml"
    report.write_text("<testsuite/>", encoding="utf-8")
    unit = runner.ProjectUnit(tmp_path, "java")
    monkeypatch.setattr(runner, "_native_report_paths", lambda language, root: [report])
    monkeypatch.setattr(runner, "_is_within", lambda *args: (_ for _ in ()).throw(OSError("gone")))
    result = runner._native_report_evidence("java", tmp_path, unit, [unit], {})
    assert result[0] is None
    assert "No fresh" in result[4]


def test_terminate_process_tree_handles_posix_lookup_and_os_errors(monkeypatch):
    state = {"alive": True, "killed": 0}
    process = SimpleNamespace(pid=123, poll=lambda: None if state["alive"] else 0,
                              kill=lambda: state.__setitem__("killed", state["killed"] + 1))
    monkeypatch.setattr(runner.os, "name", "posix")
    monkeypatch.setattr(runner.os, "killpg", lambda *args: (_ for _ in ()).throw(ProcessLookupError()))
    runner._terminate_process_tree(process)
    assert state["killed"] == 0
    monkeypatch.setattr(runner.os, "killpg", lambda *args: (_ for _ in ()).throw(OSError("denied")))
    runner._terminate_process_tree(process)
    assert state["killed"] == 1
    state["alive"] = False
    runner._terminate_process_tree(process)
    assert state["killed"] == 1


@pytest.mark.parametrize("error", [OSError("taskkill failed"), subprocess.TimeoutExpired("taskkill", 10)])
def test_terminate_process_tree_windows_fallback_kills_process(monkeypatch, error):
    state = {"killed": 0}
    process = SimpleNamespace(pid=321, poll=lambda: None,
                              kill=lambda: state.__setitem__("killed", state["killed"] + 1))
    monkeypatch.setattr(runner.os, "name", "nt")
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(error))
    runner._terminate_process_tree(process)
    assert state["killed"] == 1


def test_terminate_process_tree_unknown_platform_falls_back_to_direct_kill(monkeypatch):
    state = {"killed": 0}
    process = SimpleNamespace(pid=321, poll=lambda: None,
                              kill=lambda: state.__setitem__("killed", state["killed"] + 1))
    monkeypatch.setattr(runner.os, "name", "other")
    runner._terminate_process_tree(process)
    assert state["killed"] == 1


def test_run_command_raises_when_output_reader_fails(monkeypatch, tmp_path):
    class BrokenOutput:
        def read(self, size):
            raise OSError("read failure")

    class FakeProcess:
        pid = 1
        stdout = BrokenOutput()
        returncode = 0

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **k: FakeProcess())
    with pytest.raises(OSError, match="Could not capture command output"):
        runner._run_command(["fake"], cwd=tmp_path, timeout=1)


def test_run_command_timeout_kills_and_closes_stuck_reader(monkeypatch, tmp_path):
    class Output:
        closed = False

        def close(self):
            self.closed = True

    class FakeProcess:
        pid = 5
        stdout = Output()
        returncode = -9

        def __init__(self):
            self.wait_calls = 0
            self.killed = False

        def wait(self, timeout=None):
            self.wait_calls += 1
            if self.wait_calls <= 2:
                raise subprocess.TimeoutExpired("fake", timeout)
            return -9

        def poll(self):
            return None if not self.killed else -9

        def kill(self):
            self.killed = True

    class StuckThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

        def join(self, timeout=None):
            pass

        def is_alive(self):
            return True

    process = FakeProcess()
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **k: process)
    monkeypatch.setattr(runner, "Thread", StuckThread)
    monkeypatch.setattr(runner, "_terminate_process_tree", lambda p: None)
    with pytest.raises(subprocess.TimeoutExpired):
        runner._run_command(["fake"], cwd=tmp_path, timeout=1)
    assert process.killed
    assert process.stdout.closed


def _python_project(root):
    (root / "app.py").write_text("def add(a,b): return a+b\n", encoding="utf-8")
    tests = root / "tests"
    tests.mkdir(exist_ok=True)
    (tests / "test_app.py").write_text("def test_add(): assert 1+1==2\n", encoding="utf-8")


def test_run_checks_handles_missing_project_and_required_empty_project(tmp_path):
    missing = runner.run_checks(tmp_path / "missing")
    assert missing["results"][0]["reason"] == "Project directory does not exist."
    config = tmp_path / "required.toml"
    config.write_text("[project]\nlanguages=['python']\n", encoding="utf-8")
    empty = runner.run_checks(tmp_path, config_path=config)
    assert empty["status"] == "FAIL"
    assert empty["results"][0]["language"] == "python"


def test_run_checks_reports_required_language_without_default_command(tmp_path, monkeypatch):
    _python_project(tmp_path)
    monkeypatch.setattr(runner, "_default_plans", lambda root, files=None: {})
    monkeypatch.setattr(runner, "_has_test", lambda files, language, root=None: True)
    report = runner.run_checks(tmp_path)
    assert report["results"][0]["reason"].startswith("Language was required")


def test_run_checks_handles_result_directory_error(tmp_path):
    (tmp_path / "composer.json").write_text("{}", encoding="utf-8")
    (tmp_path / "testThing.php").write_text("<?php function testThing() {}", encoding="utf-8")
    (tmp_path / ".polyguard-results").write_text("not a directory", encoding="utf-8")
    report = runner.run_checks(tmp_path)
    assert report["status"] == "FAIL"
    assert "Could not prepare Polyguard result directory" in report["results"][0]["reason"]


def test_run_checks_handles_missing_relative_and_installed_executables(tmp_path, monkeypatch):
    _python_project(tmp_path)
    monkeypatch.setattr(runner, "_default_plans", lambda root, files=None: {"python": [["./missing-tool"]]})
    report = runner.run_checks(tmp_path)
    assert "Required command not found" in report["results"][0]["reason"]
    monkeypatch.setattr(runner, "_default_plans", lambda root, files=None: {"python": [["not-installed-tool"]]})
    monkeypatch.setattr(runner.shutil, "which", lambda command: None)
    report = runner.run_checks(tmp_path)
    assert "Required tool not installed" in report["results"][0]["reason"]


def test_run_checks_handles_command_failure_timeout_and_oserror(tmp_path, monkeypatch):
    _python_project(tmp_path)
    monkeypatch.setattr(runner, "_default_plans", lambda root, files=None: {"python": [["python", "-m", "pytest"]]})
    monkeypatch.setattr(runner, "_run_command", lambda *a, **k: subprocess.CompletedProcess(a[0], 2, "traceback"))
    failed = runner.run_checks(tmp_path)
    assert "exit code 2" in failed["results"][0]["reason"]
    monkeypatch.setattr(runner, "_run_command", lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired("python", 1, output=b"timeout-bytes")))
    timed_out = runner.run_checks(tmp_path)
    assert "timeout-bytes" in timed_out["results"][0]["output"]
    monkeypatch.setattr(runner, "_run_command", lambda *a, **k: (_ for _ in ()).throw(OSError("cannot launch")))
    launch_error = runner.run_checks(tmp_path)
    assert "Could not execute" in launch_error["results"][0]["reason"]


def test_run_checks_passes_with_real_evidence_and_fails_without_it(tmp_path, monkeypatch):
    _python_project(tmp_path)
    monkeypatch.setattr(runner, "_default_plans", lambda root, files=None: {"python": [["python", "-m", "pytest"]]})
    monkeypatch.setattr(runner, "_run_command", lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "2 passed"))
    passed = runner.run_checks(tmp_path)
    assert passed["status"] == "PASS"
    monkeypatch.setattr(runner, "_run_command", lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "completed"))
    failed = runner.run_checks(tmp_path)
    assert failed["status"] == "FAIL"
    assert "could not verify" in failed["results"][0]["reason"].lower()


def test_run_checks_handles_native_report_failure_and_skip_only(tmp_path, monkeypatch):
    (tmp_path / "pom.xml").write_text("<project/>", encoding="utf-8")
    tests = tmp_path / "src" / "test" / "java"
    tests.mkdir(parents=True)
    (tests / "ExampleTest.java").write_text("@Test void test() {}", encoding="utf-8")
    monkeypatch.setattr(runner, "_default_plans", lambda root, files=None: {"java": [["mvn", "test"]]})
    monkeypatch.setattr(runner.shutil, "which", lambda command: "/usr/bin/mvn")
    monkeypatch.setattr(runner, "_run_command", lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "BUILD SUCCESS"))
    no_report = runner.run_checks(tmp_path)
    assert "No fresh java test report" in no_report["results"][0]["reason"]
    report_path = tmp_path / "target" / "surefire-reports" / "TEST-example.xml"
    report_path.parent.mkdir(parents=True)
    def write_skip(*args, **kwargs):
        report_path.write_text(
            '<testsuite tests="1" failures="0" errors="0" skipped="1"><testcase><skipped/></testcase></testsuite>',
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args[0], 0, "BUILD SUCCESS")
    monkeypatch.setattr(runner, "_run_command", write_skip)
    skipped = runner.run_checks(tmp_path)
    assert "did not establish a passing test suite" in skipped["results"][0]["reason"]


def test_render_report_details_includes_each_available_field():
    report = {
        "status": "PASS",
        "results": [{
            "language": "python", "status": "PASS", "project_root": "/repo",
            "tests_passed": 2, "tests_total": 2, "tests_failed": 0, "tests_skipped": 0,
            "execution_evidence": "pytest summary", "reason": "", "output": "2 passed",
        }, {
            "language": "rust", "status": "FAIL", "project_root": "",
            "tests_passed": None, "tests_total": None, "tests_failed": None,
            "tests_skipped": None, "execution_evidence": "", "reason": "no tests",
            "output": "",
        }],
    }
    result = runner.render_report(report, details=True)
    assert "Project root: /repo" in result
    assert "Tests passed: 2" in result
    assert "Tests total: 2" in result
    assert "Evidence: pytest summary" in result
    assert "no tests" in result


def test_render_report_details_with_empty_results():
    assert runner.render_report({"status": "FAIL", "results": []}, details=True) == "FAIL"


def test_cli_main_returns_success_and_failure_statuses(monkeypatch, capsys):
    import polyguard.cli as cli

    reports = iter([
        {"status": "PASS", "results": []},
        {"status": "FAIL", "results": []},
    ])
    monkeypatch.setattr(cli, "run_checks", lambda *args, **kwargs: next(reports))
    monkeypatch.setattr(cli, "render_report", lambda report, **kwargs: report["status"])
    monkeypatch.setattr(sys, "argv", ["polyguard", "--path", ".", "--details"])
    assert cli.main() == 0
    assert capsys.readouterr().out.strip() == "PASS"
    assert cli.main() == 1
    assert capsys.readouterr().out.strip() == "FAIL"


def test_cli_module_main_guard_runs(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["polyguard", "--help"])
    with pytest.warns(RuntimeWarning, match="found in sys.modules"):
        with pytest.raises(SystemExit) as exc:
            runpy.run_module("polyguard.cli", run_name="__main__")
    assert exc.value.code == 0


def test_python310_tomli_fallback_import_path(monkeypatch):
    module_name = "polyguard._runner_python310_compat_test"
    compat = types.ModuleType(module_name)
    fake_tomli = types.ModuleType("tomli")
    fake_tomli.loads = lambda value: {"compat": value}
    monkeypatch.setitem(sys.modules, "tomli", fake_tomli)
    monkeypatch.setitem(sys.modules, module_name, compat)
    original_import = builtins.__import__

    def python310_import(name, *args, **kwargs):
        if name == "tomllib":
            raise ModuleNotFoundError("simulated Python 3.10 without tomllib")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", python310_import)
    source_path = Path(runner.__file__)
    source = source_path.read_text(encoding="utf-8")
    exec(compile(source, str(source_path), "exec"), compat.__dict__)
    assert compat.tomllib is fake_tomli
    assert compat.tomllib.loads("x") == {"compat": "x"}
