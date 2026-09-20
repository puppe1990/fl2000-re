"""Timestamped log wrapper. Pure text streams — no USB, no clock dependency."""

import io
import sys

from fl2000_re.log_time import TimestampedStream, install_timestamped_streams, stamp


def test_stamp_is_bracketed_local_time():
    line = stamp(clock=lambda: 0.0)
    assert line.startswith("[")
    assert line.endswith("] ")
    assert line.count("[") == 1


def test_stream_stamps_each_line():
    ticks = iter([1000.0, 2000.0])
    out = io.StringIO()
    stream = TimestampedStream(out, clock=lambda: next(ticks))
    stream.write("um\n")
    stream.write("dois\n")
    assert out.getvalue().count("[") == 2
    assert out.getvalue().endswith("dois\n")


def test_stream_buffers_partial_line_until_newline():
    out = io.StringIO()
    stream = TimestampedStream(out, clock=lambda: 0.0)
    stream.write("sem\n")
    stream.write("quebra")
    assert "quebra" not in out.getvalue()
    stream.write(" ainda\n")
    assert "quebra ainda" in out.getvalue()


def test_stream_flush_emits_pending_without_newline():
    out = io.StringIO()
    stream = TimestampedStream(out, clock=lambda: 0.0)
    stream.write("solto")
    stream.flush()
    assert "solto" in out.getvalue()


def test_stream_delegates_unknown_attributes():
    out = io.StringIO()
    stream = TimestampedStream(out)
    stream.write("x\n")
    assert stream.getvalue() == out.getvalue()


def test_install_wraps_once(monkeypatch):
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    install_timestamped_streams(clock=lambda: 0.0)
    first_out, first_err = sys.stdout, sys.stderr
    install_timestamped_streams(clock=lambda: 0.0)
    assert sys.stdout is first_out
    assert sys.stderr is first_err
    assert isinstance(first_out, TimestampedStream)
    assert isinstance(first_err, TimestampedStream)
