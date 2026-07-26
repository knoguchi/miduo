"""Lightweight, dependency-free chord estimation."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from enum import StrEnum
from fractions import Fraction

from miduo.model import ChordLabel, ChordQuality, HarmonicSlice, PitchClass, ScoreIR


class HarmonyBackend(StrEnum):
    INTERNAL = "internal"
    MUSIC21 = "music21"

_CHORD_TEMPLATES: tuple[tuple[ChordQuality, frozenset[int]], ...] = (
    (ChordQuality.MAJOR, frozenset({0, 4, 7})),
    (ChordQuality.MINOR, frozenset({0, 3, 7})),
    (ChordQuality.AUGMENTED, frozenset({0, 4, 8})),
    (ChordQuality.DOMINANT_SEVENTH, frozenset({0, 4, 7, 10})),
    (ChordQuality.MAJOR_SEVENTH, frozenset({0, 4, 7, 11})),
    (ChordQuality.MINOR_SEVENTH, frozenset({0, 3, 7, 10})),
    (ChordQuality.DIMINISHED_SEVENTH, frozenset({0, 3, 6, 9})),
    (ChordQuality.HALF_DIMINISHED_SEVENTH, frozenset({0, 3, 6, 10})),
)

_CANONICAL_PITCH_CLASSES = (
    PitchClass("C"),
    PitchClass("C", Fraction(1)),
    PitchClass("D"),
    PitchClass("E", Fraction(-1)),
    PitchClass("E"),
    PitchClass("F"),
    PitchClass("F", Fraction(1)),
    PitchClass("G"),
    PitchClass("A", Fraction(-1)),
    PitchClass("A"),
    PitchClass("B", Fraction(-1)),
    PitchClass("B"),
)

_TENSION_NAMES = {
    1: "b9",
    2: "9",
    3: "#9",
    5: "11",
    6: "#11",
    8: "b13",
    9: "13",
}


def analyze_harmony(
    slices: tuple[HarmonicSlice, ...],
    *,
    confidence_threshold: float = 0.65,
    backend: HarmonyBackend | str = HarmonyBackend.INTERNAL,
    score: ScoreIR | None = None,
) -> tuple[HarmonicSlice, ...]:
    """Attach chord labels and cadence flags to harmonic slices."""

    if not 0 <= confidence_threshold <= 1:
        raise ValueError("confidence_threshold must be between 0 and 1")
    selected_backend = HarmonyBackend(backend)
    if selected_backend is HarmonyBackend.MUSIC21:
        from miduo.music21_harmony import analyze_harmony_music21

        return analyze_harmony_music21(
            slices,
            confidence_threshold=confidence_threshold,
            score=score,
        )

    labeled: list[HarmonicSlice] = []
    last_confident: ChordLabel | None = None
    for harmonic_slice in slices:
        estimated = estimate_chord(harmonic_slice)
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

    return _mark_cadences(
        tuple(labeled),
        confidence_threshold=confidence_threshold,
    )


def estimate_chord(harmonic_slice: HarmonicSlice) -> ChordLabel | None:
    """Return the best template match for one slice."""

    pitch_classes = [
        pitch_class
        for note in harmonic_slice.active_notes
        if (pitch_class := _integer_pitch_class(note.pitch.pitch_class)) is not None
    ]
    observed = frozenset(pitch_classes)
    if not observed:
        return None

    bass_number = pitch_classes[0]
    spellings: dict[int, PitchClass] = {}
    for note in harmonic_slice.active_notes:
        number = _integer_pitch_class(note.pitch.pitch_class)
        if number is not None:
            spellings.setdefault(number, note.pitch.pitch_class)

    best: tuple[float, int, ChordQuality, frozenset[int]] | None = None
    best_rank = (-1.0, 0)
    for root in range(12):
        for quality, relative_template in _CHORD_TEMPLATES:
            template = frozenset((root + interval) % 12 for interval in relative_template)
            matches = len(observed & template)
            observed_coverage = matches / len(observed)
            template_coverage = matches / len(template)
            bass_bonus = 0.10 if bass_number == root else 0.0
            raw_score = 0.55 * observed_coverage + 0.35 * template_coverage + bass_bonus
            evidence_factor = min(1.0, len(observed) / 3)
            confidence = raw_score * evidence_factor
            rank = (confidence, -len(relative_template))
            if best is None or rank > best_rank:
                best = (confidence, root, quality, relative_template)
                best_rank = rank

    assert best is not None
    confidence, root, quality, relative_template = best
    root_pitch = spellings.get(root, _CANONICAL_PITCH_CLASSES[root])
    bass_pitch = spellings.get(bass_number, _CANONICAL_PITCH_CLASSES[bass_number])
    extras = {
        (pitch_class - root) % 12
        for pitch_class in observed
        if (pitch_class - root) % 12 not in relative_template
    }
    tensions = frozenset(
        tension_name
        for interval in extras
        if (tension_name := _TENSION_NAMES.get(interval)) is not None
    )
    return ChordLabel(
        root=root_pitch,
        quality=quality,
        tensions_present=tensions,
        bass=bass_pitch,
        confidence=round(confidence, 6),
    )


def chord_histogram(slices: tuple[HarmonicSlice, ...]) -> Counter[str]:
    """Count labeled slices by chord symbol."""

    return Counter(
        harmonic_slice.chord.symbol
        for harmonic_slice in slices
        if harmonic_slice.chord is not None
    )


def chord_pitch_classes(chord: ChordLabel) -> frozenset[int]:
    """Return the integer pitch classes in a chord's core template."""

    root = _integer_pitch_class(chord.root)
    if root is None:
        return frozenset()
    template = next(
        intervals
        for quality, intervals in _CHORD_TEMPLATES
        if quality is chord.quality
    )
    return frozenset((root + interval) % 12 for interval in template)


def _interpolate_chord(
    previous: ChordLabel,
    estimated: ChordLabel | None,
    *,
    confidence_threshold: float,
) -> ChordLabel:
    bass = estimated.bass if estimated is not None else previous.bass
    confidence = estimated.confidence if estimated is not None else 0.0
    return replace(
        previous,
        bass=bass,
        confidence=min(confidence, confidence_threshold * 0.75),
    )


def _mark_cadences(
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
        current_root = _integer_pitch_class(current.chord.root)
        following_root = _integer_pitch_class(following.chord.root)
        if current_root is None or following_root is None or current_root == following_root:
            continue
        dominant_quality = current.chord.quality in {
            ChordQuality.MAJOR,
            ChordQuality.DOMINANT_SEVENTH,
        }
        tonic_quality = following.chord.quality in {
            ChordQuality.MAJOR,
            ChordQuality.MINOR,
        }
        has_confident_evidence = _run_has_confident_chord(
            slices,
            index,
            confidence_threshold=confidence_threshold,
        )
        if (
            dominant_quality
            and tonic_quality
            and has_confident_evidence
            and following_root == (current_root + 5) % 12
        ):
            result[index] = replace(current, is_cadence=True)
    return tuple(result)


def _run_has_confident_chord(
    slices: tuple[HarmonicSlice, ...],
    index: int,
    *,
    confidence_threshold: float,
) -> bool:
    target = slices[index].chord
    assert target is not None
    for candidate_slice in reversed(slices[: index + 1]):
        candidate = candidate_slice.chord
        if candidate is None or candidate.symbol != target.symbol:
            break
        if candidate.confidence >= confidence_threshold:
            return True
    return False


def _integer_pitch_class(pitch_class: PitchClass) -> int | None:
    normalized = pitch_class.semitone % 12
    if normalized.denominator != 1:
        return None
    return int(normalized)
