"""Private runtime files used by mpv IPC sessions."""

import os
import shutil
import tempfile

TRANSITION_OSD_MS = 60 * 60 * 1000


ANISKIP_LUA_SCRIPT = """
local utils = require 'mp.utils'
local intervals = {}
local auto_skip = true
local skipped = {}
local active_prompt = nil

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
        local s_end = item["end"]
        local s_label = item.label or "Opening"
        if curr_time >= s_start and curr_time < (s_end - 0.5) then
            if not skipped[i] then
                if auto_skip then
                    skipped[i] = true
                    mp.set_property_number("time-pos", s_end)
                    mp.osd_message(string.format("Skipped %s (%s → %s)", s_label, format_time(s_start), format_time(s_end)), 3)
                else
                    if active_prompt ~= i then
                        active_prompt = i
                        mp.osd_message(string.format("[Tab/s] Skip %s (%s → %s)", s_label, format_time(s_start), format_time(s_end)), math.max(1, math.floor(s_end - curr_time)))
                    end
                end
            end
        elseif curr_time >= (s_end - 0.5) then
            skipped[i] = true
            if active_prompt == i then active_prompt = nil end
        elseif curr_time < s_start then
            if active_prompt == i then active_prompt = nil end
        end
    end
end

local function manual_skip()
    local curr_time = mp.get_property_number("time-pos", 0)
    for i, item in ipairs(intervals) do
        local s_start = item.start
        local s_end = item["end"]
        local s_label = item.label or "Opening"
        if curr_time >= s_start and curr_time < s_end then
            skipped[i] = true
            mp.set_property_number("time-pos", s_end)
            mp.osd_message(string.format("Skipped %s (%s → %s)", s_label, format_time(s_start), format_time(s_end)), 3)
            active_prompt = nil
            break
        end
    end
end

local function apply_chapters()
    if not intervals or #intervals == 0 then return end
    local chaps = {}
    local curr = 0.0
    for _, item in ipairs(intervals) do
        local s_start = math.max(0.0, tonumber(item.start) or 0.0)
        local s_end = math.max(s_start, tonumber(item["end"]) or 0.0)
        local s_label = item.label or "Skip"
        if s_end > s_start then
            if s_start > curr then
                local pre_title = (curr == 0.0) and "Intro" or "Episode"
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

mp.observe_property("time-pos", "number", on_time_pos)
mp.add_key_binding("tab", "aniskip_tab", manual_skip)
mp.add_key_binding("s", "aniskip_s", manual_skip)
mp.register_script_message("skip_interval", manual_skip)
mp.register_script_message("set_skip_intervals", function(json_str, auto_flag)
    intervals = {}
    skipped = {}
    active_prompt = nil
    auto_skip = (auto_flag == "yes" or auto_flag == "true" or auto_flag == "1")
    if json_str and json_str ~= "" then
        local parsed = utils.parse_json(json_str)
        if parsed and type(parsed) == "table" then
            intervals = parsed
        end
    end
    apply_chapters()
end)
mp.register_event("file-loaded", function()
    apply_chapters()
end)
"""


def create_mpv_runtime():
    runtime_dir = tempfile.mkdtemp(prefix="allmanga-cli-")
    os.chmod(runtime_dir, 0o700)
    socket_path = os.path.join(runtime_dir, "mpv.sock")
    config_path = os.path.join(runtime_dir, "input.conf")
    descriptor = os.open(
        config_path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as config:
        config.write("SHIFT+RIGHT script-message next_ep\n")
        config.write("SHIFT+LEFT script-message prev_ep\n")
        config.write("TAB script-message skip_interval\n")
        config.write("s script-message skip_interval\n")
        config.write("n script-message next_mirror\n")
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
