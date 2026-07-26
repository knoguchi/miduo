from fractions import Fraction

from miduo.assignment import assign_voices
from miduo.model import (
    AssignedNote,
    AssignedOrigin,
    AssignedVoice,
    AssignmentResult,
    HarmonicSlice,
    NoteEvent,
    PartInfo,
    Pitch,
    ScoreIR,
    SourceVoice,
    TimeSignature,
    ValidationIssueType,
)
from miduo.rhythm import reduce_violin2_rhythm
from miduo.validation import validate_assignment, validate_with_retries


def _assigned(
    voice: AssignedVoice,
    pitch: Pitch,
    onset: int,
    duration: int,
) -> AssignedNote:
    return AssignedNote(
        voice=voice,
        pitch=pitch,
        onset=Fraction(onset),
        duration=Fraction(duration),
        origin=AssignedOrigin.MELODY,
        cost_breakdown={},
    )


def test_accepts_valid_assignment():
    assignment = AssignmentResult(
        slices=(),
        notes=(
            _assigned(AssignedVoice.VIOLIN_1, Pitch("G", 4), 0, 1),
            _assigned(AssignedVoice.VIOLIN_2, Pitch("E", 4), 0, 1),
        ),
    )
    assert validate_assignment(assignment) == ()


def test_reports_range_violation():
    assignment = AssignmentResult(
        slices=(),
        notes=(_assigned(AssignedVoice.VIOLIN_2, Pitch("C", 3), 0, 1),),
    )
    issues = validate_assignment(assignment)
    assert issues[0].issue_type is ValidationIssueType.RANGE_VIOLATION


def test_reports_polyphony():
    assignment = AssignmentResult(
        slices=(),
        notes=(
            _assigned(AssignedVoice.VIOLIN_1, Pitch("C", 4), 0, 2),
            _assigned(AssignedVoice.VIOLIN_1, Pitch("D", 4), 1, 1),
        ),
    )
    issues = validate_assignment(assignment)
    assert issues[0].issue_type is ValidationIssueType.POLYPHONY


def test_reports_voice_crossing():
    assignment = AssignmentResult(
        slices=(),
        notes=(
            _assigned(AssignedVoice.VIOLIN_1, Pitch("C", 4), 0, 1),
            _assigned(AssignedVoice.VIOLIN_2, Pitch("G", 4), 0, 1),
        ),
    )
    issues = validate_assignment(assignment)
    assert issues[0].issue_type is ValidationIssueType.VOICE_CROSSING


def test_retry_replaces_invalid_assignment():
    source_note = NoteEvent(Pitch("G", 4), Fraction(0), Fraction(1), 1)
    score = ScoreIR(
        title=None,
        parts=(PartInfo("P1", "Melody"),),
        source_voices=(SourceVoice(1, "P1", "1", None),),
        notes=(source_note,),
        time_signatures=(),
        duration=Fraction(1),
    )
    slices = (
        HarmonicSlice(
            start=Fraction(0),
            end=Fraction(1),
            active_notes=(source_note,),
            chord=None,
            beat_weight=1.0,
        ),
    )
    invalid = AssignmentResult(
        slices=(),
        notes=(_assigned(AssignedVoice.VIOLIN_1, Pitch("C", 2), 0, 1),),
    )
    result = validate_with_retries(score, slices, (), invalid)
    assert result.is_valid
    assert result.retry_count == 1
    assert str(result.assignment.notes[0].pitch) == "G4"


def test_global_assignment_avoids_crossing_after_rhythm_extension():
    melody_high = NoteEvent(Pitch("G", 5), Fraction(0), Fraction(1, 2), 1)
    melody_low = NoteEvent(Pitch("F", 4), Fraction(1, 2), Fraction(1, 2), 1)
    support = tuple(
        NoteEvent(pitch, onset, Fraction(1, 4), 2)
        for pitch, onset in (
            (Pitch("E", 5), Fraction(0)),
            (Pitch("F", 5), Fraction(1, 4)),
            (Pitch("D", 5), Fraction(1, 2)),
            (Pitch("E", 5), Fraction(3, 4)),
        )
    )
    score = ScoreIR(
        title=None,
        parts=(PartInfo("P1", "Melody"), PartInfo("P2", "Support")),
        source_voices=(
            SourceVoice(1, "P1", "1", None),
            SourceVoice(2, "P2", "1", None),
        ),
        notes=(melody_high, melody_low, *support),
        time_signatures=(TimeSignature(Fraction(0), 4, 4),),
        duration=Fraction(1),
    )
    slices = tuple(
        HarmonicSlice(
            start=onset,
            end=onset + Fraction(1, 4),
            active_notes=(
                melody_high if onset < Fraction(1, 2) else melody_low,
                support[index],
            ),
            chord=None,
            beat_weight=1.0 if onset == 0 else 0.25,
        )
        for index, onset in enumerate(
            (Fraction(0), Fraction(1, 4), Fraction(1, 2), Fraction(3, 4))
        )
    )
    assigned = assign_voices(score, slices, ())
    reduced = reduce_violin2_rhythm(score, assigned, slices)
    assert validate_assignment(reduced.assignment) == ()
    result = validate_with_retries(score, slices, (), reduced.assignment)
    assert result.is_valid
    assert result.retry_count == 0
