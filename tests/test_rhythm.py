from fractions import Fraction

from miduo.model import (
    AssignedNote,
    AssignedOrigin,
    AssignedVoice,
    AssignmentResult,
    HarmonicSlice,
    PartInfo,
    Pitch,
    ScoreIR,
    TimeSignature,
)
from miduo.rhythm import reduce_violin2_rhythm


def _score() -> ScoreIR:
    return ScoreIR(
        title=None,
        parts=(PartInfo("P1", "Part"),),
        source_voices=(),
        notes=(),
        time_signatures=(TimeSignature(Fraction(0), 4, 4),),
        duration=Fraction(1),
    )


def _note(
    voice: AssignedVoice,
    pitch: Pitch,
    onset: Fraction,
    duration: Fraction,
    origin: AssignedOrigin = AssignedOrigin.BASS_SELECTION,
) -> AssignedNote:
    return AssignedNote(
        voice=voice,
        pitch=pitch,
        onset=onset,
        duration=duration,
        origin=origin,
        cost_breakdown={},
    )


def _slices() -> tuple[HarmonicSlice, ...]:
    return tuple(
        HarmonicSlice(
            start=onset,
            end=onset + Fraction(1, 4),
            active_notes=(),
            chord=None,
            beat_weight=weight,
        )
        for onset, weight in (
            (Fraction(0), 1.0),
            (Fraction(1, 4), 0.25),
            (Fraction(1, 2), 0.5),
            (Fraction(3, 4), 0.25),
        )
    )


def _dense_assignment(*, tension_at_half: bool = False) -> AssignmentResult:
    notes = [
        _note(
            AssignedVoice.VIOLIN_1,
            Pitch("G", 4),
            Fraction(0),
            Fraction(1),
            AssignedOrigin.MELODY,
        )
    ]
    for index, onset in enumerate(
        (Fraction(0), Fraction(1, 4), Fraction(1, 2), Fraction(3, 4))
    ):
        origin = (
            AssignedOrigin.TENSION_SELECTION
            if tension_at_half and onset == Fraction(1, 2)
            else AssignedOrigin.BASS_SELECTION
        )
        notes.append(
            _note(
                AssignedVoice.VIOLIN_2,
                Pitch(("C", "D", "E", "F")[index], 4),
                onset,
                Fraction(1, 4),
                origin,
            )
        )
    return AssignmentResult(slices=(), notes=tuple(notes))


def test_reduces_dense_beat_to_primary_attack():
    result = reduce_violin2_rhythm(_score(), _dense_assignment(), _slices())
    violin2 = [
        note
        for note in result.assignment.notes
        if note.voice is AssignedVoice.VIOLIN_2
    ]
    assert result.reduced_beat_count == 1
    assert result.removed_attack_count == 3
    assert len(violin2) == 1
    assert violin2[0].duration == 1
    assert violin2[0].origin is AssignedOrigin.RHYTHM_REDUCTION


def test_dense_beat_still_uses_one_attack_when_it_contains_a_tension():
    result = reduce_violin2_rhythm(
        _score(),
        _dense_assignment(tension_at_half=True),
        _slices(),
    )
    violin2 = [
        note
        for note in result.assignment.notes
        if note.voice is AssignedVoice.VIOLIN_2
    ]
    assert result.removed_attack_count == 3
    assert [note.onset for note in violin2] == [Fraction(0)]
    assert [note.duration for note in violin2] == [Fraction(1)]


def test_leaves_sparse_beat_unchanged():
    assignment = AssignmentResult(
        slices=(),
        notes=(
            _note(AssignedVoice.VIOLIN_2, Pitch("C", 4), Fraction(0), Fraction(1, 2)),
            _note(
                AssignedVoice.VIOLIN_2,
                Pitch("D", 4),
                Fraction(1, 2),
                Fraction(1, 2),
            ),
        ),
    )
    result = reduce_violin2_rhythm(_score(), assignment, _slices())
    assert result.removed_attack_count == 0
    assert result.assignment.notes == assignment.notes
