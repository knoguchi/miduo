"""music21-backed contextual harmony analysis."""

from __future__ import annotations

import re
from dataclasses import replace
from fractions import Fraction

from music21 import chord as music21_chord
from music21 import key as music21_key
from music21 import pitch as music21_pitch
from music21 import roman, stream

from miduo.harmony import _interpolate_chord, estimate_chord
from miduo.model import (
    ChordLabel,
    ChordQuality,
    HarmonicSlice,
    Pitch,
    PitchClass,
    ScoreIR,
)

_CONTEXT_DURATION = Fraction(16)
_COMMON_NAME_QUALITIES = {
    "major triad": ChordQuality.MAJOR,
    "minor triad": ChordQuality.MINOR,
    "augmented triad": ChordQuality.AUGMENTED,
    "dominant seventh chord": ChordQuality.DOMINANT_SEVENTH,
    "major seventh chord": ChordQuality.MAJOR_SEVENTH,
    "minor seventh chord": ChordQuality.MINOR_SEVENTH,
    "diminished seventh chord": ChordQuality.DIMINISHED_SEVENTH,
    "half-diminished seventh chord": ChordQuality.HALF_DIMINISHED_SEVENTH,
}
_TENSION_NAMES = {
    1: "b9",
    2: "9",
    3: "#9",
    5: "11",
    6: "#11",
    8: "b13",
    9: "13",
}
_QUALITY_INTERVALS = {
    ChordQuality.MAJOR: frozenset({0, 4, 7}),
    ChordQuality.MINOR: frozenset({0, 3, 7}),
    ChordQuality.AUGMENTED: frozenset({0, 4, 8}),
    ChordQuality.DOMINANT_SEVENTH: frozenset({0, 4, 7, 10}),
    ChordQuality.MAJOR_SEVENTH: frozenset({0, 4, 7, 11}),
    ChordQuality.MINOR_SEVENTH: frozenset({0, 3, 7, 10}),
    ChordQuality.DIMINISHED_SEVENTH: frozenset({0, 3, 6, 9}),
    ChordQuality.HALF_DIMINISHED_SEVENTH: frozenset({0, 3, 6, 10}),
}


def analyze_harmony_music21(
    slices: tuple[HarmonicSlice, ...],
    *,
    confidence_threshold: float,
    score: ScoreIR | None,
) -> tuple[HarmonicSlice, ...]:
    """Label slices with music21 roots and Roman numerals in local key contexts."""

    if not slices:
        return ()
    context_keys = _context_keys(slices, score=score)
    labeled: list[HarmonicSlice] = []
    last_confident: ChordLabel | None = None
    for harmonic_slice in slices:
        fallback = estimate_chord(harmonic_slice)
        context_key = context_keys.get(
            _context_index(harmonic_slice.start),
            next(iter(context_keys.values()), None),
        )
        estimated = _estimate_with_music21(
            harmonic_slice,
            context_key=context_key,
            fallback=fallback,
        )
        if estimated is not None and estimated.confidence >= confidence_threshold:
            chord = estimated
            last_confident = estimated
        elif last_confident is not None:
            chord = _interpolate_chord(
                last_confident,
                estimated,
                confidence_threshold=confidence_threshold,
            )
        else:
            chord = estimated
        labeled.append(replace(harmonic_slice, chord=chord))

    return _mark_music21_cadences(
        tuple(labeled),
        confidence_threshold=confidence_threshold,
    )


def _context_keys(
    slices: tuple[HarmonicSlice, ...],
    *,
    score: ScoreIR | None,
) -> dict[int, music21_key.Key]:
    grouped: dict[int, list[HarmonicSlice]] = {}
    for harmonic_slice in slices:
        if harmonic_slice.active_notes:
            grouped.setdefault(
                _context_index(harmonic_slice.start),
                [],
            ).append(harmonic_slice)

    global_key = _analyze_key(slices)
    result: dict[int, music21_key.Key] = {}
    for context_index, context_slices in grouped.items():
        local_key = _analyze_key(tuple(context_slices))
        result[context_index] = _prefer_notated_key(
            local_key,
            global_key=global_key,
            context_index=context_index,
            score=score,
        )
    return result


def _analyze_key(slices: tuple[HarmonicSlice, ...]) -> music21_key.Key:
    material = stream.Stream()
    for harmonic_slice in slices:
        chord = _to_music21_chord(harmonic_slice)
        if chord is None:
            continue
        chord.quarterLength = float(harmonic_slice.duration)
        material.insert(float(harmonic_slice.start), chord)
    if not material:
        return music21_key.Key("C")
    return material.analyze("key")


