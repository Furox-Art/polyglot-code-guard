from pathlib import Path
import json
import sys

import pytest

from polyguard.runner import (
    _execution_evidence,
    _has_test,
    discover,
    render_report,
    run_checks,
)


def test_python_project_without_tests_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('hello')\n", encoding="utf-8")
    report = run_checks(tmp_path)
    assert report["status"] == "FAIL"
    assert report["results"][0]["status"] == "FAIL"
    assert "No test suite" in report["results"][0]["reason"]


def test_empty_python_test_file_is_not_accepted(tmp_path: Path) -> None:
    empty_test = tmp_path / "test_app.py"
    empty_test.write_text("# intentionally empty\n", encoding="utf-8")
    assert not _has_test([empty_test], "python", tmp_path)


def test_python_test_file_is_detected(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("def add(a, b): return a + b\n", encoding="utf-8")
    test_file = tmp_path / "test_app.py"
    test_file.write_text("def test_add(): assert 1 + 1 == 2\n", encoding="utf-8")
    assert _has_test([test_file], "python", tmp_path)
    assert any(language == "python" for language, _ in discover(tmp_path))


def test_test_tree_detection_is_relative_to_project_root(tmp_path: Path) -> None:
    project = tmp_path / "tests" / "nested_project"
    project.mkdir(parents=True)
    source = project / "app.py"
    source.write_text("def test_like_name(): pass\n", encoding="utf-8")
    assert not _has_test([source], "python", project)


def test_go_requires_a_test_function(tmp_path: Path) -> None:
    test_file = tmp_path / "main_test.go"
    test_file.write_text("package main\n", encoding="utf-8")
    assert not _has_test([test_file], "go", tmp_path)
    test_file.write_text("package main\nfunc TestAdd(t *testing.T) {}\n", encoding="utf-8")
    assert _has_test([test_file], "go", tmp_path)

def test_rust_test_annotation_is_detected(tmp_path: Path) -> None:
    test_file = tmp_path / "lib.rs"
    test_file.write_text("#[test]\nfn works() {}\n", encoding="utf-8")
    assert _has_test([test_file], "rust", tmp_path)


def test_c_and_cpp_are_distinct_language_names() -> None:
    from polyguard.runner import normalize_language

    assert normalize_language("c") == "c"
    assert normalize_language("c++") == "cpp"
    assert normalize_language("cc") == "cpp"


def test_c_test_source_is_detected_only_with_registered_ctest(tmp_path: Path) -> None:
    cmake = tmp_path / "CMakeLists.txt"
    test_file = tmp_path / "tests" / "test_math.c"
    test_file.parent.mkdir()
    test_file.write_text("#include <assert.h>\nint main(void) { assert(1 + 1 == 2); }\n", encoding="utf-8")
    cmake.write_text("enable_testing()\nadd_test(NAME math COMMAND math_tests)\n", encoding="utf-8")
    assert _has_test([cmake, test_file], "c", tmp_path)


def test_cpp_test_source_is_detected_with_registered_ctest(tmp_path: Path) -> None:
    cmake = tmp_path / "CMakeLists.txt"
    test_file = tmp_path / "tests" / "test_math.cpp"
    test_file.parent.mkdir()
    test_file.write_text("#include <cassert>\nint main() { assert(1 + 1 == 2); }\n", encoding="utf-8")
    cmake.write_text("enable_testing()\nadd_test(NAME math COMMAND math_tests)\n", encoding="utf-8")
    assert _has_test([cmake, test_file], "cpp", tmp_path)


def test_python_execution_evidence_requires_positive_pass_count() -> None:
    assert _execution_evidence("python", "2 passed in 0.01s")[0] == 2
    assert _execution_evidence("python", "0 passed, 2 skipped in 0.01s")[0] is None
    assert _execution_evidence("python", "no tests ran")[0] is None


def test_rust_execution_evidence_requires_passed_tests() -> None:
    good = "test result: ok. 3 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out"
    empty = "test result: ok. 0 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out"
    assert _execution_evidence("rust", good)[0] == 3
    assert _execution_evidence("rust", empty)[0] is None


def test_java_evidence_rejects_skipped_only_suite() -> None:
    passed = "Tests run: 2, Failures: 0, Errors: 0, Skipped: 0"
    skipped = "Tests run: 2, Failures: 0, Errors: 0, Skipped: 2"
    assert _execution_evidence("java", passed)[0] == 2
    assert _execution_evidence("java", skipped)[0] is None


def test_ruby_evidence_rejects_skipped_only_suite() -> None:
    passed = "2 runs, 2 assertions, 0 failures, 0 errors, 0 skips"
    skipped = "2 runs, 0 assertions, 0 failures, 0 errors, 2 skips"
    assert _execution_evidence("ruby", passed)[0] == 2
    assert _execution_evidence("ruby", skipped)[0] is None


def test_go_json_evidence_rejects_non_object_event() -> None:
    assert _execution_evidence("go", "[]")[0] is None


def test_go_json_evidence_accepts_skip_events_alongside_passes() -> None:
    output = "\n".join([
        json.dumps({"Action": "run", "Test": "TestIncluded"}),
        json.dumps({"Action": "pass", "Test": "TestIncluded", "Elapsed": 0.001}),
        json.dumps({"Action": "run", "Test": "TestOptional"}),
        json.dumps({"Action": "skip", "Test": "TestOptional"}),
        json.dumps({"Action": "pass", "Package": "example"}),
    ])
    assert _execution_evidence("go", output)[0] == 1


def test_go_json_evidence_rejects_invalid_event_fields() -> None:
    invalid = [
        {"Action": "unknown", "Test": "TestAdd"},
        {"Action": "pass", "Test": 42},
        {"Action": "pass", "Package": []},
        {"Action": "pass", "Test": "TestAdd", "Elapsed": -1},
        {"Action": "pass", "Test": "TestAdd", "Elapsed": True},
        {"Action": "pass", "Test": "TestAdd", "Elapsed": float("nan")},
        {"Action": "pass", "Test": "TestAdd", "Elapsed": float("inf")},
    ]
    for event in invalid:
        assert _execution_evidence("go", json.dumps(event))[0] is None


def test_go_json_evidence_counts_pass_events() -> None:
    output = "\n".join([
        json.dumps({"Action": "run", "Test": "TestAdd"}),
        json.dumps({"Action": "pass", "Test": "TestAdd", "Elapsed": 0.001}),
        json.dumps({"Action": "pass", "Package": "example"}),
    ])
    assert _execution_evidence("go", output)[0] == 1
    assert _execution_evidence("go", json.dumps({"Action": "pass", "Package": "example"}))[0] is None


def test_ctest_evidence_accepts_summary_and_per_test_format_without_double_counting() -> None:
    output = "1/1 Test #1: math ... Passed 0.00 sec\n100% tests passed, 0 tests failed out of 1"
    assert _execution_evidence("c", output)[0] == 1
    assert _execution_evidence("cpp", "2/2 Test #1: a ... Passed\n2/2 Test #2: b ... Passed")[0] == 2


def test_javascript_tap_evidence_requires_passing_tests() -> None:
    assert _execution_evidence("javascript", "# tests 2\n# pass 2\n# fail 0")[0] == 2
    assert _execution_evidence("javascript", "# tests 2\n# pass 0\n# fail 0")[0] is None


def test_javascript_vitest_summary_requires_positive_test_count() -> None:
    default_reporter = "Test Files  4 passed (4)\n     Tests  16 passed | 4 skipped (20)"
    minimal_reporter = "Tests 2 passed (2)"
    zero_passed = "Test Files 1 passed (1)\nTests 0 passed | 5 skipped (5)"
    file_count_only = "Test Files 3 passed (3)"
    assert _execution_evidence("javascript", default_reporter)[0] == 16
    assert _execution_evidence("javascript", minimal_reporter)[0] == 2
    assert _execution_evidence("javascript", zero_passed)[0] is None
    assert _execution_evidence("javascript", file_count_only)[0] is None


def test_custom_config_requires_execution_evidence(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('app')\n", encoding="utf-8")
    tests_dir = tmp_path / "custom-tests"
    tests_dir.mkdir()
    (tests_dir / "suite.case").write_text("custom suite marker\n", encoding="utf-8")
    config = tmp_path / ".polyguard.toml"
    config.write_text(
        "[checks.python]\n"
        f"commands = [[{sys.executable!r}, '-c', \"print('1 passed in 0.01s')\"]]\n"
        "test_patterns = ['custom-tests/*.case']\n",
        encoding="utf-8",
    )
    report = run_checks(tmp_path)
    assert report["status"] == "FAIL"
    assert "custom commands cannot establish trusted" in report["results"][0]["reason"].lower()


def test_custom_success_patterns_cannot_manufacture_pass(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('app')\n", encoding="utf-8")
    tests_dir = tmp_path / "custom-tests"
    tests_dir.mkdir()
    (tests_dir / "suite.case").write_text("custom suite marker\n", encoding="utf-8")
    config = tmp_path / ".polyguard.toml"
    config.write_text(
        "[checks.python]\n"
        f"commands = [[{sys.executable!r}, '-c', \"print('2 passed in 0.01s')\"]]\n"
        "test_patterns = ['custom-tests/*.case']\n"
        "success_patterns = ['(?m)^([0-9]+) passed']\n",
        encoding="utf-8",
    )
    report = run_checks(tmp_path)
    assert report["status"] == "FAIL"
    assert "success_patterns is no longer supported" in report["results"][0]["reason"]


def test_custom_command_failure_is_fail(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('app')\n", encoding="utf-8")
    tests_dir = tmp_path / "custom-tests"
    tests_dir.mkdir()
    (tests_dir / "suite.case").write_text("marker\n", encoding="utf-8")
    config = tmp_path / ".polyguard.toml"
    config.write_text(
        "[checks.python]\n"
        f"commands = [[{sys.executable!r}, '-c', 'raise SystemExit(7)']]\n"
        "test_patterns = ['custom-tests/*.case']\n",
        encoding="utf-8",
    )
    report = run_checks(tmp_path)
    assert report["status"] == "FAIL"
    assert report["results"][0]["exit_code"] == 7


def test_config_test_patterns_must_match(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('app')\n", encoding="utf-8")
    config = tmp_path / ".polyguard.toml"
    config.write_text(
        "[checks.python]\ncommands = [['python', '-c', 'print(1)']]\n"
        "test_patterns = ['missing/**/*.test']\n",
        encoding="utf-8",
    )
    report = run_checks(tmp_path)
    assert report["status"] == "FAIL"
    assert "did not match" in report["results"][0]["reason"]


def test_custom_success_pattern_is_rejected_even_when_regex_is_valid(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('app')\n", encoding="utf-8")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "sample.txt").write_text("marker", encoding="utf-8")
    config = tmp_path / ".polyguard.toml"
    config.write_text(
        "[checks.python]\ncommands = [['python', '-c', 'print(1)']]\n"
        "test_patterns = ['tests/*']\n"
        "success_patterns = ['[']\n",
        encoding="utf-8",
    )
    report = run_checks(tmp_path)
    assert report["status"] == "FAIL"
    assert "success_patterns is no longer supported" in report["results"][0]["reason"]


def test_invalid_config_fails_closed(tmp_path: Path) -> None:
    config = tmp_path / "bad.toml"
    config.write_text("[checks.python\n", encoding="utf-8")
    report = run_checks(tmp_path, config_path=config)
    assert report["status"] == "FAIL"
    assert report["results"][0]["language"] == "configuration"


def test_missing_explicit_config_fails_closed(tmp_path: Path) -> None:
    report = run_checks(tmp_path, config_path=tmp_path / "missing.toml")
    assert report["status"] == "FAIL"


def test_config_cannot_traverse_outside_project(tmp_path: Path) -> None:
    config = tmp_path / ".polyguard.toml"
    config.write_text(
        "[checks.python]\ncommands = [['python', '-c', 'print(1)']]\n"
        "test_patterns = ['../outside/*']\n",
        encoding="utf-8",
    )
    (tmp_path / "app.py").write_text("print('app')\n", encoding="utf-8")
    report = run_checks(tmp_path)
    assert report["status"] == "FAIL"
    assert "outside" in report["results"][0]["reason"]


def test_no_supported_language_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("not source code", encoding="utf-8")
    assert run_checks(tmp_path)["status"] == "FAIL"


def test_timeout_validation_fails_closed(tmp_path: Path) -> None:
    report = run_checks(tmp_path, timeout=0)
    assert report["status"] == "FAIL"


def test_command_output_capture_is_bounded_and_keeps_the_tail(tmp_path: Path) -> None:
    import polyguard.runner as runner

    payload_bytes = runner.MAX_COMMAND_OUTPUT_BYTES * 5
    script = (
        "import sys; "
        f"sys.stdout.write('x' * {payload_bytes}); "
        "sys.stdout.write('\\nFINAL-OUTPUT-MARKER\\n')"
    )
    completed = runner._run_command([sys.executable, "-c", script], cwd=tmp_path, timeout=10)
    encoded = completed.stdout.encode("utf-8")

    assert completed.returncode == 0
    assert len(encoded) <= runner.MAX_COMMAND_OUTPUT_BYTES
    normalized_output = completed.stdout.replace("\r\n", "\n")
    assert normalized_output.endswith("\nFINAL-OUTPUT-MARKER\n")
    assert "FINAL-OUTPUT-MARKER" in normalized_output


def test_windows_timeout_uses_taskkill_process_tree(monkeypatch) -> None:
    from types import SimpleNamespace
    import polyguard.runner as runner

    calls = []
    state = {"alive": True}
    process = SimpleNamespace(
        pid=4321,
        poll=lambda: None if state["alive"] else 1,
        kill=lambda: calls.append("direct-kill"),
    )

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        state["alive"] = False
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(runner.os, "name", "nt")
    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    runner._terminate_process_tree(process)

    command, kwargs = calls[0]
    assert command == ["taskkill", "/PID", "4321", "/T", "/F"]
    assert kwargs["timeout"] == 10
    assert "direct-kill" not in calls


def test_report_default_is_one_word() -> None:
    report = {"status": "FAIL", "results": []}
    assert render_report(report) == "FAIL"


def test_json_report_is_machine_readable() -> None:
    report = {"status": "PASS", "results": []}
    assert json.loads(render_report(report, as_json=True)) == report


def test_cli_accepts_config_and_timeout() -> None:
    from polyguard.cli import build_parser

    args = build_parser().parse_args(["verify", "--timeout", "12", "--config", "custom.toml"])
    assert args.verify == "verify"
    assert args.timeout == 12
    assert args.config == "custom.toml"

def test_all_skipped_pytest_suite_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("def add(a, b): return a + b\n", encoding="utf-8")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_app.py").write_text(
        "import pytest\n@pytest.mark.skip(reason='skip-only suite')\ndef test_skipped(): pass\n",
        encoding="utf-8",
    )
    report = run_checks(tmp_path)
    assert report["status"] == "FAIL"
    assert "could not verify" in report["results"][0]["reason"].lower()


def test_monorepo_runs_each_language_from_its_own_project_root(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace
    import polyguard.runner as runner

    projects = {
        "apps/api": ("pyproject.toml", "tests/test_api.py", "def test_api(): assert 2 + 2 == 4\n"),
        "services/worker": ("go.mod", "worker_test.go", "package worker\nfunc TestWorker(t *testing.T) {}\n"),
        "libs/core": ("Cargo.toml", "src/lib.rs", "#[cfg(test)] mod tests { #[test] fn core_works() {} }\n"),
    }
    for directory, (manifest, test_path, content) in projects.items():
        (tmp_path / directory / manifest).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / directory / manifest).write_text(
            '[package]\nname = "fixture"\nversion = "0.1.0"\nedition = "2021"\n'
            if manifest == "Cargo.toml"
            else "module example.com/worker\ngo 1.22\n"
            if manifest == "go.mod"
            else "[project]\nname = 'api-fixture'\n",
            encoding="utf-8",
        )
        target = tmp_path / directory / test_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    calls = []

    def fake_run(command, *, cwd, **kwargs):
        calls.append((command, Path(cwd)))
        if "pytest" in command:
            output = "1 passed in 0.01s"
        elif command[0] == "cargo":
            output = "test result: ok. 1 passed; 0 failed; 0 ignored"
        elif command[0] == "go":
            output = json.dumps({"Action": "pass", "Test": "TestWorker"})
        else:
            output = ""
        return SimpleNamespace(returncode=0, stdout=output)

    monkeypatch.setattr(runner, "_run_command", fake_run)
    monkeypatch.setattr(runner.shutil, "which", lambda executable: f"/fake/{executable}")

    report = run_checks(tmp_path)

    assert report["status"] == "PASS", report
    assert {(item["language"], Path(item["project_root"]).relative_to(tmp_path).as_posix())
            for item in report["results"]} == {
        ("python", "apps/api"),
        ("go", "services/worker"),
        ("rust", "libs/core"),
    }
    assert {cwd.relative_to(tmp_path).as_posix() for _, cwd in calls} == {
        "apps/api",
        "services/worker",
        "libs/core",
    }


def test_execution_cache_is_scoped_by_language(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace
    import polyguard.runner as runner

    (tmp_path / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
    (tmp_path / "go.mod").write_text("module example.test/cache\ngo 1.22\n", encoding="utf-8")
    (tmp_path / "test_app.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
    (tmp_path / "main_test.go").write_text(
        "package main\nfunc TestCache(t *testing.T) {}\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        runner, "_default_plans",
        lambda root, files: {"go": [["fake-test"]], "python": [["fake-test"]]},
    )
    monkeypatch.setattr(
        runner, "_run_command",
        lambda command, **kwargs: SimpleNamespace(
            returncode=0, stdout=json.dumps({"Action": "pass", "Test": "TestCache"})
        ),
    )
    monkeypatch.setattr(runner.shutil, "which", lambda executable: "/fake/test")

    report = run_checks(tmp_path)
    results = {item["language"]: item for item in report["results"]}
    assert results["go"]["status"] == "PASS"
    assert results["python"]["status"] == "FAIL"


def test_monorepo_keeps_same_language_packages_separate(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace
    import polyguard.runner as runner

    expected_roots = {"packages/one", "packages/two"}
    for directory in expected_roots:
        package = tmp_path / directory
        package.mkdir(parents=True)
        (package / "pyproject.toml").write_text("[project]\nname = 'fixture'\n", encoding="utf-8")
        tests_dir = package / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_app.py").write_text("def test_ok(): assert True\n", encoding="utf-8")

    calls = []

    def fake_run(command, *, cwd, **kwargs):
        calls.append(Path(cwd))
        return SimpleNamespace(returncode=0, stdout="1 passed in 0.01s" if "pytest" in command else "")

    monkeypatch.setattr(runner, "_run_command", fake_run)
    report = run_checks(tmp_path)

    assert report["status"] == "PASS", report
    assert len(report["results"]) == 2
    assert {Path(item["project_root"]).relative_to(tmp_path).as_posix() for item in report["results"]} == expected_roots
    assert {path.relative_to(tmp_path).as_posix() for path in calls} == expected_roots


def test_one_failing_package_makes_monorepo_fail(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace
    import polyguard.runner as runner

    for directory in ("packages/healthy", "packages/broken"):
        package = tmp_path / directory
        package.mkdir(parents=True)
        (package / "pyproject.toml").write_text("[project]\nname = 'fixture'\n", encoding="utf-8")
        tests_dir = package / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_app.py").write_text("def test_ok(): assert True\n", encoding="utf-8")

    def fake_run(command, *, cwd, **kwargs):
        if "pytest" in command and Path(cwd).name == "broken":
            return SimpleNamespace(returncode=1, stdout="1 failed")
        return SimpleNamespace(returncode=0, stdout="1 passed" if "pytest" in command else "")

    monkeypatch.setattr(runner, "_run_command", fake_run)
    report = run_checks(tmp_path)

    assert report["status"] == "FAIL"
    assert {item["status"] for item in report["results"]} == {"PASS", "FAIL"}


def test_tests_missing_from_parent_project_is_not_hidden_by_nested_package(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace
    import polyguard.runner as runner

    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'parent'\n", encoding="utf-8")
    package = tmp_path / "packages" / "api"
    package.mkdir(parents=True)
    (package / "pyproject.toml").write_text("[project]\nname = 'api'\n", encoding="utf-8")
    tests_dir = package / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_api.py").write_text("def test_ok(): assert True\n", encoding="utf-8")

    monkeypatch.setattr(
        runner, "_run_command",
        lambda command, *, cwd, **kwargs: SimpleNamespace(
            returncode=0, stdout="1 passed" if "pytest" in command else ""
        ),
    )
    report = run_checks(tmp_path)

    assert report["status"] == "FAIL", report
    results = {Path(item["project_root"]): item for item in report["results"]}
    assert set(results) == {tmp_path, package}
    assert results[tmp_path]["status"] == "FAIL"
    assert "no test suite" in results[tmp_path]["reason"].lower()
    assert results[package]["status"] == "PASS"


def test_parent_python_package_does_not_execute_nested_package_tests(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace
    import polyguard.runner as runner

    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'workspace'\n", encoding="utf-8")
    parent_tests = tmp_path / "tests"
    parent_tests.mkdir()
    (parent_tests / "test_parent.py").write_text("def test_parent(): assert True\n", encoding="utf-8")

    child = tmp_path / "packages" / "child"
    child_tests = child / "tests"
    child_tests.mkdir(parents=True)
    (child / "pyproject.toml").write_text("[project]\nname = 'child'\n", encoding="utf-8")
    (child_tests / "test_child.py").write_text("def test_child(): assert True\n", encoding="utf-8")

    calls = []

    def fake_run(command, *, cwd, **kwargs):
        if "pytest" in command:
            calls.append((Path(cwd), command))
            return SimpleNamespace(returncode=0, stdout="1 passed")
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(runner, "_run_command", fake_run)
    report = run_checks(tmp_path)

    assert report["status"] == "PASS", report
    parent_command = next(command for cwd, command in calls if cwd == tmp_path)
    child_command = next(command for cwd, command in calls if cwd == child)
    assert "tests/test_parent.py" in parent_command
    assert "packages/child/tests/test_child.py" not in parent_command
    assert "tests/test_child.py" in child_command


def test_junit_xml_counts_pass_fail_and_skip(tmp_path: Path) -> None:
    from polyguard.runner import _parse_junit_xml

    report = tmp_path / "junit.xml"
    report.write_text(
        '<testsuites tests="3" failures="1" errors="0" skipped="1">'
        '<testsuite name="suite" tests="3" failures="1" errors="0" skipped="1">'
        '<testcase name="pass"/><testcase name="fail"><failure/></testcase>'
        '<testcase name="skip"><skipped/></testcase>'
        '</testsuite></testsuites>',
        encoding="utf-8",
    )
    assert _parse_junit_xml(report) == (3, 1, 1)


def test_trx_counts_pass_fail_and_not_executed(tmp_path: Path) -> None:
    from polyguard.runner import _parse_trx

    report = tmp_path / "results.trx"
    report.write_text(
        '<TestRun><ResultSummary><Counters total="3" executed="2" passed="1" '
        'failed="1" error="0" aborted="0" timeout="0" notExecuted="1" '
        'inconclusive="0" notRunnable="0"/></ResultSummary>'
        '<Results><UnitTestResult outcome="Passed"/><UnitTestResult outcome="Failed"/>'
        '<UnitTestResult outcome="NotExecuted"/></Results></TestRun>',
        encoding="utf-8",
    )
    assert _parse_trx(report) == (3, 1, 1)


def test_csharp_exit_zero_without_fresh_trx_report_fails_closed(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace
    import polyguard.runner as runner

    (tmp_path / "Fixture.csproj").write_text("<Project />", encoding="utf-8")
    tests_dir = tmp_path / "Tests"
    tests_dir.mkdir()
    (tests_dir / "AdditionTests.cs").write_text(
        "public class AdditionTests { [Fact] public void AdditionWorks() {} }",
        encoding="utf-8",
    )
    monkeypatch.setattr(runner.shutil, "which", lambda executable: f"/fake/{executable}")
    monkeypatch.setattr(
        runner,
        "_run_command",
        lambda command, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="Passed! - Failed: 0, Passed: 1, Skipped: 0, Total: 1",
        ),
    )
    report = runner.run_checks(tmp_path)
    assert report["status"] == "FAIL"
    assert "No fresh csharp test report" in report["results"][0]["reason"]


def test_csharp_all_skipped_trx_report_fails_closed(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace
    import polyguard.runner as runner

    (tmp_path / "Fixture.csproj").write_text("<Project />", encoding="utf-8")
    tests_dir = tmp_path / "Tests"
    tests_dir.mkdir()
    (tests_dir / "AdditionTests.cs").write_text(
        "public class AdditionTests { [Fact] public void AdditionWorks() {} }",
        encoding="utf-8",
    )

    def fake_run(command, *, cwd, **kwargs):
        result_dir = Path(cwd) / ".polyguard-results"
        result_dir.mkdir(exist_ok=True)
        (result_dir / "polyguard.trx").write_text(
            '<TestRun><ResultSummary><Counters total="1" executed="0" passed="0" '
            'failed="0" error="0" notExecuted="1"/></ResultSummary>'
            '<Results><UnitTestResult outcome="NotExecuted"/></Results></TestRun>',
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="Build succeeded.")

    monkeypatch.setattr(runner.shutil, "which", lambda executable: f"/fake/{executable}")
    monkeypatch.setattr(runner, "_run_command", fake_run)
    report = runner.run_checks(tmp_path)
    assert report["status"] == "FAIL"
    assert report["results"][0]["tests_passed"] == 0
    assert report["results"][0]["tests_skipped"] == 1


def test_javascript_workspace_runs_once_from_workspace_root(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace
    import polyguard.runner as runner

    (tmp_path / "package.json").write_text(
        json.dumps({
            "name": "root-workspace",
            "private": True,
            "workspaces": ["packages/*"],
            "scripts": {"test": "echo root"},
        }),
        encoding="utf-8",
    )
    package = tmp_path / "packages" / "api"
    package.mkdir(parents=True)
    (package / "package.json").write_text(
        json.dumps({"name": "api", "scripts": {"test": "node --test"}}),
        encoding="utf-8",
    )
    tests_dir = package / "test"
    tests_dir.mkdir()
    (tests_dir / "api.test.js").write_text(
        "test('api works', () => {});",
        encoding="utf-8",
    )
    calls = []

    def fake_run(command, *, cwd, **kwargs):
        calls.append((command, Path(cwd)))
        return SimpleNamespace(
            returncode=0,
            stdout="# tests 1\n# pass 1\n# fail 0\n",
        )

    monkeypatch.setattr(runner, "_run_command", fake_run)
    monkeypatch.setattr(runner.shutil, "which", lambda executable: f"/fake/{executable}")
    report = runner.run_checks(tmp_path)

    assert report["status"] == "PASS", report
    assert len(report["results"]) == 1
    assert Path(report["results"][0]["project_root"]) == tmp_path
    assert len(calls) == 1
    assert calls[0][0][:2] == ["node", "--test"]
    assert calls[0][0][0] != "npm"


def test_javascript_ignores_arbitrary_npm_test_script(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace
    import polyguard.runner as runner

    (tmp_path / "package.json").write_text(
        json.dumps({"name": "fake-script", "scripts": {"test": "echo 'Tests 99 passed (99)'"}}),
        encoding="utf-8",
    )
    tests_dir = tmp_path / "test"
    tests_dir.mkdir()
    (tests_dir / "math.test.js").write_text(
        "const test = require('node:test'); test('works', () => {});\n",
        encoding="utf-8",
    )
    calls = []

    def fake_run(command, *, cwd, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="# tests 1\n# pass 1\n# fail 0\n")

    monkeypatch.setattr(runner, "_run_command", fake_run)
    monkeypatch.setattr(runner.shutil, "which", lambda executable: f"/fake/{executable}")
    report = runner.run_checks(tmp_path)

    assert report["status"] == "PASS", report
    assert calls == [["node", "--test"]]
    assert report["results"][0]["tests_passed"] == 1


def test_maven_does_not_hide_unlisted_nested_project(tmp_path: Path) -> None:
    import polyguard.runner as runner

    (tmp_path / "pom.xml").write_text(
        "<project><modules><module>modules/api</module></modules></project>",
        encoding="utf-8",
    )
    member = tmp_path / "modules" / "api"
    member.mkdir(parents=True)
    (member / "pom.xml").write_text("<project />", encoding="utf-8")
    independent = tmp_path / "independent"
    independent.mkdir()
    (independent / "pom.xml").write_text("<project />", encoding="utf-8")

    units = runner._discover_project_units(tmp_path)
    java_roots = {unit.root for unit in units if unit.language == "java"}
    assert tmp_path in java_roots
    assert independent in java_roots
    assert member not in java_roots


def test_gradle_does_not_hide_unincluded_nested_project(tmp_path: Path) -> None:
    import polyguard.runner as runner

    (tmp_path / "settings.gradle").write_text('include(":api")\n', encoding="utf-8")
    api = tmp_path / "api"
    api.mkdir()
    (api / "build.gradle").write_text("plugins { id 'java' }\n", encoding="utf-8")
    independent = tmp_path / "independent"
    independent.mkdir()
    (independent / "build.gradle").write_text("plugins { id 'java' }\n", encoding="utf-8")

    units = runner._discover_project_units(tmp_path)
    java_roots = {unit.root for unit in units if unit.language == "java"}
    assert tmp_path in java_roots
    assert independent in java_roots
    assert api not in java_roots


def test_dotnet_does_not_hide_project_missing_from_solution(tmp_path: Path) -> None:
    import polyguard.runner as runner

    (tmp_path / "Workspace.sln").write_text(
        'Project("{FAE04EC0-301F-11D3-BF4B-00C04F79EFBC}") = "Included", '
        '"included\\Included.csproj", "{11111111-1111-1111-1111-111111111111}"\n'
        'EndProject\n',
        encoding="utf-8",
    )
    included = tmp_path / "included"
    included.mkdir()
    (included / "Included.csproj").write_text("<Project />", encoding="utf-8")
    independent = tmp_path / "independent"
    independent.mkdir()
    (independent / "Independent.csproj").write_text("<Project />", encoding="utf-8")

    units = runner._discover_project_units(tmp_path)
    csharp_roots = {unit.root for unit in units if unit.language == "csharp"}
    assert tmp_path in csharp_roots
    assert independent in csharp_roots
    assert included not in csharp_roots


def test_config_test_patterns_cannot_match_nested_child_tests(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='parent'\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("value = 1\n", encoding="utf-8")
    child = tmp_path / "packages" / "api"
    (child / "tests").mkdir(parents=True)
    (child / "pyproject.toml").write_text("[project]\nname='child'\n", encoding="utf-8")
    (child / "tests" / "test_api.py").write_text("def test_api(): assert True\n", encoding="utf-8")
    config = tmp_path / ".polyguard.toml"
    config.write_text(
        "[checks.python]\n"
        f"commands = [[{sys.executable!r}, '-c', 'print(1)']]\n"
        "test_patterns = ['**/test_api.py']\n",
        encoding="utf-8",
    )

    report = run_checks(tmp_path)
    parent_result = next(item for item in report["results"] if Path(item["project_root"]) == tmp_path)
    assert parent_result["status"] == "FAIL"
    assert "did not match" in parent_result["reason"].lower()


def test_cargo_workspace_runs_once_from_workspace_root(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace
    import polyguard.runner as runner

    (tmp_path / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["crates/*"]\nresolver = "2"\n',
        encoding="utf-8",
    )
    member = tmp_path / "crates" / "api"
    (member / "src").mkdir(parents=True)
    (member / "Cargo.toml").write_text(
        '[package]\nname = "api"\nversion = "0.1.0"\nedition = "2021"\n',
        encoding="utf-8",
    )
    (member / "src" / "lib.rs").write_text(
        "#[cfg(test)] mod tests { #[test] fn works() {} }\n",
        encoding="utf-8",
    )
    calls = []

    def fake_run(command, *, cwd, **kwargs):
        calls.append((command, Path(cwd)))
        return SimpleNamespace(
            returncode=0,
            stdout="test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out\n",
        )

    monkeypatch.setattr(runner, "_run_command", fake_run)
    monkeypatch.setattr(runner.shutil, "which", lambda executable: f"/fake/{executable}")
    report = runner.run_checks(tmp_path)

    assert report["status"] == "PASS", report
    assert len(report["results"]) == 1
    assert Path(report["results"][0]["project_root"]) == tmp_path
    assert len(calls) == 1
    assert "--workspace" in calls[0][0]


def test_maven_modules_run_once_from_aggregator_root(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace
    import polyguard.runner as runner

    (tmp_path / "pom.xml").write_text(
        "<project><modelVersion>4.0.0</modelVersion><modules><module>modules/api</module></modules></project>",
        encoding="utf-8",
    )
    member = tmp_path / "modules" / "api"
    test_file = member / "src" / "test" / "java" / "ApiTest.java"
    test_file.parent.mkdir(parents=True)
    (member / "pom.xml").write_text("<project><modelVersion>4.0.0</modelVersion></project>", encoding="utf-8")
    test_file.write_text("class ApiTest { @Test void apiWorks() {} }", encoding="utf-8")
    calls = []

    def fake_run(command, *, cwd, **kwargs):
        calls.append((command, Path(cwd)))
        report_dir = member / "target" / "surefire-reports"
        report_dir.mkdir(parents=True)
        (report_dir / "TEST-ApiTest.xml").write_text(
            '<testsuite name="ApiTest" tests="1" failures="0" errors="0" skipped="0">'
            '<testcase name="apiWorks"/></testsuite>',
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(runner, "_run_command", fake_run)
    monkeypatch.setattr(runner.shutil, "which", lambda executable: f"/fake/{executable}")
    report = runner.run_checks(tmp_path)

    assert report["status"] == "PASS", report
    assert len(report["results"]) == 1
    assert Path(report["results"][0]["project_root"]) == tmp_path
    assert report["results"][0]["tests_passed"] == 1
    assert len(calls) == 1
    assert calls[0][1] == tmp_path


def test_gradle_settings_aggregator_runs_once_at_root(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace
    import polyguard.runner as runner

    (tmp_path / "settings.gradle").write_text('rootProject.name = "workspace"\ninclude(\n    ":api"\n)\nproject(":api").projectDir = file("modules/api")\n', encoding="utf-8")
    member = tmp_path / "modules" / "api"
    test_file = member / "src" / "test" / "java" / "ApiTest.java"
    test_file.parent.mkdir(parents=True)
    (member / "build.gradle").write_text("plugins { id 'java' }\n", encoding="utf-8")
    test_file.write_text("class ApiTest { @Test void apiWorks() {} }", encoding="utf-8")
    calls = []

    def fake_run(command, *, cwd, **kwargs):
        calls.append((command, Path(cwd)))
        report_dir = member / "build" / "test-results" / "test"
        report_dir.mkdir(parents=True)
        (report_dir / "TEST-ApiTest.xml").write_text(
            '<testsuite name="ApiTest" tests="1" failures="0" errors="0" skipped="0">'
            '<testcase name="apiWorks"/></testsuite>',
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(runner, "_run_command", fake_run)
    monkeypatch.setattr(runner.shutil, "which", lambda executable: f"/fake/{executable}")
    report = runner.run_checks(tmp_path)

    assert report["status"] == "PASS", report
    assert len(report["results"]) == 1
    assert Path(report["results"][0]["project_root"]) == tmp_path
    assert len(calls) == 1
    assert calls[0][1] == tmp_path
    assert calls[0][0][0] == "gradle"


def test_dotnet_solution_runs_child_projects_once(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace
    import polyguard.runner as runner

    (tmp_path / "Workspace.sln").write_text(
        'Microsoft Visual Studio Solution File\n'
        'Project("{FAE04EC0-301F-11D3-BF4B-00C04F79EFBC}") = "Api.Tests", '
        '"tests\\Api.Tests\\Api.Tests.csproj", "{11111111-1111-1111-1111-111111111111}"\n'
        'EndProject\n',
        encoding="utf-8",
    )
    member = tmp_path / "tests" / "Api.Tests"
    member.mkdir(parents=True)
    (member / "Api.Tests.csproj").write_text("<Project />", encoding="utf-8")
    (member / "AdditionTests.cs").write_text("public class AdditionTests { [Fact] public void Works() {} }", encoding="utf-8")
    calls = []

    def fake_run(command, *, cwd, **kwargs):
        calls.append((command, Path(cwd)))
        report_dir = Path(cwd) / ".polyguard-results"
        report_dir.mkdir(exist_ok=True)
        (report_dir / "polyguard.trx").write_text(
            '<TestRun><ResultSummary><Counters total="1" executed="1" passed="1" '
            'failed="0" error="0" notExecuted="0"/></ResultSummary>'
            '<Results><UnitTestResult outcome="Passed"/></Results></TestRun>',
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(runner, "_run_command", fake_run)
    monkeypatch.setattr(runner.shutil, "which", lambda executable: f"/fake/{executable}")
    report = runner.run_checks(tmp_path)

    assert report["status"] == "PASS", report
    assert len(report["results"]) == 1
    assert Path(report["results"][0]["project_root"]) == tmp_path
    assert len(calls) == 1
    assert calls[0][1] == tmp_path


def test_java_native_report_must_be_created_during_current_run(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace
    import os
    import time
    import polyguard.runner as runner

    (tmp_path / "pom.xml").write_text("<project/>", encoding="utf-8")
    tests_dir = tmp_path / "src" / "test" / "java"
    tests_dir.mkdir(parents=True)
    (tests_dir / "MathTest.java").write_text("@Test class MathTest {}", encoding="utf-8")
    stale = tmp_path / "target" / "surefire-reports" / "TEST-stale.xml"
    stale.parent.mkdir(parents=True)
    stale.write_text('<testsuite tests="1" failures="0" skipped="0"/>', encoding="utf-8")
    old = time.time() - 60
    os.utime(stale, (old, old))

    monkeypatch.setattr(runner.shutil, "which", lambda executable: f"/fake/{executable}")
    monkeypatch.setattr(
        runner, "_run_command",
        lambda command, **kwargs: SimpleNamespace(returncode=0, stdout="BUILD SUCCESS"),
    )
    report = runner.run_checks(tmp_path)
    assert report["status"] == "FAIL"
    assert "No fresh java test report" in report["results"][0]["reason"]


def test_junit_aggregate_counters_without_testcases_are_not_evidence(tmp_path: Path) -> None:
    from polyguard.runner import _parse_junit_xml

    report = tmp_path / "empty.xml"
    report.write_text(
        '<testsuites tests="8" failures="0" errors="0" skipped="0">'
        '<testsuite name="empty" tests="8" failures="0" errors="0" skipped="0"/>'
        '</testsuites>',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="counter does not match testcase records"):
        _parse_junit_xml(report)


def test_native_report_parsers_reject_wrong_roots_and_invalid_trx_counters(tmp_path: Path) -> None:
    from polyguard.runner import _parse_junit_xml, _parse_trx

    junit = tmp_path / "wrong.xml"
    junit.write_text("<html><body>1 passed</body></html>", encoding="utf-8")
    with pytest.raises(ValueError, match="JUnit root"):
        _parse_junit_xml(junit)

    trx = tmp_path / "bad.trx"
    trx.write_text(
        '<TestRun><ResultSummary><Counters total="-1" passed="1" failed="0"/>'
        '</ResultSummary></TestRun>',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="required counters|negative"):
        _parse_trx(trx)

    trx.write_text(
        '<TestRun><ResultSummary><Counters total="1" failed="0"/>'
        '</ResultSummary></TestRun>',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="required counters"):
        _parse_trx(trx)


def test_native_report_parsers_reject_result_record_mismatches(tmp_path: Path) -> None:
    from polyguard.runner import _parse_junit_xml, _parse_trx

    junit = tmp_path / "mismatch.xml"
    junit.write_text(
        '<testsuite tests="1" failures="1" errors="0" skipped="0">'
        '<testcase name="ok"/></testsuite>',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="failure/error counters"):
        _parse_junit_xml(junit)

    trx = tmp_path / "mismatch.trx"
    trx.write_text(
        '<TestRun><ResultSummary><Counters total="1" passed="1" failed="0"/>'
        '</ResultSummary><Results><UnitTestResult outcome="Failed"/></Results></TestRun>',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="records do not match"):
        _parse_trx(trx)


def test_project_file_discovery_excludes_symlinked_files(tmp_path: Path, monkeypatch) -> None:
    from polyguard.runner import _project_files

    root = tmp_path / "project"
    root.mkdir()
    link = root / "test_external.py"
    link.write_text("symlink placeholder", encoding="utf-8")
    original_is_symlink = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == link or original_is_symlink(path))
    assert link not in _project_files(root)


def test_files_for_unit_does_not_exclude_nested_different_language_project(tmp_path: Path) -> None:
    from polyguard.runner import ProjectUnit, _files_for_unit

    root = tmp_path
    parent = root / "pyproject.toml"
    parent.write_text("[project]\nname='root'\n", encoding="utf-8")
    child = root / "services" / "java"
    child.mkdir(parents=True)
    (child / "pom.xml").write_text("<project/>", encoding="utf-8")
    python_test = child / "test_bridge.py"
    python_test.write_text("def test_bridge(): assert True\n", encoding="utf-8")
    units = [ProjectUnit(root, "python"), ProjectUnit(child, "java")]
    files = _files_for_unit(root, units[0], units)
    assert python_test in files


def test_swift_default_plan_uses_native_test_runner_summary(tmp_path: Path) -> None:
    from polyguard.runner import _default_plans

    (tmp_path / "Package.swift").write_text("// swift-tools-version: 6.0\n", encoding="utf-8")
    plans = _default_plans(tmp_path, [tmp_path / "Package.swift"])
    command = plans["swift"][0]
    assert command == ["swift", "test", "--parallel"]


def test_windows_maven_wrapper_uses_cmd(tmp_path: Path, monkeypatch) -> None:
    import polyguard.runner as runner

    (tmp_path / "pom.xml").write_text("<project/>", encoding="utf-8")
    (tmp_path / "mvnw.cmd").write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setattr(runner.sys, "platform", "win32")
    command = runner._default_plans(tmp_path, [tmp_path / "pom.xml", tmp_path / "mvnw.cmd"])["java"][0]
    assert command[:3] == ["cmd", "/c", "mvnw.cmd"]


def test_windows_gradle_wrapper_uses_cmd(tmp_path: Path, monkeypatch) -> None:
    import polyguard.runner as runner

    (tmp_path / "settings.gradle").write_text("rootProject.name='fixture'\n", encoding="utf-8")
    (tmp_path / "gradlew.bat").write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setattr(runner.sys, "platform", "win32")
    command = runner._default_plans(
        tmp_path, [tmp_path / "settings.gradle", tmp_path / "gradlew.bat"]
    )["java"][0]
    assert command[:3] == ["cmd", "/c", "gradlew.bat"]


def test_trx_inconsistent_counters_fail_closed(tmp_path: Path) -> None:
    import pytest
    from polyguard.runner import _parse_trx

    report = tmp_path / "inconsistent.trx"
    report.write_text(
        '<TestRun><ResultSummary><Counters total="4" passed="2" failed="0" '
        'error="0" notExecuted="1"/></ResultSummary></TestRun>',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="do not reconcile"):
        _parse_trx(report)


def test_mixed_c_cpp_cmake_invocation_runs_once(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace
    import polyguard.runner as runner

    (tmp_path / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.16)\nproject(mixed C CXX)\n"
        "enable_testing()\nadd_executable(test_c tests/test_c.c)\n"
        "add_executable(test_cpp tests/test_cpp.cpp)\n"
        "add_test(NAME c_math COMMAND test_c)\nadd_test(NAME cpp_math COMMAND test_cpp)\n",
        encoding="utf-8",
    )
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_c.c").write_text("#include <assert.h>\nint main(){assert(1);}\n", encoding="utf-8")
    (tests_dir / "test_cpp.cpp").write_text("#include <cassert>\nint main(){assert(1);}\n", encoding="utf-8")
    calls = []

    def fake_run(command, *, cwd, **kwargs):
        calls.append(command)
        if command[0] == "ctest":
            return SimpleNamespace(returncode=0, stdout="100% tests passed, 0 tests failed out of 2\n")
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(runner, "_run_command", fake_run)
    monkeypatch.setattr(runner.shutil, "which", lambda executable: f"/fake/{executable}")
    report = runner.run_checks(tmp_path)

    assert report["status"] == "PASS", report
    assert [item["language"] for item in report["results"]] == ["c", "cpp"]
    assert sum(1 for command in calls if command[0] == "ctest") == 1


def test_ruby_gemspec_alone_is_discovered(tmp_path: Path) -> None:
    from polyguard.runner import _discover_project_units

    (tmp_path / "example.gemspec").write_text(
        "Gem::Specification.new do |spec|\n  spec.name = 'example'\nend\n",
        encoding="utf-8",
    )
    units = _discover_project_units(tmp_path)
    assert any(unit.language == "ruby" and unit.root == tmp_path for unit in units)


@pytest.mark.parametrize("language", ["php", "csharp", "swift"])
def test_nested_native_report_projects_pass_from_their_own_root(
    tmp_path: Path, monkeypatch, language: str
) -> None:
    from types import SimpleNamespace
    import polyguard.runner as runner

    project = tmp_path / "services" / language
    project.mkdir(parents=True)
    if language == "php":
        (project / "composer.json").write_text(
            '{"require-dev":{"phpunit/phpunit":"^12.0"}}', encoding="utf-8"
        )
        tests = project / "tests"
        tests.mkdir()
        (tests / "ExampleTest.php").write_text(
            '<?php class ExampleTest extends \\PHPUnit\\Framework\\TestCase '
            '{ public function testWorks(): void {} }', encoding="utf-8"
        )
    elif language == "csharp":
        (project / "Fixture.csproj").write_text('<Project />', encoding="utf-8")
        tests = project / "Tests"
        tests.mkdir()
        (tests / "ExampleTests.cs").write_text(
            'public class ExampleTests { [Fact] public void Works() {} }', encoding="utf-8"
        )
    else:
        (project / "Package.swift").write_text(
            '// swift-tools-version: 5.9\nimport PackageDescription\n'
            'let package = Package(name: "Fixture", targets: '
            '[.target(name: "Fixture"), .testTarget(name: "FixtureTests", dependencies: ["Fixture"])])\n',
            encoding="utf-8",
        )
        _swift_tests = project / "Tests" / "FixtureTests"
        _swift_tests.mkdir(parents=True)
        (_swift_tests / "ExampleTests.swift").write_text(
            'import Testing\n@Test func works() {}\n', encoding="utf-8"
        )

    def fake_run(command, *, cwd, **kwargs):
        result_dir = Path(cwd) / ".polyguard-results"
        result_dir.mkdir(parents=True, exist_ok=True)
        if language == "csharp":
            (result_dir / "polyguard.trx").write_text(
                '<TestRun><ResultSummary><Counters total="1" executed="1" passed="1" '
                'failed="0" error="0" notExecuted="0" inconclusive="0" notRunnable="0"/>'
                '</ResultSummary><Results><UnitTestResult outcome="Passed"/></Results></TestRun>', encoding="utf-8"
            )
        elif language == "php":
            (result_dir / "phpunit.xml").write_text(
                '<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0">'
                    '<testcase name="works"/></testsuite></testsuites>',
                encoding="utf-8"
            )
        output = "Test run with 1 test in 1 suite passed." if language == "swift" else "Build succeeded."
        return SimpleNamespace(returncode=0, stdout=output)

    monkeypatch.setattr(runner.shutil, "which", lambda executable: f"/fake/{executable}")
    monkeypatch.setattr(runner, "_run_command", fake_run)
    report = runner.run_checks(tmp_path)

    assert report["status"] == "PASS", report
    assert len(report["results"]) == 1
    assert Path(report["results"][0]["project_root"]) == project
    assert report["results"][0]["tests_passed"] == 1
