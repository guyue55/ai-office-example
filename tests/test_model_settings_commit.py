"""模型配置页提交语义：主操作是「保存并启用」，仅保存草稿不应再要额外确认。

草稿与生效修订必须分开，是因为启用会切换运行中的模型（服务端还要求当前没有
运行中任务），所以「保存并启用」需要一次明确确认；而仅保存草稿不改运行配置、
不调用模型，再拦一层确认只是增加点击。这两条约定写在模板里，容易被后续改动
悄悄改回去，因此在这里固定。
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (
    ROOT / "office-data-service/app/entrypoints/web/templates/model_settings.html"
).read_text(encoding="utf-8")


def footer() -> str:
    start = TEMPLATE.index('<div class="settings-footer">')
    return TEMPLATE[start : TEMPLATE.index("</div>", start)]


def test_form_no_longer_confirms_every_submit() -> None:
    """表单级确认会让「仅保存草稿」也弹窗，因此必须去掉。"""
    form = TEMPLATE[TEMPLATE.index('<form id="model-settings-form"') :]
    opening = form[: form.index(">")]
    assert "data-confirm-message" not in opening


def test_save_draft_is_secondary_without_confirm() -> None:
    """仅保存草稿是次要操作，且不该带自己的确认文案。"""
    block = footer()
    match = re.search(
        r"<button[^>]*data-pending-label=\"正在保存…\"[^>]*>仅保存草稿</button>", block
    )
    assert match, "找不到「仅保存草稿」按钮"
    assert 'class="secondary"' in match.group(0)
    assert "data-confirm-message" not in match.group(0)


def test_save_and_apply_is_primary_and_confirmed() -> None:
    """保存并启用是主操作，必须保留自己的确认文案。"""
    block = footer()
    match = re.search(r"<button[^>]*>保存并启用</button>", block)
    assert match, "找不到「保存并启用」按钮"
    assert 'formaction="/console/model-settings/save-apply"' in match.group(0)
    assert "data-confirm-message=" in match.group(0)
    assert 'class="secondary"' not in match.group(0)


def test_primary_action_comes_after_secondary() -> None:
    """视觉顺序上主操作应在次要操作之后（右侧），与共享按钮约定一致。"""
    block = footer()
    assert block.index("仅保存草稿") < block.index("保存并启用")


def test_footer_explains_the_difference() -> None:
    """按钮文案本身不足以说明差异，页脚需要一句说明。"""
    block = footer()
    assert "settings-footer-note" in block
    assert "不会切换正在使用的模型" in block


def test_without_apply_permission_only_draft_save_is_offered() -> None:
    """没有启用权限时不渲染「保存并启用」，并说明需由有权限的同事应用。"""
    block = footer()
    apply_part = block[block.index("{% if 'agent.config.apply' in me.permissions %}") :]
    assert "{% else %}保存后需由有启用权限的同事应用此配置。{% endif %}" in apply_part


SCRIPT = (ROOT / "office-data-service/app/entrypoints/web/templates/model-settings.js").read_text(
    encoding="utf-8"
)


def test_submit_guards_saving_when_secret_is_reused_at_a_new_url() -> None:
    """改地址后沿用原密钥，保存路径必须自己拦下，而不是交给服务端 422。

    服务端会以 422001 拒绝，但那时是一次整页错误响应，用户刚填的地址、模型、
    协议全部丢失。勾选框只在界面上出现不构成保护，提交路径也得检查。
    """
    submit = SCRIPT[SCRIPT.index("editor.addEventListener('submit'") :]
    assert "field('key-consent').hidden" in submit
    assert "reuse_secret_at_new_url.checked" in submit
    assert "event.preventDefault(); event.stopImmediatePropagation();" in submit


def test_inspect_path_returns_before_the_save_guard() -> None:
    """获取模型与测试连接走自己的分支，不能被保存的拦截逻辑二次触发。"""
    listener = SCRIPT[SCRIPT.index("editor.addEventListener('submit'") :]
    branch = listener[
        listener.index("endsWith('/models')") : listener.index("field('key-consent').hidden")
    ]
    assert "return;" in branch
