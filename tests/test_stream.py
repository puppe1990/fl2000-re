"""Bulk pump cadence. No USB: pure timing helpers only."""

import pytest
from fl2000_re.stream import frame_period_s


def test_frame_period_is_one_over_freq():
    assert frame_period_s(60) == pytest.approx(1 / 60)


def test_frame_period_rejects_zero_freq():
    with pytest.raises(ValueError, match="freq=0"):
        frame_period_s(0)
