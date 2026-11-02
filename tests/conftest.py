"""Repository-wide pytest policy: no test may be skipped."""

import pytest


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    skipped = reporter.stats.get("skipped", []) if reporter is not None else []
    if skipped:
        reporter.write_sep("!", f"ERROR: {len(skipped)} test(s) were skipped; skipping is forbidden.")
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
