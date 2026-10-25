"""在仓库外安装候选轮包并验证，拒绝将源码导入或editable安装当作发行包证据。"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from email.parser import BytesParser
from pathlib import Path


def verify(directory: Path):
    expected = {"office-agent", "office-client", "office-api-contracts"}
    wheels = {}
    for path in directory.glob("*.whl"):
        if path.is_symlink():
            raise ValueError("候选轮包不得是软链")
        with zipfile.ZipFile(path) as archive:
            names = [n for n in archive.namelist() if n.endswith(".dist-info/METADATA")]
            if len(names) != 1:
                raise ValueError("轮包元数据不唯一")
            name = BytesParser().parsebytes(archive.read(names[0]))["Name"]
            if name not in expected or name in wheels:
                raise ValueError("需要三个包各一个候选，不能混入历史版本")
            if name == "office-agent":
                assert "office_agent/py.typed" in archive.namelist()
                assert not any(
                    "test" in n.split("/") or "__pycache__" in n for n in archive.namelist()
                )
            wheels[name] = str(path.resolve())
    if set(wheels) != expected:
        raise ValueError("缺少Agent、SDK或契约轮包")
    env = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {"PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE", "PYTEST_ADDOPTS", "PYTEST_PLUGINS"}
    }
    env["PYTHONNOUSERSITE"] = "1"
    env["PYDANTIC_AI_NO_BANNER"] = "1"
    with tempfile.TemporaryDirectory(prefix="office-agent-consumer-") as temporary:
        root = Path(temporary)
        target = root / "env"
        subprocess.run([sys.executable, "-m", "venv", str(target)], env=env, check=True)
        python = target / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        subprocess.run(
            ["uv", "pip", "install", "--python", str(python), *wheels.values(), "pytest"],
            cwd=root,
            env=env,
            check=True,
        )
        probe = """import importlib.metadata as m, office_agent, office_client, sys
from pathlib import Path
for module in (office_agent,office_client):
 assert Path(module.__file__).is_relative_to(Path(sys.prefix))
assert m.version('office-agent') == office_agent.__version__
assert Path(office_agent.__file__).with_name('py.typed').is_file()
print('INSTALLED_MODULES_VERIFIED')
"""
        subprocess.run([str(python), "-I", "-c", probe], cwd=root, env=env, check=True)
        subprocess.run(
            [str(python), "-I", "-m", "office_client.cli", "agent", "--help"],
            cwd=root,
            env=env,
            check=True,
        )
        shutil.copytree(
            Path(__file__).resolve().parents[1] / "tests",
            root / "tests",
            ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"),
        )
        subprocess.run(
            [str(python), "-I", "-m", "pytest", "tests", "-q"], cwd=root, env=env, check=True
        )
        subprocess.run(
            ["uv", "pip", "check", "--python", str(python)], cwd=root, env=env, check=True
        )
    print(json.dumps({"status": "agent_install_verified", "packages": sorted(wheels)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheels", type=Path, required=True)
    verify(parser.parse_args().wheels)
