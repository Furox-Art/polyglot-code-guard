from pathlib import Path
import json
import sys

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


def test_go_json_evidence_counts_pass_events() -> None:
    output = "\n".join([
        json.dumps({"Action": "run", "Test": "TestAdd"}),
        json.dumps({"Action": "pass", "Test": "TestAdd", "Elapsed": 0.001}),
        json.dumps({"Action": "pass", "Package": "example"}),
    ])
    assert _execution_evidence("go", output)[0] == 1
    assert _execution_evidence("go", json.dumps({"Action": "pass", "Package": "example"}))[0] is None


def test_javascript_tap_evidence_requires_passing_tests() -> None:
    assert _execution_evidence("javascript", "# tests 2\n# pass 2\n# fail 0")[0] == 2
    assert _execution_evidence("javascript", "# tests 2\n# pass 0\n# fail 0")[0] is None


def test_custom_config_requires_execution_evidence(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('app')\n", encoding="utf-8")
    tests_dir = tmp_path / "custom-tests"
    tests_dir.mkdir()
    (tests_dir / "suite.case").write_text("custom suite marker\n", encoding="utf-8")
    config = tmp_path / ".polyguard.toml"
    config.write_text(
        "[checks.python]\n"
        f"commands = [[{sys.executable!r}, '-c', \"print('custom command passed')\"]]\n"
        "test_patterns = ['custom-tests/*.case']\n",
        encoding="utf-8",
    )
    report = run_checks(tmp_path)
    assert report["status"] == "FAIL"
    assert "could not verify" in report["results"][0]["reason"].lower()


def test_custom_config_can_parse_trusted_test_summary(tmp_path: Path) -> None:
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
    assert report["status"] == "PASS"
    assert report["results"][0]["tests_passed"] == 2


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


def test_invalid_success_regex_fails_closed(tmp_path: Path) -> None:
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
    assert "Invalid success regex" in report["results"][0]["reason"]


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
