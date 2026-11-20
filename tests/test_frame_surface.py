"""Native image preservation across shared/legacy lists and loading overlays."""
from media_cli.ui import frame_surface, terminal_images
from media_cli.ui.list_screen import ScreenFrame


def frame(text='Title', image='IMAGE'):
    lines = [''] * 30
    lines[20] = '         ' + text
    return ScreenFrame(lines, image, 21, 0, 8, 5)


def test_metadata_repaint_does_not_clear_or_resend_cover():
    terminal_images.clear_now()
    assert 'IMAGE' in frame_surface.compose(frame(), 30, 80)
    changed = frame_surface.compose(frame('Other metadata'), 30, 80)
    assert 'IMAGE' not in changed
    assert '\033[21;1H\033[2K' not in changed
    assert '\033[21;9H\033[K' in changed
    assert 'Other metadata' in changed


def test_footer_loading_and_screen_return_preserve_cover():
    terminal_images.clear_now()
    frame_surface.compose(frame(), 30, 80)
    frame_surface.invalidate_row(30)
    result = frame_surface.compose(frame(), 30, 80)
    assert 'IMAGE' not in result
    assert '\033[30;1H\033[2K' in result
    assert '\033[21;' not in result


def test_external_clear_and_new_cover_repaint():
    terminal_images.clear_now()
    frame_surface.compose(frame(), 30, 80)
    terminal_images.clear_now()
    assert 'IMAGE' in frame_surface.compose(frame(), 30, 80)
    assert 'NEW' in frame_surface.compose(frame(image='NEW'), 30, 80)


def test_screen_handoff_repaints_text_without_resending_image():
    terminal_images.clear_now()
    frame_surface.compose(frame(), 30, 80)
    frame_surface.invalidate_text()
    result = frame_surface.compose(frame(), 30, 80)
    assert 'Title' in result
    assert 'IMAGE' not in result
    assert '\033[21;1H\033[2K' not in result
