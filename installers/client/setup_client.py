"""项目隔离安装：不修改全局CLI、AI工具配置或操作系统信任。"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from delivery_config import read_env


def ensure_connection(root, *, interactive=None, input_fn=None):
    """首次人工安装可交互填写；AI已有有效配置时不提问，不猜员工IP。"""
    path = root / ".env"
    if path.is_symlink():
        raise ValueError("公开配置不能是软链")
    try:
        values = read_env(path)
        hostname = urlsplit(values["OFFICE_PUBLIC_URL"]).hostname or ""
        needs_input = hostname == "office.example"
    except (OSError, ValueError):
        needs_input = True
    if not needs_input:
        return
    if (root / ".office/source.json").exists():
        raise ValueError("已安装目录的连接配置无效，请按新连接流程处理，不会原地切换身份源")
    if interactive is None:
        interactive = sys.stdin.isatty()
    if not interactive:
        raise ValueError(
            "请由AI询问企业服务地址并填写本包.env，再运行安装；示例OFFICE_PUBLIC_URL=https://office.example、OFFICE_DEPLOYMENT_MODE=https。不能使用员工电脑IP"
        )
    ask = input_fn or input
    service = ask("请输入管理员提供的服务器根地址（不是本机IP）：").strip()
    mode = "https"
    if service.startswith("http://"):
        if ask("HTTP仅用于明文验收，输入yes确认：").strip().lower() != "yes":
            raise ValueError("未确认HTTP模式，未修改配置")
        mode = "lan-http-demo"
    content = "OFFICE_PUBLIC_URL=" + json.dumps(service) + "\nOFFICE_DEPLOYMENT_MODE=" + mode + "\n"
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=root, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(content)
    try:
        values = read_env(temporary)
        if urlsplit(values["OFFICE_PUBLIC_URL"]).hostname == "office.example":
            raise ValueError("请提供真实企业服务地址，不能使用交付占位地址")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def load_bundle(root):
    manifest = json.loads((root / "bundle.json").read_text(encoding="utf-8"))
    if manifest.get("connection_config") == ".env" and not (root / ".env").is_file():
        raise ValueError("缺少服务连接.env，请通过AI询问企业地址后生成配置，不会使用隐藏默认地址")
    if (root / ".env").exists():
        if (root / ".env").is_symlink():
            raise ValueError("公开配置不能是软链")
        values = read_env(root / ".env")
        manifest["service"] = values["OFFICE_PUBLIC_URL"]
        manifest["allow_lan_http_demo"] = values["OFFICE_DEPLOYMENT_MODE"] == "lan-http-demo"
    url = urlsplit(manifest["service"])
    if (
        (url.scheme != "https" and not manifest.get("allow_lan_http_demo", False))
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
        or url.path not in ("", "/")
    ):
        raise ValueError("交付包必须预配置企业HTTPS根地址")
    if manifest.get("format") != 1 or not manifest.get("files"):
        raise ValueError("交付清单格式无效")
    for name, digest in manifest["files"].items():
        relative = Path(name)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or "\\" in name
            or ":" in name
            or (relative.parts and relative.parts[0] == ".office")
        ):
            raise ValueError("交付清单路径越界")
        path = root / relative
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("交付包不接受软链")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("交付文件摘要不匹配：" + name)
    return manifest


def stage_connection(root, destination, service, *, allow_lan_http_demo=False):
    """只从已验摘要的公开制品建立新连接目录；不复制.office或凭证。"""
    manifest = load_bundle(root)
    if destination.is_symlink():
        raise ValueError("新连接目录不能是软链")
    destination = destination.resolve()
    if destination.exists():
        raise ValueError("新连接目录已存在，不会覆盖")
    mode = "lan-http-demo" if allow_lan_http_demo else "https"
    with tempfile.TemporaryDirectory(prefix=".connection-", dir=destination.parent) as temp:
        staged = Path(temp) / "delivery"
        staged.mkdir()
        for name in manifest["files"]:
            source = root / name
            target = staged / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        shutil.copy2(root / "bundle.json", staged / "bundle.json")
        (staged / ".env").write_text(
            "OFFICE_PUBLIC_URL=" + json.dumps(service) + "\nOFFICE_DEPLOYMENT_MODE=" + mode + "\n",
            encoding="utf-8",
        )
        read_env(staged / ".env")
        load_bundle(staged)
        staged.rename(destination)
    return {"status": "connection_staged", "directory": str(destination), "login_migrated": False}


def parse_connection_report(completed, service):
    """成功须核对结构与服务地址，不能仅凭进程退出码声称已连通。"""
    try:
        report = json.loads(completed.stdout)
    except (ValueError, TypeError):
        report = None
    if (
        isinstance(report, dict)
        and report.get("service") == service
        and report.get("ok") is True
        and report.get("online_verified") is True
        and report.get("identity_verified") is False
        and report.get("credential_io") is False
        and completed.returncode == 0
    ):
        return True, None
    issue = report.get("issue") if isinstance(report, dict) else None
    if not isinstance(issue, dict) or not isinstance(issue.get("code"), str):
        issue = {
            "code": "invalid_check_result",
            "stage": "client_check",
            "http_status": None,
            "message": "连接检查没有返回有效结果，请运行connection-check核对",
        }
    else:
        issue = {key: issue.get(key) for key in ("code", "stage", "http_status", "message")}
    return False, issue


def install(root, uv):
    manifest = load_bundle(root)
    state = root / ".office"
    if state.is_symlink() or not state.resolve().is_relative_to(root.resolve()):
        raise ValueError("安装状态目录不能指向交付目录之外")
    state.mkdir(mode=0o700, exist_ok=True)
    for name in (
        "venv",
        "install.lock",
        "source.json",
        "resolved-requirements.txt",
        "installation.json",
        "connection.json",
    ):
        if (state / name).is_symlink():
            raise ValueError("安装状态文件不能是软链")
    # 操作系统级锁会随进程退出释放；不能靠遗留PID文件判断。
    lock = (state / "install.lock").open("a+b")
    if os.name == "nt":
        import msvcrt

        lock.seek(0)
        lock.write(b"0")
        lock.flush()
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    identity = {
        "service": manifest["service"],
        "files": manifest["files"],
        "allow_lan_http_demo": manifest.get("allow_lan_http_demo", False),
    }
    marker = state / "source.json"
    if marker.exists() and json.loads(marker.read_text(encoding="utf-8")) != identity:
        raise ValueError("已安装内容与当前交付包不同，请使用新的交付目录，避免静默升级")
    # 首次失败也记录来源，重跑同包可修复；不读取已有凭证。
    marker.write_text(json.dumps(identity, ensure_ascii=False), encoding="utf-8")
    environment = state / "venv"
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    product = json.loads((root / "product.json").read_text(encoding="utf-8"))
    command = product["command"]
    if not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", command):
        raise ValueError("交付命令名无效")
    cli = environment / ("Scripts/" + command + ".exe" if os.name == "nt" else "bin/" + command)
    if not python.exists():
        subprocess.run([uv, "venv", "--python", "3.13", str(environment)], check=True)
    wheels = [root / name for name in manifest["files"] if name.endswith(".whl")]
    if len(wheels) != 2:
        raise ValueError("需要客户端和契约库两份本地轮包")
    requirements = (root / "requirements.txt").read_text(encoding="utf-8")
    for path in wheels:
        requirements += (
            "\n"
            + path.as_uri()
            + " --hash=sha256:"
            + manifest["files"][path.relative_to(root).as_posix()]
            + "\n"
        )
    resolved = state / "resolved-requirements.txt"
    resolved.write_text(requirements, encoding="utf-8")
    subprocess.run(
        [uv, "pip", "sync", "--require-hashes", "--python", str(python), str(resolved)],
        check=True,
    )
    connection = state / "connection.json"
    connection.write_text(
        json.dumps(
            {
                "service": manifest["service"],
                "allow_lan_http_demo": manifest.get("allow_lan_http_demo", False),
            }
        ),
        encoding="utf-8",
    )
    subprocess.run([str(cli), "--config", str(connection), "doctor"], check=True)
    checked = subprocess.run(
        [str(cli), "--config", str(connection), "connection-check"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    connection_verified, connection_issue = parse_connection_report(checked, manifest["service"])
    result = {
        "connection_verified": connection_verified,
        "connection_issue": connection_issue,
        "next_action": "verify_identity" if connection_verified else "confirm_service_address",
        "connection_notice": "服务入口已核验，请核对当前身份；未登录时再登录"
        if connection_verified
        else "本地安装完成，但连接未核验；请通过AI确认地址、网络及服务状态，修正后重试connection-check，暂不登录或提交数据",
        "status": "installed",
        "cli": str(cli),
        "python": str(python),
        "service": manifest["service"],
        "skills": str(root / "office-skills"),
        "identity_verified": False,
        "identity_notice": "安装不读取员工凭证；先用whoami核对，未登录时再登录，不强制重复授权",
        "cli_args": ["--config", str(connection)],
        "notice": "HTTP验收模式，传输未加密，请勿用于正式业务"
        if manifest.get("allow_lan_http_demo")
        else "HTTPS",
        "host_global_registration": False,
    }
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=state, delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(result, stream, ensure_ascii=False, indent=2)
    temporary.replace(state / "installation.json")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    lock.close()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uv", required=True)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--stage-connection", type=Path, help="从可信制品生成独立的新连接目录")
    parser.add_argument("--service")
    parser.add_argument("--lan-http-demo", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    if args.stage_connection:
        if not args.service or args.verify_only:
            parser.error("新连接需要明确--service，且不能同时verify-only")
        print(
            json.dumps(
                stage_connection(
                    root,
                    args.stage_connection,
                    args.service,
                    allow_lan_http_demo=args.lan_http_demo,
                ),
                ensure_ascii=False,
            )
        )
    elif args.verify_only:
        load_bundle(root)
        print("BUNDLE_VERIFIED")
    else:
        ensure_connection(root)
        install(root, args.uv)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, EOFError) as error:
        print("安装未完成：" + str(error), file=sys.stderr)
        raise SystemExit(2) from None
    except subprocess.CalledProcessError as error:
        print(
            f"安装阶段失败（退出码{error.returncode}），请检查上方提示；不要继续登录。",
            file=sys.stderr,
        )
        raise SystemExit(2) from None
