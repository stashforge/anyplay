from media_cli.ui.metadata_styles import legacy_metadata, metadata_content


def test_groups_and_quiet_labels_shared_with_legacy():
    legacy = legacy_metadata('FINISHED • TV • SUB • Watched 2/12 • Avail 4 • 2026 • ★ 8.2')
    typed = metadata_content([
        ('FINISHED', 'metadata.status'), (' • ', 'metadata.separator'),
        ('TV', 'details'), (' • ', 'metadata.separator'), ('SUB', 'details'),
        (' • ', 'metadata.separator'), ('Watched', 'muted'), (' 2/12', 'details'),
        (' • ', 'metadata.separator'), ('Avail 4', 'details'),
        (' • ', 'metadata.separator'), ('2026', 'details'),
        (' • ', 'metadata.separator'), ('★ 8.2', 'details')])
    for content in (legacy, typed):
        assert ''.join(run.text for run in content.runs) == 'FINISHED  TV  SUB • Watched 2/12  Avail 4 • 2026  ★ 8.2'
        labels = [run for run in content.runs if run.role == 'library.label']
        assert {run.text.strip() for run in labels} == {'Watched', 'Avail'}
