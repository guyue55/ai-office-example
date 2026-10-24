"""显式调整本机开发realm期限；要求隔离验证证据，保留字段级回退快照。"""

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
from session_policy import ROOT, admin_headers, apply_policy, policy, read_policy


def runtime_file(path):
    path = path.resolve()
    if not path.is_relative_to((ROOT / "runtime/foundation").resolve()) or not path.is_file():
        raise RuntimeError("只接受本项目开发运行目录内的证据或回退文件")
    return path


def validate_shape(value):
    expected = policy()
    if not isinstance(value, dict) or set(value) != {"realm", "clients"}:
        raise RuntimeError("策略结构无效")
    if set(value["realm"]) != set(expected["realm"]) or set(value["clients"]) != set(
        expected["clients"]
    ):
        raise RuntimeError("策略包含未允许的字段或客户端")
    for key, item in value["realm"].items():
        if key == "revokeRefreshToken":
            valid = type(item) is bool
        else:
            valid = type(item) is int and 0 <= item <= 31536000
        if not valid:
            raise RuntimeError("策略取值无效")
    for name, attributes in value["clients"].items():
        if set(attributes) != set(expected["clients"][name]):
            raise RuntimeError("策略包含未允许的客户端属性")
        if any(
            item is not None
            and (not isinstance(item, str) or not item.isdecimal() or len(item) > 8)
            for item in attributes.values()
        ):
            raise RuntimeError("客户端期限无效")
    return value


def verify_evidence(path):
    data = json.loads(runtime_file(path).read_text())
    required = {
        "configured_policy",
        "refresh_rotation",
        "cli_refresh",
        "web_refresh",
        "independent_sessions",
        "idle_expiry",
        "maximum_lifetime",
    }
    checks = data.get("checks", {}).get("session_policy", {})
    if (
        data.get("source_frozen_before_run") is not True
        or data.get("status") != "passed"
        or any(checks.get(key) is not True for key in required)
    ):
        raise RuntimeError("隔离策略验证未通过，拒绝应用")
    age = (datetime.now(UTC) - datetime.fromisoformat(data["verified_at"])).total_seconds()
    if not 0 <= age <= 86400:
        raise RuntimeError("隔离证据已过期，请重新验证")
    sources = data.get("verification_source", {})
    files = [
        *sorted((ROOT / "office-data-service/app").rglob("*.py")),
        *sorted((ROOT / "clients/python/src/office_client").glob("*.py")),
        ROOT / "deploy/identity/session-policy.json",
        ROOT / "scripts/session_policy.py",
        ROOT / "scripts/session_policy_probe.py",
    ]
    if any(
        sources.get(str(p.relative_to(ROOT))) != hashlib.sha256(p.read_bytes()).hexdigest()
        for p in files
    ):
        raise RuntimeError("隔离证据与当前实现不一致，拒绝应用")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--check", action="store_true")
    modes.add_argument("--apply", action="store_true")
    modes.add_argument("--restore", type=Path)
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()
    config = json.loads((ROOT / "runtime/foundation/config.json").read_text())
    if (
        config.get("issuer") != "http://127.0.0.1:8081/realms/office"
        or config.get("database_port") != 55432
    ):
        raise RuntimeError("只允许本机独立开发环境，不操作客户实例")
    if args.apply:
        if args.evidence is None:
            raise RuntimeError("应用策略必须提供隔离验收证据")
        verify_evidence(args.evidence)
    desired = (
        validate_shape(json.loads(runtime_file(args.restore).read_text())["before"])
        if args.restore
        else validate_shape(policy())
    )
    with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as http:
        headers = admin_headers(http)
        before = read_policy(http, headers)
        if args.check:
            print(
                json.dumps(
                    {"matches_recommended": before == policy(), "current": before},
                    ensure_ascii=False,
                )
            )
            return
        folder = ROOT / "runtime/foundation/session-policy"
        folder.mkdir(mode=0o700, exist_ok=True)
        target = folder / (uuid4().hex + ".json")
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as output:
            json.dump(
                {"before": before, "after": desired, "created_at": datetime.now(UTC).isoformat()},
                output,
            )
        try:
            apply_policy(http, headers, desired)
            if read_policy(http, headers) != desired:
                raise RuntimeError("策略读回不一致")
        except Exception:  # noqa: BLE001 -- 仅回退本次列明字段，错误不回显凭证
            apply_policy(http, admin_headers(http), before)
            raise RuntimeError("策略应用失败，已尝试字段级回退，请核对实例") from None
        print("SESSION_POLICY_APPLIED rollback=" + str(target.relative_to(ROOT)))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # noqa: BLE001 -- 不输出管理令牌或请求体
        print(
            "策略操作未完成："
            + (str(error) if type(error) is RuntimeError else type(error).__name__)
        )
        raise SystemExit(1) from None
