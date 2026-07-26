from fractions import Fraction

from miduo.model import NoteEvent, PartInfo, Pitch, ScoreIR, TimeSignature
from miduo.slicing import beat_weight_at, build_harmonic_slices


def _score(*notes: NoteEvent, duration: Fraction = Fraction(4)) -> ScoreIR:
    return ScoreIR(
        title=None,
        parts=(PartInfo("P1", "Part"),),
        source_voices=(),
        notes=notes,
        time_signatures=(TimeSignature(Fraction(0), 4, 4),),
        duration=duration,
    )


def test_slices_follow_attacks_releases_and_silence():
    score = _score(
        NoteEvent(Pitch("C", 4), Fraction(1), Fraction(2), 1),
        NoteEvent(Pitch("E", 4), Fraction(2), Fraction(1, 2), 2),
    )
    slices = build_harmonic_slices(score)

    assert [(item.start, item.end) for item in slices] == [
        (Fraction(0), Fraction(1)),
        (Fraction(1), Fraction(2)),
        (Fraction(2), Fraction(5, 2)),
        (Fraction(5, 2), Fraction(3)),
        (Fraction(3), Fraction(4)),
    ]
    assert [len(item.active_notes) for item in slices] == [0, 1, 2, 1, 0]


def test_active_notes_are_ordered_low_to_high():
    score = _score(
        NoteEvent(Pitch("G", 4), Fraction(0), Fraction(1), 1),
        NoteEvent(Pitch("C", 4), Fraction(0), Fraction(1), 2),
    )
    first_slice = build_harmonic_slices(score)[0]
    assert [str(note.pitch) for note in first_slice.active_notes] == ["C4", "G4"]


def test_duplicate_notes_are_not_collapsed():
    duplicate = NoteEvent(Pitch("C", 4), Fraction(0), Fraction(1), 1)
    first_slice = build_harmonic_slices(_score(duplicate, duplicate))[0]
    assert len(first_slice.active_notes) == 2


def test_beat_weights():
    signatures = (TimeSignature(Fraction(0), 4, 4),)
    assert beat_weight_at(Fraction(0), signatures) == 1.0
    assert beat_weight_at(Fraction(3, 2), signatures) == 0.5
    assert beat_weight_at(Fraction(7, 4), signatures) == 0.25
