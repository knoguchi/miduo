from fractions import Fraction
from xml.etree import ElementTree

from miduo.io import inspect_score
from miduo.model import (
    AssignedNote,
    AssignedOrigin,
    AssignedVoice,
    AssignmentResult,
    KeySignature,
    PartInfo,
    Pitch,
    ScoreIR,
    TimeSignature,
)
from miduo.writer import (
    build_musicxml,
    quantize_assignment_for_notation,
    write_arrangement,
)


def _score() -> ScoreIR:
    return ScoreIR(
        title="Writer Fixture",
        parts=(PartInfo("source", "Source"),),
        source_voices=(),
        notes=(),
        time_signatures=(TimeSignature(Fraction(0), 4, 4),),
        duration=Fraction(8),
        key_signatures=(KeySignature(Fraction(0), -2, "major"),),
    )


def _assignment() -> AssignmentResult:
    return AssignmentResult(
        slices=(),
        notes=(
            AssignedNote(
                voice=AssignedVoice.VIOLIN_1,
                pitch=Pitch("B", 4, Fraction(-1)),
                onset=Fraction(0),
                duration=Fraction(6),
                origin=AssignedOrigin.MELODY,
                cost_breakdown={},
            ),
            AssignedNote(
                voice=AssignedVoice.VIOLIN_2,
                pitch=Pitch("G", 4),
                onset=Fraction(1),
                duration=Fraction(2),
                origin=AssignedOrigin.BASS_SELECTION,
                cost_breakdown={},
            ),
        ),
    )


def test_builds_two_part_musicxml_with_ties_and_rests():
    root = ElementTree.fromstring(build_musicxml(_score(), _assignment()))
    parts = root.findall("part")
    assert [part.attrib["id"] for part in parts] == ["P1", "P2"]
    assert len(parts[0].findall("measure")) == 2
    assert root.findtext("part/measure/attributes/key/fifths") == "-2"
    assert parts[0].find(".//type") is not None
    assert parts[0].find(".//tie[@type='start']") is not None
    assert parts[0].find(".//tie[@type='stop']") is not None
    assert parts[1].find(".//rest") is not None


def test_writes_inspectable_musicxml(tmp_path):
    output = tmp_path / "duet.musicxml"
    write_arrangement(_score(), _assignment(), output)
    summary = inspect_score(output)
    assert summary.part_names == ("Violin 1", "Violin 2")
    assert summary.measure_count == 4
    assert summary.note_count >= 3


def test_writes_inspectable_mxl(tmp_path):
    output = tmp_path / "duet.mxl"
    write_arrangement(_score(), _assignment(), output)
    summary = inspect_score(output)
    assert summary.format.value == "mxl"
    assert summary.part_names == ("Violin 1", "Violin 2")


def test_quantizes_irregular_durations_to_sixteenth_grid():
    assignment = AssignmentResult(
        slices=(),
        notes=(
            AssignedNote(
                voice=AssignedVoice.VIOLIN_1,
                pitch=Pitch("C", 5),
                onset=Fraction(1, 6),
                duration=Fraction(7, 6),
                origin=AssignedOrigin.MELODY,
                cost_breakdown={},
            ),
        ),
    )
    result = quantize_assignment_for_notation(_score(), assignment)
    assert result.notes[0].onset == Fraction(1, 4)
    assert result.notes[0].duration == Fraction(1)


def test_generated_musicxml_needs_no_tuplet_notation():
    root = ElementTree.fromstring(build_musicxml(_score(), _assignment()))
    assert root.find(".//time-modification") is None
