"""Native image packets must never pass through text-line sanitization."""

import pytest

from media_cli.ui.covers import poster_symbol_lines, poster_uses_native_protocol


def test_compact_variant_is_rendered_at_five_rows_without_changing_player_cover(monkeypatch):
    from types import SimpleNamespace
    from media_cli.ui import covers
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout='FULL' if len(calls) == 1 else 'SMALL')
    monkeypatch.setattr(covers.subprocess, 'run', run)
    monkeypatch.setattr(covers, '_compact_covers', {})
    result = covers.render_cover_variants('/tmp/cover.jpg')
    assert result.stdout == 'FULL'
    assert covers.compact_cover('FULL') == 'SMALL'
    assert '--size=12x8' in calls[0]
    assert '--size=8x5' in calls[1]


@pytest.mark.parametrize('shape,width', [('square', 10)])
def test_info_cover_shapes_center_crop_without_changing_original(monkeypatch, shape, width):
    from types import SimpleNamespace
    from media_cli.ui import covers
    monkeypatch.setattr(covers, '_info_cover_shape', shape)
    monkeypatch.setattr(covers, '_compact_covers', {})
    monkeypatch.setattr(covers.shutil, 'which', lambda name: '/usr/bin/magick' if name == 'magick' else None)
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout='FULL' if len(calls) == 1 else 'CROPPED')
    monkeypatch.setattr(covers.subprocess, 'run', run)
    assert covers.render_cover_variants('/tmp/cover.jpg').stdout == 'FULL'
    assert '--size=12x8' in calls[0]
    assert calls[1][:3] == ['magick', '/tmp/cover.jpg', '-resize']
    assert '-gravity' in calls[1] and 'center' in calls[1]
    assert f'--size={width}x5' in calls[2]
    assert covers.info_cover_size() == (width, 5)
    assert covers.compact_cover('FULL') == 'CROPPED'


def test_placeholder_renders_once_per_session_shape(monkeypatch):
    from types import SimpleNamespace
    from media_cli.ui import covers
    calls = []
    monkeypatch.setattr(covers, '_info_cover_shape', 'portrait')
    monkeypatch.setattr(covers, '_placeholder_renders', {})
    monkeypatch.setattr(covers, '_placeholder_pending', set())
    def render(path, **kwargs):
        calls.append(path)
        assert path.endswith('assets/no-cover.png')
        return SimpleNamespace(returncode=0, stdout='PLACEHOLDER')
    monkeypatch.setattr(covers, '_render_compact', render)
    monkeypatch.setattr(covers.threading, 'Thread', lambda target, **kwargs: SimpleNamespace(start=target))
    assert covers.placeholder_cover() == ''
    assert covers.placeholder_cover() == 'PLACEHOLDER'
    assert covers.placeholder_cover() == 'PLACEHOLDER'
    assert len(calls) == 1


def test_landscape_is_no_longer_an_available_shape(monkeypatch):
    from media_cli.ui import covers
    monkeypatch.setattr(covers, '_info_cover_shape', 'portrait')
    covers.configure_info_cover('landscape')
    assert covers.info_cover_size() == (8, 5)


def test_rendered_cover_is_reused_across_view_managers(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from collections import OrderedDict
    from media_cli.ui import covers
    image = tmp_path / 'cached.jpg'
    image.write_bytes(b'cached image')
    monkeypatch.setattr(covers, '_rendered_covers', OrderedDict())
    monkeypatch.setattr(covers, '_compact_covers', {})
    monkeypatch.setattr(covers, '_info_cover_shape', 'portrait')
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout='FULL' if len(calls) == 1 else 'COMPACT')
    monkeypatch.setattr(covers.subprocess, 'run', run)
    first = covers.render_cover_variants(str(image))
    assert covers.render_cover_variants(str(image)) is first
    assert len(calls) == 2  # full + compact once, no second rendering


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
