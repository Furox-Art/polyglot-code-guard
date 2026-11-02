from pathlib import Path
import sys

from polyguard.runner import _has_test, discover, render_report, run_checks


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
    empty_test = tmp_path / "main_test.go"
    empty_test.write_text("package main\n", encoding="utf-8")
    assert not _has_test([empty_test], "go", tmp_path)
    empty_test.write_text("package main\nfunc TestAdd(t *testing.T) {}\n", encoding="utf-8")
    assert _has_test([empty_test], "go", tmp_path)


def test_rust_test_annotation_is_detected(tmp_path: Path) -> None:
    test_file = tmp_path / "lib.rs"
    test_file.write_text("#[test]\nfn works() {}\n", encoding="utf-8")
    assert _has_test([test_file], "rust", tmp_path)


def test_cpp_requires_registered_ctest_case(tmp_path: Path) -> None:
    cmake = tmp_path / "CMakeLists.txt"
    test_file = tmp_path / "test_math.cpp"
    test_file.write_text("TEST(Math, Adds) { EXPECT_EQ(1 + 1, 2); }\n", encoding="utf-8")
    cmake.write_text("enable_testing()\nadd_test(NAME math COMMAND math_tests)\n", encoding="utf-8")
    assert _has_test([cmake, test_file], "cpp", tmp_path)
    cmake.write_text("add_test(NAME math COMMAND math_tests)\n", encoding="utf-8")
    assert not _has_test([cmake, test_file], "cpp", tmp_path)


def test_custom_config_runs_exact_commands_and_patterns(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('app')\n", encoding="utf-8")
    tests_dir = tmp_path / "custom-tests"
    tests_dir.mkdir()
    (tests_dir / "suite.case").write_text("custom suite marker\n", encoding="utf-8")
    config = tmp_path / ".polyguard.toml"
    config.write_text(
        "[project]\nlanguages = ['python']\n\n"
        "[checks.python]\n"
        f"commands = [[{str(sys.executable)!r}, '-c', \"print('custom command passed')\"]]\n"
        "test_patterns = ['custom-tests/*.case']\n",
        encoding="utf-8",
    )
    report = run_checks(tmp_path)
    assert report["status"] == "PASS"
    assert report["results"][0]["output"].strip() == "custom command passed"


def test_custom_command_failure_is_fail(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('app')\n", encoding="utf-8")
    tests_dir = tmp_path / "custom-tests"
    tests_dir.mkdir()
    (tests_dir / "suite.case").write_text("marker\n", encoding="utf-8")
    config = tmp_path / ".polyguard.toml"
    config.write_text(
        "[checks.python]\n"
        f"commands = [[{str(sys.executable)!r}, '-c', 'raise SystemExit(7)']]\n"
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
    import json

    report = {"status": "PASS", "results": []}
    assert json.loads(render_report(report, as_json=True)) == report


def test_cli_accepts_config_and_timeout() -> None:
    from polyguard.cli import build_parser

    args = build_parser().parse_args(["verify", "--timeout", "12", "--config", "custom.toml"])
    assert args.verify == "verify"
    assert args.timeout == 12
    assert args.config == "custom.toml"
