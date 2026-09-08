"""CLI surface for clone vs extend. Parser only — no USB."""

from fl2000_re.cli import build_parser


def test_cli_accepts_extend():
    ns = build_parser().parse_args(["extend", "--seconds", "0"])
    assert ns.cmd == "extend"
    assert ns.seconds == 0.0


def test_cli_extend_is_listed_in_help():
    help_text = build_parser().format_help()
    assert "extend" in help_text
    assert "mirror" in help_text
