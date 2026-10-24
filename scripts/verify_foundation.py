"""复跑框架质量门禁；报告留在忽略目录，外部副作用仅限独立开发环境。"""

import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from office_client.product import PRODUCT

ROOT = Path(__file__).resolve().parents[1]
SCOPE = [
    "deploy",
    ".github",
    ".dockerignore",
    ".gitattributes",
    "office-data-service",
    "clients/python",
    "packages/api-contracts",
    "contracts",
    "office-skills",
    "scripts",
    "tests",
    "pyproject.toml",
    "uv.lock",
]


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def check_timeout(command):
    """完整回归与浏览器允许冷启动，仍保留每阶段硬上限。"""
    if "pytest" in command or "docker" in command or "scripts/docker_smoke.py" in command:
        return 600
    if any(part.startswith("tests/e2e_") for part in command):
        return 180
    return 120


def run_check(command, *, cwd, env, timeout):
    """超时关闭本轮进程组，返回失败证据；不遗留继续写库的测试进程。"""
    with subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=os.name == "posix",
    ) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            return subprocess.CompletedProcess(command, process.returncode, stdout, stderr), False
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                # Windows验收调用显式终止该PID树，不依赖shell解释。
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
                process.kill()
            stdout, stderr = process.communicate(timeout=10)
            return subprocess.CompletedProcess(command, 124, stdout, stderr), True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true", help="显式允许独立本机开发库／身份的活体测试"
    )
    parser.add_argument("--docker", action="store_true", help="构建镜像并验收独立临时PG容器")
    args = parser.parse_args()
    output = ROOT / "runtime/foundation/verification"
    output.mkdir(parents=True, exist_ok=True)
    dirty = bool(
        git("diff", "HEAD", "--", *SCOPE)
        or git("ls-files", "--others", "--exclude-standard", "--", *SCOPE)
    )
    report = {
        "source_commit": git("rev-parse", "HEAD"),
        "source_scope_clean": not dirty,
        "scope": SCOPE,
        "live": args.live,
        "checks": [],
        "time": datetime.now(UTC).isoformat(),
        "status": "running",
    }
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    checks = [
        [
            "uv",
            "run",
            "ruff",
            "check",
            "office-data-service",
            "clients/python",
            "scripts",
            "tests",
            "packages/api-contracts",
        ],
        [
            "uv",
            "run",
            "ruff",
            "format",
            "--check",
            "office-data-service",
            "clients/python",
            "scripts",
            "tests",
            "packages/api-contracts",
        ],
        [
            "uv",
            "run",
            "mypy",
            "office-data-service/app",
            "clients/python/src",
            "--check-untyped-defs",
            "--ignore-missing-imports",
        ],
        [
            "uv",
            "run",
            "mypy",
            "packages/api-contracts/src",
            "packages/api-contracts/tests/typing",
            "--check-untyped-defs",
        ],
        ["uv", "run", "python", "scripts/check_architecture.py"],
        ["uv", "run", "python", "scripts/check_capability_coverage.py"],
        ["uv", "run", "python", "scripts/check_skills.py"],
        ["uv", "run", "python", "scripts/export_contracts.py", "--check"],
        ["uv", "run", "pytest", "-q"],
        ["uv", "build", "--package", "office-api-contracts"],
        ["uv", "build", "--package", "office-data-service"],
        ["uv", "build", "--package", "office-client"],
        [
            "uv",
            "run",
            "--no-project",
            "--with",
            "./dist/office_client-1.0.0-py3-none-any.whl",
            "--with",
            "./dist/office_api_contracts-1.0.0-py3-none-any.whl",
            PRODUCT["command"],
            "--help",
        ],
        [
            "uv",
            "run",
            "--no-project",
            "--with",
            "./dist/office_data_service-1.0.0-py3-none-any.whl",
            "--with",
            "./dist/office_api_contracts-1.0.0-py3-none-any.whl",
            "office-service",
            "--help",
        ],
        [
            "uv",
            "run",
            "--no-project",
            "--with",
            "./dist/office_data_service-1.0.0-py3-none-any.whl",
            "--with",
            "./dist/office_api_contracts-1.0.0-py3-none-any.whl",
            "python",
            "-c",
            (
                "from app.application.codebook import build_registry; "
                "from app.entrypoints.web.responses import TEMPLATES; "
                "assert build_registry().by_code(403003); "
                "TEMPLATES.get_template('signed_out.html'); "
                "TEMPLATES.get_template('pagination.html'); "
                "TEMPLATES.get_template('styles.css'); "
                "TEMPLATES.get_template('access_detail.html'); "
                "TEMPLATES.get_template('_workspace.html'); TEMPLATES.get_template('workspace.js'); TEMPLATES.get_template('form-state.js'); TEMPLATES.get_template('_interactions.html'); TEMPLATES.get_template('interactions.js'); "
                "TEMPLATES.get_template('_account_menu.html'); TEMPLATES.get_template('account-menu.js'); "
                "[TEMPLATES.get_template(name) for name in ('account_new.html','account_detail.html','account_operation.html','account_credential.html','account_link.html','account_operations.html','account_preview.html','account_security.html','client_authorizations.html','_filters.html')]; "
                "print('PACKAGE_RESOURCES_OK')"
            ),
        ],
    ]
    checks.append(
        [
            "uv",
            "run",
            "python",
            "-c",
            (
                "import runpy,tempfile,subprocess; from pathlib import Path; "
                "from office_client.product import PRODUCT; "
                "installer=runpy.run_path('scripts/install_client.py')['install']; "
                "stage=tempfile.TemporaryDirectory(prefix='office-install-'); "
                "installer(Path('dist'),Path(stage.name)); "
                "subprocess.run([str(Path(stage.name)/'bin'/PRODUCT['command']),'--help'],check=True); "
                "stage.cleanup()"
            ),
        ]
    )
    if args.live:
        checks.append(["uv", "run", "alembic", "-c", "office-data-service/alembic.ini", "check"])
        # 浏览器用例依次运行，避免多个用例同时调整同一开发员工的权限。
        checks.extend(
            [
                ["uv", "run", "python", f"tests/{filename}"]
                for filename in (
                    "e2e_web_console.py",
                    "e2e_workspace.py",
                    "e2e_account_menu.py",
                    "e2e_provisioning.py",
                    "e2e_accounts.py",
                    "e2e_client_authorizations.py",
                    "e2e_tables.py",
                    "e2e_interactions.py",
                    "e2e_page_review.py",
                    "e2e_login_layout.py",
                )
            ]
        )
    checks.append(["uv", "run", "python", "scripts/package_skills.py"])
    if args.docker:
        checks.extend(
            [
                [
                    "docker",
                    "build",
                    "-f",
                    "deploy/Dockerfile",
                    "-t",
                    "office-data-service:verification",
                    ".",
                ],
                [
                    "uv",
                    "run",
                    "python",
                    "scripts/docker_smoke.py",
                    "--image",
                    "office-data-service:verification",
                ],
            ]
        )
    for index, command in enumerate(checks, start=1):
        if "--no-project" in command:
            # 开发wheel可能沿用版本号；隔离并重装指定内部包，不能复用旧命令环境假验收。
            package = (
                "office-client"
                if any("office_client-" in part for part in command)
                else "office-data-service"
            )
            command = [
                *command[:3],
                "--isolated",
                "--no-cache",
                "--reinstall-package",
                package,
                "--reinstall-package",
                "office-api-contracts",
                *command[3:],
            ]
        env = dict(os.environ)
        env["OFFICE_LIVE_TESTS"] = "1" if args.live else "0"
        if "alembic" in command:
            env["OFFICE_CONFIG"] = str(ROOT / "runtime/foundation/owner.json")
        budget = check_timeout(command)
        result, timed_out = run_check(
            command,
            cwd=ROOT,
            env=env,
            timeout=budget,
        )
        content = result.stdout + result.stderr
        log = output / f"check-{index:02}.log"
        log.write_bytes(content)
        check = {
            "command": command,
            "exit_code": result.returncode,
            "output_sha256": hashlib.sha256(content).hexdigest(),
            "timeout_seconds": budget,
            "timed_out": timed_out,
        }
        if "pytest" in command:
            matched = re.search(rb"(\d+) passed", content)
            check["passed"] = int(matched.group(1)) if matched else 0
            skipped = re.search(rb"(\d+) skipped", content)
            check["skipped"] = int(skipped.group(1)) if skipped else 0
        report["checks"].append(check)
        if result.returncode:
            report["status"] = "failed"
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(f"检查{index}: exit={result.returncode}", flush=True)
        if result.returncode:
            raise SystemExit("检查失败，请在本机查看忽略目录中的日志；未宣告交付完成")
    report["wheels"] = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT / "dist").glob("*.whl")
    }
    report["status"] = "passed"
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print("门禁通过；正式交付还需核对来源清洁状态、范围及活体验收。")


if __name__ == "__main__":
    main()
