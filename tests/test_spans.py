from fractions import Fraction

from miduo.model import (
    ChordLabel,
    ChordQuality,
    HarmonicSlice,
    NoteEvent,
    PartInfo,
    Pitch,
    PitchClass,
    ScoreIR,
    SpanType,
)
from miduo.spans import detect_sustain_spans


def _chord(root: str, quality: ChordQuality) -> ChordLabel:
    pitch_class = PitchClass(root)
    return ChordLabel(
        root=pitch_class,
        quality=quality,
        tensions_present=frozenset(),
        bass=pitch_class,
        confidence=1.0,
    )


def _slice(start: int, end: int, chord: ChordLabel) -> HarmonicSlice:
    return HarmonicSlice(
        start=Fraction(start),
        end=Fraction(end),
        active_notes=(),
        chord=chord,
        beat_weight=1.0,
    )


def _score(notes: tuple[NoteEvent, ...], duration: int) -> ScoreIR:
    return ScoreIR(
        title=None,
        parts=(PartInfo("P1", "Part"),),
        source_voices=(),
        notes=notes,
        time_signatures=(),
        duration=Fraction(duration),
    )


def test_detect_suspension_and_resolution():
    notes = (
        NoteEvent(Pitch("C", 4), Fraction(0), Fraction(1), 1),
        NoteEvent(Pitch("C", 4), Fraction(1), Fraction(1), 1),
        NoteEvent(Pitch("D", 4), Fraction(2), Fraction(1), 1),
    )
    slices = (
        _slice(0, 1, _chord("C", ChordQuality.MAJOR)),
        _slice(1, 2, _chord("G", ChordQuality.MAJOR)),
        _slice(2, 3, _chord("G", ChordQuality.MAJOR)),
    )
    spans = detect_sustain_spans(_score(notes, 3), slices)
    assert len(spans) == 1
    assert spans[0].span_type is SpanType.SUSPENSION
    assert str(spans[0].resolves_to) == "D"


def test_detect_plain_sustain():
    notes = (
        NoteEvent(Pitch("E", 4), Fraction(0), Fraction(1), 1),
        NoteEvent(Pitch("E", 4), Fraction(1), Fraction(2), 1),
    )
    slices = (_slice(0, 3, _chord("C", ChordQuality.MAJOR)),)
    spans = detect_sustain_spans(_score(notes, 3), slices)
    assert spans[0].span_type is SpanType.PLAIN_SUSTAIN


def test_ignore_short_repeated_pitch_without_tie():
    notes = (
        NoteEvent(Pitch("E", 4), Fraction(0), Fraction(1), 1),
        NoteEvent(Pitch("E", 4), Fraction(1), Fraction(1), 1),
    )
    slices = (_slice(0, 2, _chord("C", ChordQuality.MAJOR)),)
    assert detect_sustain_spans(_score(notes, 2), slices) == ()


def test_keep_short_tied_span():
    notes = (
        NoteEvent(Pitch("E", 4), Fraction(0), Fraction(1), 1, tie_next=True),
        NoteEvent(Pitch("E", 4), Fraction(1), Fraction(1), 1, tie_prev=True),
    )
    slices = (_slice(0, 2, _chord("C", ChordQuality.MAJOR)),)
    spans = detect_sustain_spans(_score(notes, 2), slices)
    assert spans[0].span_type is SpanType.PLAIN_SUSTAIN


def test_detect_long_root_or_fifth_as_pedal():
    notes = (NoteEvent(Pitch("G", 2), Fraction(0), Fraction(8), 1),)
    slices = (_slice(0, 8, _chord("C", ChordQuality.MAJOR)),)
    spans = detect_sustain_spans(_score(notes, 8), slices)
    assert spans[0].span_type is SpanType.PEDAL
