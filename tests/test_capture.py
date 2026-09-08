"""Screen grab is injected; unit tests never call mss or screencapture."""

from fl2000_re.capture import grab_letterboxed_rgb


def test_grab_letterboxed_rgb_uses_injected_grabber():
    src = bytes([255, 0, 0]) * (20 * 10)

    def grab() -> tuple[bytes, int, int]:
        return src, 20, 10

    out = grab_letterboxed_rgb(20, 20, grab=grab)
    assert len(out) == 20 * 20 * 3
    assert out[0:3] == bytes([0, 0, 0])
    mid = (10 * 20 + 10) * 3
    assert out[mid : mid + 3] == bytes([255, 0, 0])
