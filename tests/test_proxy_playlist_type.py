"""Opaque HLS child URLs must not be mistaken for transport-stream segments."""

import pytest

from media_cli.media.local_proxy import _upstream_content_type


@pytest.mark.parametrize("hint,upstream,expected", [
    ("video/MP2T", "application/vnd.apple.mpegurl; charset=utf-8", "application/vnd.apple.mpegurl; charset=utf-8"),
    ("video/mp4", "application/x-mpegURL", "application/x-mpegURL"),
    ("text/vtt; charset=utf-8", "application/octet-stream", "text/vtt; charset=utf-8"),
    (None, "video/mp4", "video/mp4"),
    ("video/MP2T", "video/MP2T", "video/MP2T"),
])
def test_upstream_hls_type_overrides_guessed_segment_type(hint, upstream, expected):
    assert _upstream_content_type(hint, upstream) == expected
