import json
import shutil
import subprocess

import pytest

from media_cli.media.aniskip import generate_chapters_file
from media_cli.playback.mpv_runtime import ANISKIP_LUA_SCRIPT


@pytest.mark.parametrize("kind,expected", [("op", "Intro"), ("mixed-op", "Intro"),
                                           ("ed", "Episode"), ("mixed-ed", "Episode"), ("recap", "Episode")])
def test_exported_chapter_leadin_depends_on_skip_type(tmp_path, kind, expected):
    target = tmp_path / "chapters.txt"
    generate_chapters_file([{"type": kind, "label": "Skip", "start": 100, "end": 190}], str(target))
    content = target.read_text()
    assert f"START=0\nEND=100000\nTITLE={expected}\n" in content
    assert "START=100000\nEND=190000\nTITLE=Skip" in content


@pytest.mark.parametrize("kind,expected", [("op", "Intro"), ("mixed-op", "Intro"),
                                           ("ed", "Episode"), ("mixed-ed", "Episode"), ("recap", "Episode")])
def test_actual_lua_chapter_leadin_matches_exported_policy(kind, expected):
    lua = shutil.which("lua")
    if not lua:
        pytest.skip("Lua interpreter required")
    prelude = """
local handlers = {}
local fixture = {{ type = KIND, label = "Skip", start = 100, ["end"] = 190 }}
package.preload['mp.utils'] = function() return { parse_json = function() return fixture end } end
mp = {
 add_hook = function() end,
 observe_property = function() end,
 add_key_binding = function() end,
 register_event = function() end,
 register_script_message = function(name, fn) handlers[name] = fn end,
 set_property_native = function(name, chapters)
  if name == 'chapter-list' then
   for _, chapter in ipairs(chapters) do print(chapter.title .. ':' .. tostring(chapter.time)) end
  end
 end,
}
""".replace("KIND", json.dumps(kind))
    result = subprocess.run([lua, "-"], input=prelude + ANISKIP_LUA_SCRIPT +
                            '\nhandlers["set_skip_intervals"]("fixture", "no", "keep")\n',
                            text=True, capture_output=True, check=True, timeout=3)
    assert result.stdout.splitlines()[0].split(":")[0] == expected
    assert result.stdout.splitlines()[1] == "Skip:100"
