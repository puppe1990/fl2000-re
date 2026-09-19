"""CLI surface for clone vs extend. Parser only — no USB."""

from fl2000_re.cli import build_parser
from fl2000_re.letterbox import UNDERSCAN


def test_cli_accepts_extend():
    ns = build_parser().parse_args(["extend", "--seconds", "0"])
    assert ns.cmd == "extend"
    assert ns.seconds == 0.0


def test_cli_extend_is_listed_in_help():
    help_text = build_parser().format_help()
    assert "extend" in help_text
    assert "mirror" in help_text


def test_cli_fill_flags_default_to_hdmi_behavior():
    ns = build_parser().parse_args(["mirror"])
    assert ns.underscan == UNDERSCAN
    assert ns.stretch_x == 1.0


def test_cli_mirror_accepts_p2016_vga_fill():
    ns = build_parser().parse_args(["mirror", "--underscan", "1.0", "--stretch-x", "1.2"])
    assert ns.underscan == 1.0
    assert ns.stretch_x == 1.2


def test_cli_mode_defaults_to_hdmi_timing():
    ns = build_parser().parse_args(["mirror"])
    assert ns.mode == "720x480"


def test_cli_mode_accepts_vga_timing():
    ns = build_parser().parse_args(["mirror", "--mode", "640x480"])
    assert ns.mode == "640x480"


def test_cli_v_shift_defaults_to_zero():
    assert build_parser().parse_args(["mirror"]).v_shift == 0


def test_cli_v_shift_accepts_negative():
    ns = build_parser().parse_args(["mirror", "--v-shift", "-8"])
    assert ns.v_shift == -8


def test_cli_place_defaults_right():
    assert build_parser().parse_args(["extend"]).place == "right"


def test_cli_place_accepts_left():
    ns = build_parser().parse_args(["extend", "--place", "left"])
    assert ns.place == "left"


def test_cli_cursor_defaults_on():
    """Extend must show the pointer unless the user trades it for fps."""
    assert build_parser().parse_args(["extend"]).cursor is True


def test_cli_no_cursor_disables_pointer():
    ns = build_parser().parse_args(["extend", "--no-cursor"])
    assert ns.cursor is False


def test_cli_cursor_flag_enables_pointer():
    ns = build_parser().parse_args(["extend", "--cursor"])
    assert ns.cursor is True


def test_cli_help_lists_no_cursor():
    help_text = build_parser().format_help()
    assert "--cursor" in help_text
    assert "--no-cursor" in help_text


def test_cli_main_forwards_no_cursor(monkeypatch):
    import sys

    from fl2000_re import cli

    seen: dict[str, object] = {}

    class FakeFL2000:
        pass

    def fake_extend(*args: object) -> int:
        seen["cursor"] = args[7]
        return 0

    monkeypatch.setattr(cli, "FL2000", lambda: FakeFL2000())
    monkeypatch.setattr(cli, "cmd_extend", fake_extend)
    monkeypatch.setattr(sys, "argv", ["hagibis_re.py", "extend", "--no-cursor"])
    assert cli.main() == 0
    assert seen["cursor"] is False


def test_bin_extend_forwards_extra_flags():
    from pathlib import Path

    text = Path("bin/extend").read_text()
    assert '"$@"' in text
    assert "python -u" in text


def test_cli_accepts_monitor_log_with_out():
    ns = build_parser().parse_args(["monitor-log", "--out", "monitor.jsonl"])
    assert ns.cmd == "monitor-log"
    assert ns.out == "monitor.jsonl"
