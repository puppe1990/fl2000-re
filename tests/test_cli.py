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
