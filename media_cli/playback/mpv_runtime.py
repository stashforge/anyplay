"""Private runtime files used by mpv IPC sessions."""

import os
import shutil
import tempfile

TRANSITION_OSD_MS = 60 * 60 * 1000


def _candidate_user_input_confs():
    candidates = []
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config:
        candidates.append(os.path.join(xdg_config, "mpv", "input.conf"))
    home = os.path.expanduser("~")
    candidates.extend(
        [
            os.path.join(home, ".config", "mpv", "input.conf"),
            os.path.join(home, ".mpv", "input.conf"),
        ]
    )
    seen = set()
    unique = []
    for path in candidates:
        if path and path not in seen:
            seen.add(path)
            unique.append(path)
    return unique


def _read_user_input_conf():
    for path in _candidate_user_input_confs():
        try:
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    return path, fh.read()
        except OSError:
            continue
    return None, ""


def _write_mpv_input_config(config):
    user_path, user_text = _read_user_input_conf()
    if user_text:
        config.write("# ---- User mpv input.conf copied by AnyPlay ----\n")
        config.write(f"# Source: {user_path}\n")
        config.write(user_text)
        if not user_text.endswith("\n"):
            config.write("\n")
        config.write("\n")
    config.write("# ---- AnyPlay playback controls ----\n")
    config.write("SHIFT+RIGHT script-message next_ep\n")
    config.write("SHIFT+LEFT script-message prev_ep\n")
    config.write("TAB script-message skip_interval\n")


