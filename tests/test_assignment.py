from fractions import Fraction

from miduo.assignment import assign_voices
from miduo.model import (
    ChordLabel,
    ChordQuality,
    HarmonicSlice,
    NoteEvent,
    PartInfo,
    Pitch,
    PitchClass,
    ScoreIR,
    SourceVoice,
)


def _score(notes: tuple[NoteEvent, ...], duration: int = 1) -> ScoreIR:
    return ScoreIR(
        title=None,
        parts=(PartInfo("P1", "Melody"), PartInfo("P2", "Accompaniment")),
        source_voices=(
            SourceVoice(1, "P1", "1", None),
            SourceVoice(2, "P2", "1", None),
            SourceVoice(3, "P2", "2", None),
        ),
        notes=notes,
        time_signatures=(),
        duration=Fraction(duration),
    )


def _c_major_slice(
    start: int,
    end: int,
    notes: tuple[NoteEvent, ...],
) -> HarmonicSlice:
    root = PitchClass("C")
    return HarmonicSlice(
        start=Fraction(start),
        end=Fraction(end),
        active_notes=notes,
        chord=ChordLabel(
            root=root,
            quality=ChordQuality.MAJOR,
            tensions_present=frozenset(),
            bass=root,
            confidence=1.0,
        ),
        beat_weight=1.0,
    )


def test_assigns_melody_to_violin1_and_characteristic_tone_to_violin2():
    notes = (
        NoteEvent(Pitch("C", 4), Fraction(0), Fraction(1), 2),
        NoteEvent(Pitch("E", 4), Fraction(0), Fraction(1), 3),
        NoteEvent(Pitch("G", 4), Fraction(0), Fraction(1), 1),
    )
    result = assign_voices(_score(notes), (_c_major_slice(0, 1, notes),), ())
    assignment = result.slices[0]
    assert str(assignment.violin1_pitch) == "G4"
    assert str(assignment.violin2_pitch) == "E4"


def test_transposes_support_pitch_into_violin_range():
    notes = (
        NoteEvent(Pitch("C", 2), Fraction(0), Fraction(1), 2),
        NoteEvent(Pitch("E", 4), Fraction(0), Fraction(1), 1),
    )
    result = assign_voices(_score(notes), (_c_major_slice(0, 1, notes),), ())
    violin2 = result.slices[0].violin2_pitch
    assert violin2 is not None
    assert violin2.midi_number >= 55


def test_merges_same_pitch_across_adjacent_slices():
    notes = (
        NoteEvent(Pitch("C", 4), Fraction(0), Fraction(2), 2),
        NoteEvent(Pitch("E", 4), Fraction(0), Fraction(2), 3),
        NoteEvent(Pitch("G", 4), Fraction(0), Fraction(2), 1),
    )
    slices = (
        _c_major_slice(0, 1, notes),
        _c_major_slice(1, 2, notes),
    )
    result = assign_voices(_score(notes, duration=2), slices, ())
    assert len(result.notes) == 2
    assert {note.duration for note in result.notes} == {Fraction(2)}


def test_assignment_avoids_voice_crossing():
    notes = (
        NoteEvent(Pitch("C", 4), Fraction(0), Fraction(1), 2),
        NoteEvent(Pitch("E", 4), Fraction(0), Fraction(1), 3),
        NoteEvent(Pitch("G", 4), Fraction(0), Fraction(1), 1),
    )
    result = assign_voices(_score(notes), (_c_major_slice(0, 1, notes),), ())
    assignment = result.slices[0]
    assert assignment.violin2_pitch is not None
    assert assignment.violin1_pitch is not None
    assert assignment.violin1_pitch.midi_number > assignment.violin2_pitch.midi_number


def test_global_assignment_keeps_common_tone_for_future_slice():
    first_notes = (
        NoteEvent(Pitch("D", 4), Fraction(0), Fraction(1), 2),
        NoteEvent(Pitch("C", 4), Fraction(0), Fraction(2), 3),
        NoteEvent(Pitch("G", 4), Fraction(0), Fraction(2), 1),
    )
    second_notes = (first_notes[1], first_notes[2])
    score = _score(first_notes, duration=2)
    slices = (
        HarmonicSlice(
            start=Fraction(0),
            end=Fraction(1),
            active_notes=first_notes,
            chord=None,
            beat_weight=1.0,
        ),
        HarmonicSlice(
            start=Fraction(1),
            end=Fraction(2),
            active_notes=second_notes,
            chord=None,
            beat_weight=1.0,
        ),
    )
    result = assign_voices(score, slices, ())
    assert [str(item.violin2_pitch) for item in result.slices] == ["C4", "C4"]
