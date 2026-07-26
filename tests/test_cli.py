from pathlib import Path

from miduo.cli import main

FIXTURE = Path(__file__).parent / "fixtures" / "minimal.musicxml"


def test_inspect(capsys):
    assert main(["inspect", str(FIXTURE)]) == 0
    output = capsys.readouterr().out
    assert "Title: Fixture" in output
    assert "Parts: Piano" in output
    assert "Notes: 1" in output


def test_arrange_dry_run(capsys, tmp_path):
    output_path = tmp_path / "duet.musicxml"
    assert main(["arrange", str(FIXTURE), "-o", str(output_path), "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "Parsed note events: 1" in output
    assert "Harmonic slices: 1" in output
    assert "parse -> analyze-harmony" in output
    assert not output_path.exists()


def test_parse(capsys):
    assert main(["parse", str(FIXTURE), "--json"]) == 0
    output = capsys.readouterr().out
    assert '"note_event_count": 1' in output
    assert '"duration_quarter_notes": "4"' in output


def test_slice(capsys):
    assert main(["slice", str(FIXTURE), "--json"]) == 0
    output = capsys.readouterr().out
    assert '"slice_count": 1' in output
    assert '"max_active_notes": 1' in output


def test_analyze(capsys):
    assert main(["analyze", str(FIXTURE), "--json"]) == 0
    output = capsys.readouterr().out
    assert '"slice_count": 1' in output
    assert '"labeled_slice_count": 1' in output


def test_analyze_with_music21_backend(capsys):
    assert (
        main(
            [
                "analyze",
                str(FIXTURE),
                "--harmony-backend",
                "music21",
                "--json",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert '"backend": "music21"' in output
    assert '"roman_numeral_histogram"' in output


def test_analyze_rejects_invalid_confidence(capsys):
    try:
        main(["analyze", str(FIXTURE), "--confidence-threshold", "1.5"])
    except SystemExit as error:
        assert error.code == 2
    assert "must be between 0 and 1" in capsys.readouterr().err


def test_spans(capsys):
    assert main(["spans", str(FIXTURE), "--json"]) == 0
    output = capsys.readouterr().out
    assert '"span_count": 1' in output
    assert '"plain_sustain": 1' in output


def test_assign(capsys):
    assert main(["assign", str(FIXTURE), "--json"]) == 0
    output = capsys.readouterr().out
    assert '"slice_assignment_count": 1' in output
    assert '"violin1_note_count": 1' in output
    assert '"violin2_leaps"' in output


def test_reduce(capsys):
    assert main(["reduce", str(FIXTURE), "--json"]) == 0
    output = capsys.readouterr().out
    assert '"removed_attack_count": 0' in output
    assert '"violin2_note_count_after": 0' in output


def test_validate(capsys):
    assert main(["validate", str(FIXTURE), "--json"]) == 0
    output = capsys.readouterr().out
    assert '"valid": true' in output
    assert '"issue_count": 0' in output


def test_arrange_writes_output(capsys, tmp_path):
    output_path = tmp_path / "duet.musicxml"
    assert main(["arrange", str(FIXTURE), "-o", str(output_path)]) == 0
    assert output_path.is_file()
    captured = capsys.readouterr()
    assert "Wrote:" in captured.out
    assert "[parse]" in captured.err
    assert "[assign-voices]" in captured.err


def test_arrange_quiet_suppresses_progress(capsys, tmp_path):
    output_path = tmp_path / "quiet-duet.musicxml"
    assert (
        main(
            [
                "arrange",
                str(FIXTURE),
                "-o",
                str(output_path),
                "--quiet",
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    assert "Wrote:" in captured.out
    assert captured.err == ""


def test_missing_input_is_a_cli_error(capsys):
    assert main(["inspect", "missing.musicxml"]) == 2
    assert "does not exist" in capsys.readouterr().err


def test_musescore_option_is_forwarded(monkeypatch, capsys, tmp_path):
    executable = tmp_path / "mscore"
    executable.touch(mode=0o755)
    mscz_path = tmp_path / "score.mscz"
    mscz_path.touch()

    def fake_convert(self, input_path, output_path):
        output_path.write_bytes(FIXTURE.read_bytes())

    monkeypatch.setattr("miduo.musescore.MuseScoreConverter.convert_to_musicxml", fake_convert)

    assert main(["inspect", str(mscz_path), "--musescore", str(executable)]) == 0
    assert "Format: mscz" in capsys.readouterr().out