def _prefer_notated_key(
    local_key: music21_key.Key,
    *,
    global_key: music21_key.Key,
    context_index: int,
    score: ScoreIR | None,
) -> music21_key.Key:
    if score is None:
        return local_key
    onset = Fraction(context_index) * _CONTEXT_DURATION
    signature = next(
        (
            signature
            for signature in reversed(score.key_signatures)
            if signature.onset <= onset
        ),
        None,
    )
    if signature is None:
        return local_key
    mode = signature.mode if signature.mode in {"major", "minor"} else global_key.mode
    notated = music21_key.KeySignature(signature.fifths).asKey(mode)
    local_confidence = float(getattr(local_key, "correlationCoefficient", 0.0) or 0.0)
    if local_confidence >= 0.75:
        return local_key
    return notated


def _estimate_with_music21(
    harmonic_slice: HarmonicSlice,
    *,
    context_key: music21_key.Key | None,
    fallback: ChordLabel | None,
) -> ChordLabel | None:
    chord = _to_music21_chord(harmonic_slice)
    if chord is None:
        return None
    quality = _COMMON_NAME_QUALITIES.get(chord.commonName)
    if quality is None or fallback is None:
        return fallback

    root = _from_music21_pitch(chord.root())
    bass = _from_music21_pitch(chord.bass())
    observed = {
        int(item.pitchClass)
        for item in chord.pitches
    }
    root_number = int(chord.root().pitchClass)
    template = _QUALITY_INTERVALS[quality]
    extras = {
        (pitch_number - root_number) % 12
        for pitch_number in observed
        if (pitch_number - root_number) % 12 not in template
    }
    tensions = frozenset(
        name
        for interval in extras
        if (name := _TENSION_NAMES.get(interval)) is not None
    )
    roman_numeral = None
    if context_key is not None:
        try:
            analysis = roman.romanNumeralFromChord(chord, context_key)
            roman_numeral = analysis.figure
            if _contains_chromatic_pitch(chord, context_key):
                secondary = roman.romanNumeralFromChord(
                    chord,
                    context_key,
                    preferSecondaryDominants=True,
                )
                if "/" in secondary.figure:
                    roman_numeral = secondary.figure
        except (ValueError, roman.RomanNumeralException):
            roman_numeral = None
    return ChordLabel(
        root=root,
        quality=quality,
        tensions_present=tensions,
        bass=bass,
        confidence=fallback.confidence,
        analysis_backend="music21",
        key=context_key.tonicPitchNameWithCase if context_key is not None else None,
        roman_numeral=roman_numeral,
    )


def _to_music21_chord(
    harmonic_slice: HarmonicSlice,
) -> music21_chord.Chord | None:
    pitches: dict[Fraction, music21_pitch.Pitch] = {}
    for note in harmonic_slice.active_notes:
        pitches.setdefault(note.pitch.midi_number, _to_music21_pitch(note.pitch))
    if not pitches:
        return None
    return music21_chord.Chord(tuple(pitches.values()))


def _to_music21_pitch(pitch: Pitch) -> music21_pitch.Pitch:
    result = music21_pitch.Pitch()
    result.step = pitch.step
    result.octave = pitch.octave
    if pitch.alter:
        result.accidental = music21_pitch.Accidental(float(pitch.alter))
    return result


def _from_music21_pitch(pitch: music21_pitch.Pitch) -> PitchClass:
    alter = (
        Fraction(str(pitch.accidental.alter))
        if pitch.accidental is not None
        else Fraction(0)
    )
    return PitchClass(pitch.step, alter)


def _contains_chromatic_pitch(
    chord: music21_chord.Chord,
    context_key: music21_key.Key,
) -> bool:
    diatonic_pitch_classes = {
        int(context_key.pitchFromDegree(degree).pitchClass)
        for degree in range(1, 8)
    }
    return any(
        int(pitch.pitchClass) not in diatonic_pitch_classes
        for pitch in chord.pitches
    )


def _context_index(onset: Fraction) -> int:
    return int(onset // _CONTEXT_DURATION)


def _mark_music21_cadences(
    slices: tuple[HarmonicSlice, ...],
    *,
    confidence_threshold: float,
) -> tuple[HarmonicSlice, ...]:
    result = list(slices)
    for index, (current, following) in enumerate(zip(slices, slices[1:], strict=False)):
        if current.chord is None or following.chord is None:
            continue
        if following.chord.confidence < confidence_threshold:
            continue
        current_figure = current.chord.roman_numeral
        following_figure = following.chord.roman_numeral
        if current_figure is None or following_figure is None:
            continue
        current_degree = _roman_degree(current_figure)
        following_degree = _roman_degree(following_figure)
        target_degree = (
            _roman_degree(current_figure.rsplit("/", maxsplit=1)[1])
            if "/" in current_figure
            else "I"
        )
        if current_degree == "V" and following_degree == target_degree:
            result[index] = replace(current, is_cadence=True)
    return tuple(result)


def _roman_degree(figure: str) -> str | None:
    match = re.match(r"^[#b]*([ivIV]+)", figure)
    return match.group(1).upper() if match else None
