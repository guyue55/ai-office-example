"""工具预载、延迟加载与搜索边界；可见性只影响上下文，不替代服务端权限。"""

from types import SimpleNamespace

from office_agent.toolsets import (
    GROUPS,
    deferred_tools,
    preloaded_tools,
    search,
    tool_group,
)


def test_every_tool_belongs_to_exactly_one_group():
    seen = []
    for group in GROUPS.values():
        seen.extend(group["tools"])  # type: ignore[arg-type]
    assert len(seen) == len(set(seen))
    for name in seen:
        assert tool_group(str(name)) is not None


def test_page_and_prompt_decide_preload_and_defer():
    data = preloaded_tools("仪表盘", "查询记录")
    assert {"read_whoami", "read_records"} <= data
    assert "read_access_list" in deferred_tools("仪表盘", "查询记录")

    access = preloaded_tools("权限目录", "我具备的权限")
    assert {"read_access_list", "read_access_detail"} <= access
    assert "read_records" in deferred_tools("权限目录", "我具备的权限")

    account = preloaded_tools("账号操作记录", "查账号")
    assert "read_account_history" in account

    write = preloaded_tools("仪表盘", "新建一个项目")
    assert "prepare_data" in write
    assert "prepare_access" in deferred_tools("仪表盘", "新建一个项目")


def test_search_matches_chinese_and_english_keywords_with_bound():
    tools = [
        SimpleNamespace(name="read_records", description="查询记录"),
        SimpleNamespace(name="read_access_list", description="人员与权限"),
        SimpleNamespace(name="prepare_data", description="变更预览 data"),
    ]
    assert search(None, ["记录"], tools) == ["read_records"]
    assert "read_access_list" in search(None, ["权限"], tools)
    assert "prepare_data" in search(None, ["新建项目"], tools)
    assert search(None, ["不存在的词"], tools) == []
    many = [SimpleNamespace(name=f"read_x{i}", description="记录") for i in range(30)]
    assert len(search(None, ["记录"], many)) <= 10


def test_chinese_synonym_finds_tool_with_english_name():
    tools = [
        SimpleNamespace(name="read_pending_identities", description="查询待认领身份"),
        SimpleNamespace(name="read_authorizations", description="客户端授权会话"),
    ]
    assert search(None, ["待认领身份"], tools) == ["read_pending_identities"]
    assert search(None, ["客户端授权"], tools) == ["read_authorizations"]
