from pathlib import Path
from subprocess import CompletedProcess

import pytest

from miduo.errors import MuseScoreError
from miduo.musescore import MuseScoreConverter


def test_conversion_invokes_musescore(monkeypatch, tmp_path):
    source = tmp_path / "source.mscz"
    source.touch()
    destination = tmp_path / "destination.musicxml"

    def fake_run(command, **kwargs):
        assert command == ["/opt/mscore", "-o", str(destination), str(source)]
        assert kwargs["timeout"] == 120
        destination.write_text("<score-partwise/>")
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("miduo.musescore.subprocess.run", fake_run)

    MuseScoreConverter(Path("/opt/mscore")).convert_to_musicxml(source, destination)
    assert destination.is_file()


def test_conversion_reports_musescore_failure(monkeypatch, tmp_path):
    def fake_run(command, **kwargs):
        return CompletedProcess(command, 1, "", "cannot read score")

    monkeypatch.setattr("miduo.musescore.subprocess.run", fake_run)

    converter = MuseScoreConverter(Path("/opt/mscore"))
    with pytest.raises(MuseScoreError, match="cannot read score"):
        converter.convert_to_musicxml(tmp_path / "source.mscz", tmp_path / "output.musicxml")


def test_generic_conversion_can_request_force_flag(monkeypatch, tmp_path):
    source = tmp_path / "source.musicxml"
    destination = tmp_path / "destination.mscz"

    def fake_run(command, **kwargs):
        assert command == [
            "/opt/mscore",
            "-f",
            "-o",
            str(destination),
            str(source),
        ]
        destination.touch()
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("miduo.musescore.subprocess.run", fake_run)
    MuseScoreConverter(Path("/opt/mscore")).convert(source, destination, force=True)
