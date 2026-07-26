from fractions import Fraction

from miduo.harmony import analyze_harmony, estimate_chord
from miduo.model import ChordQuality, HarmonicSlice, NoteEvent, Pitch


def _slice(start: int, pitches: tuple[str, ...]) -> HarmonicSlice:
    notes = tuple(
        NoteEvent(
            pitch=Pitch(pitch[0], int(pitch[-1]), Fraction({"b": -1, "#": 1}.get(pitch[1:2], 0))),
            onset=Fraction(start),
            duration=Fraction(1),
            source_voice=index,
        )
        for index, pitch in enumerate(pitches, start=1)
    )
    return HarmonicSlice(
        start=Fraction(start),
        end=Fraction(start + 1),
        active_notes=notes,
        chord=None,
        beat_weight=1.0,
    )


def test_estimate_root_position_major_chord():
    chord = estimate_chord(_slice(0, ("C4", "E4", "G4")))
    assert chord is not None
    assert str(chord.root) == "C"
    assert chord.quality is ChordQuality.MAJOR
    assert chord.confidence == 1.0


def test_estimate_dominant_seventh():
    chord = estimate_chord(_slice(0, ("G3", "B3", "D4", "F4")))
    assert chord is not None
    assert chord.symbol == "G:dom7"
    assert chord.confidence == 1.0


def test_low_confidence_slice_inherits_previous_chord():
    first, second = analyze_harmony(
        (
            _slice(0, ("C4", "E4", "G4")),
            _slice(1, ("D4",)),
        )
    )
    assert first.chord is not None
    assert second.chord is not None
    assert second.chord.symbol == "C:maj"
    assert second.chord.confidence < 0.65


def test_dominant_to_tonic_marks_cadence():
    dominant, tonic = analyze_harmony(
        (
            _slice(0, ("G3", "B3", "D4", "F4")),
            _slice(1, ("C4", "E4", "G4")),
        )
    )
    assert dominant.is_cadence
    assert not tonic.is_cadence


def test_music21_backend_adds_key_and_roman_context():
    progression = (
        _slice(0, ("C4", "E4", "G4")),
        _slice(1, ("F4", "A4", "C5")),
        _slice(2, ("D4", "F#4", "A4")),
        _slice(3, ("G3", "B3", "D4", "F4")),
        _slice(4, ("C4", "E4", "G4")),
    ) * 4
    analyzed = analyze_harmony(progression, backend="music21")

    opening, _, secondary_dominant, dominant, tonic = analyzed[:5]
    assert opening.chord is not None
    assert opening.chord.analysis_backend == "music21"
    assert opening.chord.key == "C"
    assert opening.chord.roman_numeral == "I"
    assert not opening.is_cadence
    assert secondary_dominant.chord is not None
    assert secondary_dominant.chord.roman_numeral == "V/V"
    assert secondary_dominant.is_cadence
    assert dominant.chord is not None
    assert dominant.chord.roman_numeral == "V7"
    assert dominant.is_cadence
    assert not tonic.is_cadence
