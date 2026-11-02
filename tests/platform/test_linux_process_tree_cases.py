"""Linux-specific process-tree behavior tests."""

from pathlib import Path
import json
import subprocess
import sys
import time

import pytest

import polyguard.runner as runner
from polyguard.runner import run_checks


def test_timeout_kills_descendant_after_parent_exits(tmp_path: Path) -> None:
    pid_file = tmp_path / "orphaned-child.pid"
    child_script = "import time; time.sleep(60)"
    parent_script = (
        "import subprocess, sys; "
        "child = subprocess.Popen([sys.executable, '-c', sys.argv[2]]); "
        "open(sys.argv[1], 'w', encoding='utf-8').write(str(child.pid))"
    )
    with pytest.raises(subprocess.TimeoutExpired):
        runner._run_command(
            [sys.executable, "-c", parent_script, str(pid_file), child_script],
            cwd=tmp_path,
            timeout=1,
        )

    assert pid_file.exists(), "The parent process did not launch its descendant."
    child_pid = int(pid_file.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        stat_file = Path(f"/proc/{child_pid}/stat")
        if not stat_file.exists():
            break
        fields = stat_file.read_text(encoding="utf-8").split()
        if len(fields) > 2 and fields[2] == "Z":
            break
        time.sleep(0.05)
    else:
        pytest.fail(f"Timed-out command left descendant {child_pid} running.")


def test_timeout_terminates_descendant_processes(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('app')\n", encoding="utf-8")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_marker.py").write_text("# configured test marker\n", encoding="utf-8")
    pid_file = tmp_path / "child.pid"
    child_script = (
        "import subprocess, sys, time; "
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        "open(sys.argv[1], 'w', encoding='utf-8').write(str(child.pid)); "
        "time.sleep(60)"
    )
    config = tmp_path / ".polyguard.toml"
    config.write_text(
        "[checks.python]\n"
        f"commands = [[{json.dumps(sys.executable)}, '-c', {json.dumps(child_script)}, "
        f"{json.dumps(str(pid_file))}]]\n"
        'test_patterns = ["tests/test_marker.py"]\n',
        encoding="utf-8",
    )

    report = run_checks(tmp_path, timeout=1)
    assert report["status"] == "FAIL"
    assert "timed out" in report["results"][0]["reason"].lower()
    assert pid_file.exists(), "The parent command did not launch its child process."
    child_pid = int(pid_file.read_text(encoding="utf-8"))

    deadline = time.monotonic() + 3
    child_running = True
    while time.monotonic() < deadline:
        stat_file = Path(f"/proc/{child_pid}/stat")
        if not stat_file.exists():
            child_running = False
            break
        fields = stat_file.read_text(encoding="utf-8").split()
        if len(fields) > 2 and fields[2] == "Z":
            child_running = False
            break
        time.sleep(0.05)
    assert not child_running, f"Timed-out command left child process {child_pid} running."
