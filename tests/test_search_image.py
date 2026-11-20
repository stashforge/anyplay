from types import SimpleNamespace

import pytest

from media_cli.ui import search_image
from media_cli.ui.search_home import SearchHomeState, search_home_lines
from media_cli.core.terminal import display_width, strip_ansi


@pytest.mark.parametrize("requested,expected", [(1, 8), (200, 30), (15, 15), ("invalid", 15)])
def test_size_clamping_and_render_reuse(tmp_path, monkeypatch, requested, expected):
    image = tmp_path / "image.png"
    image.write_bytes(b"test")
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(stdout="IMAGE\n")
    monkeypatch.setattr(search_image.subprocess, "run", run)
    search_image._render.cache_clear()
    config = {"search_image_path": str(image), "search_image_size": requested}
    assert search_image.search_image_lines(config, 90, 42) == ("IMAGE",)
    assert search_image.search_image_lines(config, 90, 42) == ("IMAGE",)
    assert len(calls) == 1
    assert f"--size={expected}x8" in calls[0]


def test_off_missing_and_small_screen_do_not_render(tmp_path, monkeypatch):
    monkeypatch.setattr(search_image.subprocess, "run", lambda *a, **kw: pytest.fail("Unexpected renderer"))
    assert search_image.search_image_lines({"search_image": "off"}, 90, 42) == ()
    assert search_image.search_image_lines({"search_image_path": str(tmp_path / 'missing')}, 90, 42) == ()
    assert search_image.search_image_lines({}, 25, 20) == ()


def test_image_is_centered_above_brand_and_layout_stays_bounded():
    lines = search_home_lines(SearchHomeState(), "AniPM", 80, 40, image=("IMAGE",))
    plain = list(map(strip_ansi, lines))
    assert next(i for i, line in enumerate(plain) if "IMAGE" in line) < next(i for i, line in enumerate(plain) if "█" in line)
    assert all(display_width(line) < 80 for line in lines)
    assert any("History" in line for line in plain)


def test_default_data_path_and_missing_chafa_are_safe(tmp_path, monkeypatch):
    image = tmp_path / 'anyplay' / 'search-image.png'
    image.parent.mkdir()
    image.write_bytes(b'test')
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path))
    def missing(*args, **kwargs):
        raise FileNotFoundError('chafa')
    monkeypatch.setattr(search_image.subprocess, 'run', missing)
    search_image._render.cache_clear()
    assert search_image.search_image_lines({}, 90, 42) == ()


def test_native_image_is_preserved_and_centered_as_overlay(tmp_path, monkeypatch):
    image = tmp_path / 'native.png'
    image.write_bytes(b'test')
    native = '\033P0;1qDATA\nMORE\033\\'
    def run(command, **kwargs):
        assert '--format=symbols' not in command
        return SimpleNamespace(stdout=native)
    monkeypatch.setattr(search_image.subprocess, 'run', run)
    search_image._render.cache_clear()
    rendered = search_image.search_image_lines({'search_image_path': str(image)}, 80, 40)
    assert rendered[0] == native and len(rendered) == 8
    frame = search_home_lines(SearchHomeState(), 'AniPM', 80, 40, image=rendered)
    assert frame.poster == native
    assert frame.poster_column == (80 - 15) // 2
    brand_row = next(i for i, line in enumerate(frame.lines) if "█" in strip_ansi(line))
    assert brand_row - (frame.poster_row - 1 + len(rendered)) == 1
    assert not strip_ansi(frame.lines[brand_row - 1]).strip()
    assert not strip_ansi(frame.lines[brand_row + 2]).strip()
    assert all(native not in line for line in frame.lines)
    assert all(display_width(line) < 80 for line in frame.lines)
