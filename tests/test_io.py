from pathlib import Path

import pytest

from miduo.errors import MuseScoreError, UnsupportedFormatError
from miduo.io import ScoreFormat, detect_format, inspect_score
from miduo.musescore import MuseScoreConverter

FIXTURE = Path(__file__).parent / "fixtures" / "minimal.musicxml"


def test_inspect_musicxml():
    summary = inspect_score(FIXTURE)
    assert summary.format is ScoreFormat.MUSICXML
    assert summary.title == "Fixture"
    assert summary.part_names == ("Piano",)
    assert summary.measure_count == 1
    assert summary.note_count == 1


def test_reject_unknown_extension():
    with pytest.raises(UnsupportedFormatError):
        detect_format(Path("score.mid"))


def test_inspect_musescore_via_converter(monkeypatch, tmp_path):
    executable = tmp_path / "mscore"
    executable.touch(mode=0o755)
    source = tmp_path / "score.mscz"
    source.touch()

    def fake_convert(self, input_path, output_path):
        assert input_path == source
        output_path.write_bytes(FIXTURE.read_bytes())

    monkeypatch.setattr(MuseScoreConverter, "convert_to_musicxml", fake_convert)

    summary = inspect_score(source, musescore_executable=executable)
    assert summary.path == source
    assert summary.format is ScoreFormat.MUSESCORE
    assert summary.title == "Fixture"


def test_explicit_musescore_executable_must_be_runnable(tmp_path):
    with pytest.raises(MuseScoreError, match="not runnable"):
        MuseScoreConverter.discover(tmp_path / "missing-mscore")
