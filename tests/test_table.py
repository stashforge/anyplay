from media_cli.ui.table import Table,Column
from media_cli.core.terminal import strip_ansi,display_width
from media_cli.state.theme import MINT_SCREEN_THEME

ROWS=[dict(name='AnimePahe ✔',type='Anime',language='Sub'),
      dict(name='Movies',type='Movies / Shows',language='Sub / Dub')]
COLS=(Column('name','name',10,optional=False),Column('type','type',8),Column('language','language',7))


def test_headers_and_cells_share_column_boundaries():
    table=Table(COLS,ROWS,77)
    header=strip_ansi(table.render({},theme=MINT_SCREEN_THEME,header=True))
    for row in ROWS:
        rendered=strip_ansi(table.render(row,theme=MINT_SCREEN_THEME))
        assert rendered.index(row['type'],len(row['name']))==header.index('type')
        assert rendered.index(row['language'])==header.index('language')
    filtered=strip_ansi(table.render(ROWS[1],theme=MINT_SCREEN_THEME,query='movies'))
    assert filtered.index('Movies / Shows')==header.index('type')


def test_table_fits_small_widths_and_hides_optional_columns():
    for width in (1,12,20,28,40,80):
        table=Table(COLS,ROWS,width)
        assert display_width(table.render({},theme=MINT_SCREEN_THEME,header=True))<=width
        for row in ROWS:
            assert display_width(table.render(row,theme=MINT_SCREEN_THEME))<=width
    assert len(Table(COLS,ROWS,12).columns)==1
    assert len(Table(COLS,ROWS,80).columns)==3


def test_styled_cell_preserves_marker_and_column_roles():
    from media_cli.ui.styled_text import StyledText, TextRun
    from media_cli.ui.screen_chrome import role_prefix
    row = dict(name=StyledText((TextRun('AniPM', 'row_normal'), TextRun(' ✔', 'table.active'))),
               type='Anime', language='Sub / Dub')
    columns = (Column('name', 'name'), Column('type', 'type', role='table.secondary'),
               Column('language', 'language', role='table.secondary'))
    rendered = Table(columns, [row], 70).render(row, theme=MINT_SCREEN_THEME)
    assert role_prefix(MINT_SCREEN_THEME, 'table.active') + ' ✔' in rendered
    assert role_prefix(MINT_SCREEN_THEME, 'table.secondary') + 'Anime' in rendered
    assert role_prefix(MINT_SCREEN_THEME, 'table.secondary') + 'Sub / Dub' in rendered
    assert display_width(rendered) <= 70
    assert strip_ansi(rendered).startswith('AniPM ✔')