ANISKIP_LUA_SCRIPT = """
local utils = require 'mp.utils'
local intervals = {}
local auto_skip = true
local clear_chapters = false
local skipped = {}
local active_prompt = nil
local known_duration = 0
local mirror_prompt_serial = nil

local function clear_mirror_prompt()
    mirror_prompt_serial = nil
    mp.remove_key_binding('anyplay_next_mirror')
end

mp.register_script_message('set_mirror_prompt', function(serial, enabled)
    clear_mirror_prompt()
    if enabled == 'yes' then
        mirror_prompt_serial = serial
        mp.add_forced_key_binding('ENTER', 'anyplay_next_mirror', function()
            if mirror_prompt_serial then
                mp.commandv('script-message', 'next_mirror', mirror_prompt_serial)
            end
        end)
    end
end)
mp.register_event('start-file', clear_mirror_prompt)
mp.register_event('playback-restart', clear_mirror_prompt)
mp.register_event('end-file', clear_mirror_prompt)

local function format_time(sec)
    if not sec then return "00:00" end
    local m = math.floor(sec / 60)
    local s = math.floor(sec % 60)
    if m >= 60 then
        local h = math.floor(m / 60)
        m = m % 60
        return string.format("%02d:%02d:%02d", h, m, s)
    end
    return string.format("%02d:%02d", m, s)
end

local function on_time_pos(name, val)
    if val == nil or #intervals == 0 then return end
    local curr_time = val
    for i, item in ipairs(intervals) do
        local s_start = item.start
        local s_end = item["end"] or mp.get_property_number("duration", 0)
        local s_label = item.label or "Opening"
        if not s_end or s_end <= s_start then goto continue end
        if curr_time >= s_start and curr_time < (s_end - 0.5) then
            if not skipped[i] then
                if auto_skip then
                    skipped[i] = true
                    mp.set_property_number("time-pos", s_end)
                    mp.osd_message(string.format("%s skipped %s → %s", s_label, format_time(s_start), format_time(s_end)), 3)
                else
                    if active_prompt ~= i then
                        active_prompt = i
                        mp.osd_message(string.format("[Tab] Skip %s %s → %s", s_label, format_time(s_start), format_time(s_end)), math.max(1, math.floor(s_end - curr_time)))
                    end
                end
            end
        elseif curr_time >= (s_end - 0.5) then
            skipped[i] = true
            if active_prompt == i then active_prompt = nil end
        elseif curr_time < s_start then
            if active_prompt == i then active_prompt = nil end
        end
        ::continue::
    end
end

local function manual_skip()
    local curr_time = mp.get_property_number("time-pos", 0)
    for i, item in ipairs(intervals) do
        local s_start = item.start
        local s_end = item["end"] or mp.get_property_number("duration", 0)
        local s_label = item.label or "Opening"
        if not s_end or s_end <= s_start then goto continue end
        if curr_time >= s_start and curr_time < s_end then
            skipped[i] = true
            mp.set_property_number("time-pos", s_end)
            mp.osd_message(string.format("%s skipped %s → %s", s_label, format_time(s_start), format_time(s_end)), 3)
            active_prompt = nil
            break
        end
        ::continue::
    end
end

local function interval_end(item)
    local explicit_end = tonumber(item["end"])
    if explicit_end and explicit_end > 0 then
        return explicit_end
    end
    if known_duration and known_duration > 0 then
        return known_duration
    end
    return mp.get_property_number("duration", 0)
end

local function apply_chapters()
    if not intervals or #intervals == 0 then
        if clear_chapters then
            mp.set_property_native("chapter-list", {})
        end
        return
    end
    local chaps = {}
    local curr = 0.0
    for _, item in ipairs(intervals) do
        local s_start = math.max(0.0, tonumber(item.start) or 0.0)
        local s_end = math.max(s_start, interval_end(item) or 0.0)
        local s_label = item.label or "Skip"
        if s_end > s_start then
            if s_start > curr then
                local pre_title = (curr == 0.0 and (item.type == "op" or item.type == "mixed-op")) and "Intro" or "Episode"
                table.insert(chaps, { title = pre_title, time = curr })
            end
            table.insert(chaps, { title = s_label, time = s_start })
            table.insert(chaps, { title = "Episode", time = s_end })
            curr = s_end
        end
    end
    if #chaps > 0 then
        table.sort(chaps, function(a, b) return a.time < b.time end)
        mp.set_property_native("chapter-list", chaps)
    end
end

local function on_duration(name, val)
    known_duration = tonumber(val) or 0
    apply_chapters()
end

mp.observe_property("time-pos", "number", on_time_pos)
mp.observe_property("duration", "number", on_duration)
mp.add_key_binding("tab", "aniskip_tab", manual_skip)
mp.register_script_message("skip_interval", manual_skip)
mp.register_script_message("set_skip_intervals", function(json_str, auto_flag, chapter_mode)
    intervals = {}
    skipped = {}
    active_prompt = nil
    auto_skip = (auto_flag == "yes" or auto_flag == "true" or auto_flag == "1")
    clear_chapters = (chapter_mode == "clear")
    if json_str and json_str ~= "" then
        local parsed = utils.parse_json(json_str)
        if parsed and type(parsed) == "table" then
            intervals = parsed
        end
    end
    apply_chapters()
end)
mp.register_event("file-loaded", function()
    known_duration = mp.get_property_number("duration", 0)
    apply_chapters()
end)
"""


def create_mpv_runtime():
    runtime_dir = tempfile.mkdtemp(prefix="anyplay-")
    os.chmod(runtime_dir, 0o700)
    socket_path = os.path.join(runtime_dir, "mpv.sock")
    config_path = os.path.join(runtime_dir, "input.conf")
    descriptor = os.open(
        config_path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as config:
        _write_mpv_input_config(config)
    chapters_path = os.path.join(runtime_dir, "chapters.txt")
    with open(chapters_path, "w", encoding="utf-8") as chap:
        chap.write(";FFMETADATA1\n")
    os.chmod(chapters_path, 0o600)

    lua_path = os.path.join(runtime_dir, "aniskip.lua")
    with open(lua_path, "w", encoding="utf-8") as f:
        f.write(ANISKIP_LUA_SCRIPT)
    os.chmod(lua_path, 0o600)

    return runtime_dir, socket_path, config_path, chapters_path, lua_path


def cleanup_mpv_runtime(runtime_dir):
    if runtime_dir:
        shutil.rmtree(runtime_dir, ignore_errors=True)
