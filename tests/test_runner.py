from pathlib import Path

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
    assert not _has_test([empty_test], "python")


def test_python_test_file_is_detected(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("def add(a, b): return a + b\n", encoding="utf-8")
    test_file = tmp_path / "test_app.py"
    test_file.write_text("def test_add(): assert 1 + 1 == 2\n", encoding="utf-8")
    assert _has_test([test_file], "python")
    assert any(language == "python" for language, _ in discover(tmp_path))


def test_go_requires_a_test_function(tmp_path: Path) -> None:
    empty_test = tmp_path / "main_test.go"
    empty_test.write_text("package main\n", encoding="utf-8")
    assert not _has_test([empty_test], "go")
    empty_test.write_text("package main\nfunc TestAdd(t *testing.T) {}\n", encoding="utf-8")
    assert _has_test([empty_test], "go")


def test_rust_test_annotation_is_detected(tmp_path: Path) -> None:
    test_file = tmp_path / "lib.rs"
    test_file.write_text("#[test]\nfn works() {}\n", encoding="utf-8")
    assert _has_test([test_file], "rust")


def test_cpp_requires_registered_ctest_case(tmp_path: Path) -> None:
    cmake = tmp_path / "CMakeLists.txt"
    test_file = tmp_path / "test_math.cpp"
    test_file.write_text("TEST(Math, Adds) { EXPECT_EQ(1 + 1, 2); }\n", encoding="utf-8")
    cmake.write_text("enable_testing()\nadd_test(NAME math COMMAND math_tests)\n", encoding="utf-8")
    assert _has_test([cmake, test_file], "cpp")
    cmake.write_text("add_test(NAME math COMMAND math_tests)\n", encoding="utf-8")
    assert not _has_test([cmake, test_file], "cpp")


def test_no_supported_language_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("not source code", encoding="utf-8")
    assert run_checks(tmp_path)["status"] == "FAIL"


def test_report_default_is_one_word() -> None:
    report = {"status": "FAIL", "results": []}
    assert render_report(report) == "FAIL"


def test_json_report_is_machine_readable() -> None:
    import json

    report = {"status": "PASS", "results": []}
    assert json.loads(render_report(report, as_json=True)) == report


def test_cli_accepts_verify_subcommand() -> None:
    from polyguard.cli import build_parser

    args = build_parser().parse_args(["verify", "--timeout", "12"])
    assert args.verify == "verify"
    assert args.timeout == 12
