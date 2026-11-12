"""开发栈与生产安装器必须给 realm 设置同一个品牌登录主题。

历史上开发栈建 realm 时没有写 loginTheme，开发者看到的是 Keycloak 默认外观，
而生产安装器 `deploy/installer/identity.py` 写了 office —— 同一份主题文件，
两条链路渲染结果不同。这里把两侧的字面量对齐锁死，避免再次漂移。

同时锁住三处「父主题硬编码外观」的覆写：4px 硬编码顶条、PatternFly 伪元素直角边框、
以及主题里不能出现第二份品牌主色（唯一来源是生成的 product.css）。
"""

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
THEME_CSS = ROOT / "deploy/identity/themes/office/login/resources/css/office.css"
COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)


def load(relative):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(relative.replace("/", "_"), path)
    module = importlib.util.module_from_spec(spec)
    # dev_stack 与安装器都会 `import identity_clients`，需把脚本目录放进搜索路径。
    import sys

    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(path.parent))
    return module


def theme_css():
    return THEME_CSS.read_text(encoding="utf-8")


def declarations():
    """去掉注释后的样式表：注释里可以提到旧色值，不算实现残留。"""
    return COMMENT.sub("", theme_css())


def compact(css):
    return re.sub(r"\s+", "", css)


def test_dev_stack_declares_login_theme():
    assert load("scripts/dev_stack.py").LOGIN_THEME == "office"


def test_installer_and_dev_stack_use_the_same_theme_name():
    source = (ROOT / "deploy/installer/identity.py").read_text(encoding="utf-8")
    match = re.search(r'"loginTheme":\s*"([^"]+)"', source)
    assert match is not None, "安装器必须显式设置 loginTheme"
    assert match.group(1) == load("scripts/dev_stack.py").LOGIN_THEME


def test_provision_creates_realm_with_login_theme():
    """创建分支必须带上主题，否则新环境从第一天就是默认外观。"""
    source = (ROOT / "scripts/dev_stack.py").read_text(encoding="utf-8")
    create = re.search(r'"realm":\s*"office".*?\n\s*\},', source, re.DOTALL)
    assert create is not None, "找不到创建 realm 的请求体"
    assert '"loginTheme": LOGIN_THEME' in create.group(0)


def test_provision_repairs_existing_realm_theme():
    """只在创建分支写 loginTheme 会让存量环境一直停留在默认外观。"""
    source = (ROOT / "scripts/dev_stack.py").read_text(encoding="utf-8")
    assert 'if current.get("loginTheme") != LOGIN_THEME' in source


def test_theme_drops_hardcoded_parent_blue_bar():
    """父主题的 4px 顶条写死调色板蓝 #0066CC，不读品牌色，必须被覆盖掉。"""
    css = compact(declarations())
    assert "border-top:0" in css
    assert "#0066CC" not in css.upper()
    assert "keycloak-card-top-color" not in css


def test_theme_rounds_patternfly_pseudo_border():
    """PatternFly 的输入框边框由 ::before/::after 画，只给容器圆角会留下直角。"""
    css = theme_css()
    assert ".pf-v5-c-form-control::before" in css
    assert ".pf-v5-c-form-control::after" in css


def test_theme_has_no_second_brand_color_source():
    """品牌主色只有 product.css 一份来源，主题里再写死会在换品牌后残留旧色。"""
    css = declarations()
    assert "--pf-v5-global--primary-color--100: #" not in css
    assert "--pf-v5-global--primary-color--200: #" not in css


def test_theme_follows_brand_for_dark_primary():
    """浅底组件会改用 primary-color--dark-100；不跟随就会退回内置蓝。"""
    assert "--pf-v5-global--primary-color--dark-100" in theme_css()
