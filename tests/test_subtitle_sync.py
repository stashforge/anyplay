from allmanga_cli.media.local_proxy import _ensure_vtt, _shift_vtt_timestamps


def test_ensure_vtt_injects_xtimestampmap_for_standard_players():
    raw_vtt = b"WEBVTT\n\n00:00:15.210 --> 00:00:15.910\nGene!\n"
    res = _ensure_vtt(raw_vtt, mpegts_pts=133508, client_ua="ExoPlayerLib/2.19.1").decode("utf-8")
    assert "X-TIMESTAMP-MAP=MPEGTS:133508,LOCAL:00:00:00.000" in res
    assert "00:00:15.210 --> 00:00:15.910" in res


def test_ensure_vtt_shifts_timestamps_for_mpv():
    raw_vtt = b"WEBVTT\n\n00:00:15.210 --> 00:00:15.910\nGene!\n"
    res = _ensure_vtt(raw_vtt, mpegts_pts=133508, client_ua="libmpv").decode("utf-8")
    # 133508 / 90000 = ~1.483s
    # 15.210 + 1.4834 = 16.693
    assert "00:00:16.693 --> 00:00:17.393" in res
    assert "Gene!" in res


def test_ensure_vtt_srt_conversion_with_timestamp_map():
    raw_srt = b"1\n00:00:10,000 --> 00:00:12,000\nHello world\n"
    res = _ensure_vtt(raw_srt, mpegts_pts=133508, client_ua="VLC/3.5.4").decode("utf-8")
    assert "X-TIMESTAMP-MAP=MPEGTS:133508,LOCAL:00:00:00.000" in res
    assert "00:00:10.000 --> 00:00:12.000" in res


def test_ensure_vtt_no_offset_when_mpegts_pts_none():
    raw_vtt = b"WEBVTT\n\n00:00:05.000 --> 00:00:08.000\nNo shift\n"
    res = _ensure_vtt(raw_vtt, mpegts_pts=None, client_ua="ExoPlayer").decode("utf-8")
    assert "X-TIMESTAMP-MAP" not in res
    assert "00:00:05.000 --> 00:00:08.000" in res


def test_shift_vtt_timestamps_handles_minutes_hours():
    text = "01:59:59.000 --> 02:00:01.000 line:92%,end\nCue text"
    shifted = _shift_vtt_timestamps(text, 2.0)
    assert "02:00:01.000 --> 02:00:03.000 line:92%,end" in shifted


def test_ensure_vtt_vlc_skips_line_padding_for_multiline():
    raw_vtt = b"WEBVTT\n\n00:00:23.180 --> 00:00:26.770\nThat speed...\nIs that Scarlet, the Red Meteor?!\n"
    res = _ensure_vtt(raw_vtt, mpegts_pts=133508, target_player="vlc").decode("utf-8")
    assert "X-TIMESTAMP-MAP=MPEGTS:133508,LOCAL:00:00:00.000" in res
    # Must NOT contain line:92%,end so VLC's multi-line layout works properly
    assert "line:92%,end" not in res
    assert "line:83%" in res
    assert "00:00:23.180 --> 00:00:26.770" in res


def test_ensure_vtt_vlc_single_line_padding():
    raw_vtt = b"WEBVTT\n\n00:00:23.180 --> 00:00:26.770\nSingle line text\n"
    res = _ensure_vtt(raw_vtt, mpegts_pts=133508, target_player="vlc").decode("utf-8")
    assert "line:88%" in res


