"""帮助页只展示信息，保持现有命令与机器结果协议；不触发凭证或服务访问。"""

import argparse
import sys
from unittest.mock import Mock

import pytest
from office_client import cli
from office_client.cli_presentation import COMMAND_GROUPS, display_width
from office_client.product import PRODUCT


def test_no_arguments_is_help_without_side_effects(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["office"])
    monkeypatch.setattr(cli, "OfficeClient", Mock(side_effect=AssertionError("帮助不得访问服务")))
    cli.main()
    output = capsys.readouterr()
    assert output.err == ""
    assert "开始与诊断" in output.out and PRODUCT["command"] + " doctor" in output.out
    assert "required" not in output.out


def test_all_commands_are_grouped_once_and_have_chinese_descriptions():
    parser = cli.build_parser()
    commands = next(a.choices for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    declared = [name for _, items in COMMAND_GROUPS for name, _ in items]
    assert len(declared) == len(set(declared)) == len(commands)
    assert set(declared) == set(commands)
    for command in commands.values():
        assert command.description and any("\u4e00" <= c <= "\u9fff" for c in command.description)


@pytest.mark.parametrize("columns", [40, 60, 100])
def test_help_fits_chinese_display_width(monkeypatch, columns):
    monkeypatch.setenv("COLUMNS", str(columns))
    output = cli.build_parser().format_help()
    assert all(display_width(line) <= columns for line in output.splitlines())
    assert "\x1b" not in output
    assert "{logout," not in output


def test_subcommand_help_preserves_correct_usage_and_required_parameters(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["office", "aggregate", "-h"])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 0
    text = capsys.readouterr().out
    assert "用法：" + PRODUCT["command"] + " aggregate" in text
    assert "全局选项" not in text
    assert "--revision" in text and "--file" in text
    with pytest.raises(SystemExit) as error:
        cli.build_parser().parse_args(["aggregate", "p"])
    assert error.value.code == 2


def test_unknown_command_is_still_an_error(capsys):
    with pytest.raises(SystemExit) as error:
        cli.build_parser().parse_args(["nonexistent-command"])
    assert error.value.code == 2
    assert "nonexistent-command" in capsys.readouterr().err
