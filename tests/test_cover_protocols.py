"""Native image packets must never pass through text-line sanitization."""

import pytest

from media_cli.ui.covers import poster_symbol_lines, poster_uses_native_protocol


@pytest.mark.parametrize(
    "packet",
    (
        "\033Pq#0;2;100;0;0~\033\\",
        "\033P0;1;0q#0;2;100;0;0~\033\\",
        "\033P;1q#0;2;100;0;0~\033\\",
        "\033_Ga=T,f=24,s=1,v=1,t=d;/wAA\033\\",
        "\033]1337;File=inline=1:AAAA\a",
    ),
)
def test_native_cover_protocol_is_not_split_into_symbol_lines(packet):
    assert poster_uses_native_protocol(packet)
    assert poster_symbol_lines(packet, 8, 100) == []


@pytest.mark.parametrize("text", ("", "plain image", "\033[31mcolored symbols\033[0m", "\033P0;1pnot-sixel\033\\"))
def test_other_content_is_not_misclassified_as_native_cover(text):
    assert not poster_uses_native_protocol(text)
