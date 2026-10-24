"""验收执行器应如实记录超时，停止后续步骤并结束本轮子进程。"""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def runner():
    path = Path(__file__).resolve().parents[1] / "scripts/verify_foundation.py"
    spec = importlib.util.spec_from_file_location("foundation_verification", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_timeout_records_failure_instead_of_preserving_old_success(runner, tmp_path, monkeypatch):
    folder = tmp_path / "runtime/foundation/verification"
    folder.mkdir(parents=True)
    (folder / "report.json").write_text('{"status":"passed"}')
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "git", lambda *args: "baseline" if args[0] == "rev-parse" else "")
    monkeypatch.setattr(sys, "argv", ["verify_foundation.py"])
    calls = []

    def timeout(command, **kwargs):
        # 检查开始就撤下旧passed，不能等第一项完成后才替换。
        assert json.loads((folder / "report.json").read_text())["status"] == "running"
        calls.append(command)
        return subprocess.CompletedProcess(command, 124, b"partial evidence", b""), True

    monkeypatch.setattr(runner, "run_check", timeout)
    with pytest.raises(SystemExit, match="检查失败"):
        runner.main()
    report = json.loads((folder / "report.json").read_text())
    assert report["status"] == "failed"
    assert len(calls) == 1
    assert report["checks"][0]["timed_out"] is True
    assert report["checks"][0]["exit_code"] == 124
    assert (folder / "check-01.log").read_bytes() == b"partial evidence"


@pytest.mark.skipif(os.name != "posix", reason="本项验证POSIX真实进程组；Windows另验")
def test_actual_timeout_ends_descendant_and_retains_output(runner, tmp_path):
    code = (
        "import subprocess,sys,time; "
        "subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
        "print('started',flush=True); time.sleep(60)"
    )
    result, timed_out = runner.run_check(
        [sys.executable, "-c", code], cwd=tmp_path, env=os.environ.copy(), timeout=0.5
    )
    # 后代继承输出管道，若没有终止进程树，communicate无法在其10秒预算内结束。
    assert timed_out and result.returncode == 124 and b"started" in result.stdout


def test_success_preserves_real_exit_and_full_suite_has_bounded_budget(runner, tmp_path):
    result, timed_out = runner.run_check(
        [sys.executable, "-c", "print('complete')"],
        cwd=tmp_path,
        env=os.environ.copy(),
        timeout=5,
    )
    assert not timed_out and result.returncode == 0 and result.stdout == b"complete\n"
    assert 140 < runner.check_timeout(["uv", "run", "pytest", "-q"]) <= 600
