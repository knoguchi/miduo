"""Greedy two-violin voice assignment."""

from __future__ import annotations

import heapq
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, replace
from fractions import Fraction

from miduo.harmony import chord_pitch_classes
from miduo.model import (
    AssignedNote,
    AssignedOrigin,
    AssignedVoice,
    AssignmentResult,
    ChordLabel,
    ChordQuality,
    HarmonicSlice,
    NoteEvent,
    Pitch,
    ScoreIR,
    SliceAssignment,
    SpanType,
    SustainSpan,
)


@dataclass(frozen=True, slots=True)
class AssignmentWeights:
    tension_loss: float = 8.0
    range_violation: float = 100.0
    leap: float = 1.0
    cadence_root: float = 4.0
    voice_crossing: float = 100.0
    spacing: float = 2.0
    span_continuity_bonus: float = 2.0
    large_leap: float = 3.0
    direction_change: float = 0.5
    weak_beat_change: float = 1.5
    common_tone_bonus: float = 0.6
    structural_leap: float = 6.0


@dataclass(frozen=True, slots=True)
class ViolinRanges:
    violin1_low: int = 55  # G3
    violin1_high: int = 100  # E7
    violin2_low: int = 55  # G3
    violin2_high: int = 93  # A6


@dataclass(frozen=True, slots=True)
class _Candidate:
    pitch: Pitch
    source_note: NoteEvent


@dataclass(frozen=True, slots=True)
class _OptimizationPath:
    cost: float
    selections: tuple[tuple[_Candidate | None, dict[str, float]], ...]
    previous_pitch: Pitch | None
    before_previous_pitch: Pitch | None
    anchor_pitch: Pitch | None


def assign_voices(
    score: ScoreIR,
    slices: tuple[HarmonicSlice, ...],
    spans: tuple[SustainSpan, ...],
    *,
    weights: AssignmentWeights = AssignmentWeights(),
    ranges: ViolinRanges = ViolinRanges(),
    progress: Callable[[int, int, str], None] | None = None,
) -> AssignmentResult:
    """Build a greedy baseline, then optimize Violin 2 over short phrases."""

    active_span_pitches = _active_span_pitches(slices, spans)
    greedy = _assign_voices_greedy(
        score,
        slices,
        spans,
        active_span_pitches=active_span_pitches,
        weights=weights,
        ranges=ranges,
    )
    return _optimize_violin2(
        score,
        slices,
        spans,
        greedy,
        active_span_pitches=active_span_pitches,
        weights=weights,
        ranges=ranges,
        progress=progress,
    )


def _assign_voices_greedy(
    score: ScoreIR,
    slices: tuple[HarmonicSlice, ...],
    spans: tuple[SustainSpan, ...],
    *,
    active_span_pitches: tuple[tuple[Fraction, ...], ...],
    weights: AssignmentWeights,
    ranges: ViolinRanges,
) -> AssignmentResult:
    """Assign one pitch per violin to each harmonic slice."""

    melody_voice = _melody_source_voice(score)
    previous_v1: Pitch | None = None
    previous_v2: Pitch | None = None
    assignments: list[SliceAssignment] = []

    for slice_index, harmonic_slice in enumerate(slices):
        if not harmonic_slice.active_notes:
            assignments.append(
                SliceAssignment(
                    start=harmonic_slice.start,
                    end=harmonic_slice.end,
                    violin1_pitch=None,
                    violin2_pitch=None,
                    violin1_origin=None,
                    violin2_origin=None,
                    cost_breakdown={"total": 0.0},
                )
            )
            continue

        melody_notes = [
            note for note in harmonic_slice.active_notes if note.source_voice == melody_voice
        ]
        if melody_voice is not None and not melody_notes:
            candidates = _deduplicate_candidates(
                candidate
                for note in harmonic_slice.active_notes
                for candidate in _octave_candidates(
                    note,
                    low=ranges.violin2_low,
                    high=ranges.violin2_high,
                )
            )
            selected = min(
                candidates,
                key=lambda candidate: _solo_support_cost(
                    harmonic_slice,
                    candidate.pitch,
                    previous=previous_v2,
                    active_span_pitches=active_span_pitches[slice_index],
                    weights=weights,
                    ranges=ranges,
                )["total"],
            )
            breakdown = _solo_support_cost(
                harmonic_slice,
                selected.pitch,
                previous=previous_v2,
                active_span_pitches=active_span_pitches[slice_index],
                weights=weights,
                ranges=ranges,
            )
            assignments.append(
                SliceAssignment(
                    start=harmonic_slice.start,
                    end=harmonic_slice.end,
                    violin1_pitch=None,
                    violin2_pitch=selected.pitch,
                    violin1_origin=None,
                    violin2_origin=_support_origin(
                        selected.pitch,
                        harmonic_slice,
                    ),
                    cost_breakdown=breakdown,
                )
            )
            previous_v2 = selected.pitch
            continue
        source_v1 = max(
            melody_notes or harmonic_slice.active_notes,
            key=lambda note: note.pitch.midi_number,
        )
        v1_candidates = _melody_candidates(
            source_v1,
            low=ranges.violin1_low,
            high=ranges.violin1_high,
        )
        if not v1_candidates:
            v1_candidates = (_Candidate(source_v1.pitch, source_v1),)

        best: tuple[float, _Candidate, _Candidate | None, dict[str, float]] | None = None
        for v1_candidate in v1_candidates:
            support_notes = [
                note for note in harmonic_slice.active_notes if note is not source_v1
            ]
            v2_candidates = _deduplicate_candidates(
                candidate
                for note in support_notes
                for candidate in _octave_candidates(
                    note,
                    low=ranges.violin2_low,
                    high=ranges.violin2_high,
                )
                if candidate.pitch.midi_number != v1_candidate.pitch.midi_number
            )
            for v2_candidate in (*v2_candidates, None):
                breakdown = _cost(
                    harmonic_slice,
                    v1_candidate.pitch,
                    v2_candidate.pitch if v2_candidate else None,
                    previous_v1=previous_v1,
                    previous_v2=previous_v2,
                    active_span_pitches=active_span_pitches[slice_index],
                    weights=weights,
                    ranges=ranges,
                )
                candidate_result = (
                    breakdown["total"],
                    v1_candidate,
                    v2_candidate,
                    breakdown,
                )
                if best is None or candidate_result[0] < best[0]:
                    best = candidate_result

        assert best is not None
        _, selected_v1, selected_v2, cost_breakdown = best
        v1_origin = _origin_for_pitch(
            selected_v1.pitch,
            harmonic_slice,
            spans,
            default=AssignedOrigin.MELODY,
        )
        v2_origin = (
            _origin_for_pitch(
                selected_v2.pitch,
                harmonic_slice,
                spans,
                default=_support_origin(selected_v2.pitch, harmonic_slice),
            )
            if selected_v2 is not None
            else None
        )
        assignments.append(
            SliceAssignment(
                start=harmonic_slice.start,
                end=harmonic_slice.end,
                violin1_pitch=selected_v1.pitch,
                violin2_pitch=selected_v2.pitch if selected_v2 else None,
                violin1_origin=v1_origin,
                violin2_origin=v2_origin,
                cost_breakdown=cost_breakdown,
            )
        )
        previous_v1 = selected_v1.pitch
        if selected_v2 is not None:
            previous_v2 = selected_v2.pitch

    return AssignmentResult(
        slices=tuple(assignments),
        notes=_merge_assignments(assignments),
    )


def _optimize_violin2(
    score: ScoreIR,
    slices: tuple[HarmonicSlice, ...],
    spans: tuple[SustainSpan, ...],
    baseline: AssignmentResult,
    *,
    active_span_pitches: tuple[tuple[Fraction, ...], ...],
    weights: AssignmentWeights,
    ranges: ViolinRanges,
    phrase_duration: Fraction = Fraction(8),
    beam_width: int = 24,
    candidate_limit: int = 8,
    progress: Callable[[int, int, str], None] | None = None,
) -> AssignmentResult:
    melody_voice = _melody_source_voice(score)
    previous_v1_by_slice = _previous_violin1_pitches(baseline.slices)
    optimized: list[SliceAssignment] = []
    carried_previous: Pitch | None = None
    carried_before_previous: Pitch | None = None
    carried_anchor: Pitch | None = None

    for phrase_start, phrase_end in _phrase_ranges(slices, phrase_duration):
        paths = (
            _OptimizationPath(
                cost=0.0,
                selections=(),
                previous_pitch=carried_previous,
                before_previous_pitch=carried_before_previous,
                anchor_pitch=carried_anchor,
            ),
        )
        for index in range(phrase_start, phrase_end):
            harmonic_slice = slices[index]
            baseline_slice = baseline.slices[index]
            violin1 = baseline_slice.violin1_pitch
            candidates = _support_candidates(
                harmonic_slice,
                violin1,
                melody_voice=melody_voice,
                ranges=ranges,
            )
            candidates = _prune_support_candidates(
                candidates,
                harmonic_slice,
                violin1,
                baseline_pitch=baseline_slice.violin2_pitch,
                active_span_pitches=active_span_pitches[index],
                weights=weights,
                ranges=ranges,
                limit=candidate_limit,
            )
            best_by_state: dict[
                tuple[
                    Fraction | None,
                    Fraction | None,
                    Fraction | None,
                    Fraction | None,
                ],
                _OptimizationPath,
            ] = {}
            for path in paths:
                for candidate in candidates:
                    violin2 = candidate.pitch if candidate is not None else None
                    if violin1 is None:
                        breakdown = _solo_support_cost(
                            harmonic_slice,
                            violin2,
                            previous=path.previous_pitch,
                            active_span_pitches=active_span_pitches[index],
                            weights=weights,
                            ranges=ranges,
                        )
                    else:
                        breakdown = _cost(
                            harmonic_slice,
                            violin1,
                            violin2,
                            previous_v1=previous_v1_by_slice[index],
                            previous_v2=path.previous_pitch,
                            active_span_pitches=active_span_pitches[index],
                            weights=weights,
                            ranges=ranges,
                        )
                    _add_musical_transition_cost(
                        breakdown,
                        harmonic_slice,
                        before_previous=path.before_previous_pitch,
                        previous=path.previous_pitch,
                        current=violin2,
                        anchor=path.anchor_pitch,
                        weights=weights,
                    )
                    if violin2 is None:
                        new_previous = path.previous_pitch
                        new_before = None
                    else:
                        new_previous = violin2
                        new_before = path.previous_pitch
                    new_anchor = (
                        violin2
                        if violin2 is not None and harmonic_slice.beat_weight == 1.0
                        else path.anchor_pitch
                    )
                    new_path = _OptimizationPath(
                        cost=path.cost + breakdown["total"],
                        selections=(*path.selections, (candidate, breakdown)),
                        previous_pitch=new_previous,
                        before_previous_pitch=new_before,
                        anchor_pitch=new_anchor,
                    )
                    state = (
                        violin2.midi_number if violin2 else None,
                        new_previous.midi_number if new_previous else None,
                        new_before.midi_number if new_before else None,
                        new_anchor.midi_number if new_anchor else None,
                    )
                    existing = best_by_state.get(state)
                    if existing is None or new_path.cost < existing.cost:
                        best_by_state[state] = new_path
            paths = tuple(
                sorted(best_by_state.values(), key=lambda path: path.cost)[:beam_width]
            )

        best_path = min(paths, key=lambda path: path.cost)
        for offset, (candidate, breakdown) in enumerate(best_path.selections):
            index = phrase_start + offset
            harmonic_slice = slices[index]
            baseline_slice = baseline.slices[index]
            pitch = candidate.pitch if candidate is not None else None
            origin = (
                _origin_for_pitch(
                    pitch,
                    harmonic_slice,
                    spans,
                    default=_support_origin(pitch, harmonic_slice),
                )
                if pitch is not None
                else None
            )
            optimized.append(
                replace(
                    baseline_slice,
                    violin2_pitch=pitch,
                    violin2_origin=origin,
                    cost_breakdown=breakdown,
                )
            )
        carried_previous = best_path.previous_pitch
        carried_before_previous = best_path.before_previous_pitch
        carried_anchor = best_path.anchor_pitch
        if progress is not None:
            measure_index, measure_number = _measure_at(
                score,
                slices[phrase_end - 1].start,
            )
            progress(measure_index, len(score.measures), measure_number)

    return AssignmentResult(
        slices=tuple(optimized),
        notes=_merge_assignments(optimized),
    )


def _cost(
    harmonic_slice: HarmonicSlice,
    violin1: Pitch,
    violin2: Pitch | None,
    *,
    previous_v1: Pitch | None,
    previous_v2: Pitch | None,
    active_span_pitches: tuple[Fraction, ...],
    weights: AssignmentWeights,
    ranges: ViolinRanges,
) -> dict[str, float]:
    selected = (violin1,) if violin2 is None else (violin1, violin2)
    raw_tension = _tension_loss(harmonic_slice.chord, selected)
    if violin2 is None and len(harmonic_slice.active_notes) > 1:
        raw_tension += 0.5
    raw_range = _range_violation(
        violin1,
        low=ranges.violin1_low,
        high=ranges.violin1_high,
    )
    if violin2 is not None:
        raw_range += _range_violation(
            violin2,
            low=ranges.violin2_low,
            high=ranges.violin2_high,
        )
    raw_leap = _leap_cost(previous_v1, violin1) + _leap_cost(previous_v2, violin2)
    raw_cadence = _cadence_root_cost(harmonic_slice, selected)
    raw_crossing = (
        1.0 if violin2 is not None and violin1.midi_number < violin2.midi_number else 0.0
    )
    raw_spacing = _spacing_cost(violin1, violin2)
    raw_span = _span_bonus(selected, active_span_pitches)

    breakdown = {
        "tension_loss": weights.tension_loss * raw_tension,
        "range_violation": weights.range_violation * raw_range,
        "leap": weights.leap * raw_leap,
        "cadence_root": weights.cadence_root * raw_cadence,
        "voice_crossing": weights.voice_crossing * raw_crossing,
        "spacing": weights.spacing * raw_spacing,
        "span_continuity": -weights.span_continuity_bonus * raw_span,
    }
    breakdown["total"] = sum(breakdown.values())
    return breakdown


def _solo_support_cost(
    harmonic_slice: HarmonicSlice,
    pitch: Pitch | None,
    *,
    previous: Pitch | None,
    active_span_pitches: tuple[Fraction, ...],
    weights: AssignmentWeights,
    ranges: ViolinRanges,
) -> dict[str, float]:
    if pitch is None:
        return {"missing_support": weights.tension_loss, "total": weights.tension_loss}
    raw_tension = _tension_loss(harmonic_slice.chord, (pitch,))
    raw_range = _range_violation(
        pitch,
        low=ranges.violin2_low,
        high=ranges.violin2_high,
    )
    raw_leap = _leap_cost(previous, pitch)
    raw_cadence = _cadence_root_cost(harmonic_slice, (pitch,))
    raw_span = _span_bonus((pitch,), active_span_pitches)
    breakdown = {
        "tension_loss": weights.tension_loss * raw_tension,
        "range_violation": weights.range_violation * raw_range,
        "leap": weights.leap * raw_leap,
        "cadence_root": weights.cadence_root * raw_cadence,
        "span_continuity": -weights.span_continuity_bonus * raw_span,
    }
    breakdown["total"] = sum(breakdown.values())
    return breakdown


def _add_musical_transition_cost(
    breakdown: dict[str, float],
    harmonic_slice: HarmonicSlice,
    *,
    before_previous: Pitch | None,
    previous: Pitch | None,
    current: Pitch | None,
    anchor: Pitch | None,
    weights: AssignmentWeights,
) -> None:
    large_leap = 0.0
    direction_change = 0.0
    weak_beat_change = 0.0
    common_tone = 0.0
    structural_leap = 0.0
    if previous is not None and current is not None:
        interval = float(current.midi_number - previous.midi_number)
        distance = abs(interval)
        if distance > 7:
            large_leap = ((distance - 7) / 5) ** 2
        if before_previous is not None:
            previous_interval = float(
                previous.midi_number - before_previous.midi_number
            )
            if previous_interval * interval < 0:
                direction_change = min(
                    abs(previous_interval),
                    distance,
                ) / 12
        if current.midi_number != previous.midi_number and harmonic_slice.beat_weight < 1:
            weak_beat_change = 1.0 - harmonic_slice.beat_weight
        if current.midi_number == previous.midi_number:
            common_tone = 1.0
        if harmonic_slice.beat_weight == 1.0 and anchor is not None:
            anchor_distance = float(abs(current.midi_number - anchor.midi_number))
            structural_leap = (anchor_distance / 12) ** 2

    additions = {
        "global_large_leap": weights.large_leap * large_leap,
        "global_direction_change": weights.direction_change * direction_change,
        "global_weak_beat_change": weights.weak_beat_change * weak_beat_change,
        "global_common_tone": -weights.common_tone_bonus * common_tone,
        "global_structural_leap": weights.structural_leap * structural_leap,
    }
    breakdown.update(additions)
    breakdown["total"] = sum(
        value for key, value in breakdown.items() if key != "total"
    )


def _tension_loss(chord: ChordLabel | None, selected: tuple[Pitch, ...]) -> float:
    if chord is None:
        return 0.0
    root = _pitch_class_number(chord.root.semitone)
    if root is None:
        return 0.0
    selected_numbers = {
        number
        for pitch in selected
        if (number := _pitch_class_number(pitch.pitch_class.semitone)) is not None
    }
    characteristic_intervals = {
        ChordQuality.MAJOR: {4},
        ChordQuality.MINOR: {3},
        ChordQuality.AUGMENTED: {4, 8},
        ChordQuality.DOMINANT_SEVENTH: {4, 10},
        ChordQuality.MAJOR_SEVENTH: {4, 11},
        ChordQuality.MINOR_SEVENTH: {3, 10},
        ChordQuality.DIMINISHED_SEVENTH: {3, 6, 9},
        ChordQuality.HALF_DIMINISHED_SEVENTH: {3, 6, 10},
        ChordQuality.OTHER: set(),
    }[chord.quality]
    required = {(root + interval) % 12 for interval in characteristic_intervals}
    if not required:
        return 0.0
    return len(required - selected_numbers) / len(required)


def _cadence_root_cost(
    harmonic_slice: HarmonicSlice,
    selected: tuple[Pitch, ...],
) -> float:
    if not harmonic_slice.is_cadence or harmonic_slice.chord is None:
        return 0.0
    root = _pitch_class_number(harmonic_slice.chord.root.semitone)
    if root is None:
        return 0.0
    selected_numbers = {
        number
        for pitch in selected
        if (number := _pitch_class_number(pitch.pitch_class.semitone)) is not None
    }
    return 0.0 if selected_numbers & {root, (root + 7) % 12} else 1.0


def _range_violation(pitch: Pitch, *, low: int, high: int) -> float:
    number = float(pitch.midi_number)
    if number < low:
        return low - number
    if number > high:
        return number - high
    return 0.0


def _leap_cost(previous: Pitch | None, current: Pitch | None) -> float:
    if previous is None or current is None:
        return 0.0
    distance = float(abs(current.midi_number - previous.midi_number))
    return (distance / 12) ** 2


def _spacing_cost(violin1: Pitch, violin2: Pitch | None) -> float:
    if violin2 is None:
        return 0.0
    distance = float(abs(violin1.midi_number - violin2.midi_number))
    if distance == 0:
        return 2.0
    if distance <= 2:
        return 1.0
    if distance > 24:
        return (distance - 24) / 12
    return 0.0


def _span_bonus(
    selected: tuple[Pitch, ...],
    active_span_pitches: tuple[Fraction, ...],
) -> float:
    selected_numbers = {pitch.midi_number for pitch in selected}
    return float(
        sum(pitch_number in selected_numbers for pitch_number in active_span_pitches)
    )


def _active_span_pitches(
    slices: tuple[HarmonicSlice, ...],
    spans: tuple[SustainSpan, ...],
) -> tuple[tuple[Fraction, ...], ...]:
    """Index active sustain pitches once instead of scanning every span per path."""

    ordered_spans = sorted(spans, key=lambda span: span.start)
    active: list[tuple[Fraction, int, Fraction]] = []
    pitch_counts: Counter[Fraction] = Counter()
    next_span = 0
    result: list[tuple[Fraction, ...]] = []
    for harmonic_slice in slices:
        while (
            next_span < len(ordered_spans)
            and ordered_spans[next_span].start <= harmonic_slice.start
        ):
            span = ordered_spans[next_span]
            pitch_number = span.pitch.midi_number
            heapq.heappush(active, (span.end, next_span, pitch_number))
            pitch_counts[pitch_number] += 1
            next_span += 1
        while active and active[0][0] <= harmonic_slice.start:
            _, _, pitch_number = heapq.heappop(active)
            pitch_counts[pitch_number] -= 1
            if pitch_counts[pitch_number] == 0:
                del pitch_counts[pitch_number]
        result.append(tuple(pitch_counts.elements()))
    return tuple(result)


def _octave_candidates(note: NoteEvent, *, low: int, high: int) -> tuple[_Candidate, ...]:
    shifts = (0, -1, 1, -2, 2, -3, 3, -4, 4)
    candidates = []
    for shift in shifts:
        pitch = Pitch(note.pitch.step, note.pitch.octave + shift, note.pitch.alter)
        if low <= pitch.midi_number <= high:
            candidates.append(_Candidate(pitch=pitch, source_note=note))
    return tuple(candidates)


def _melody_candidates(note: NoteEvent, *, low: int, high: int) -> tuple[_Candidate, ...]:
    if low <= note.pitch.midi_number <= high:
        return (_Candidate(note.pitch, note),)
    return _octave_candidates(note, low=low, high=high)


def _support_candidates(
    harmonic_slice: HarmonicSlice,
    violin1: Pitch | None,
    *,
    melody_voice: int | None,
    ranges: ViolinRanges,
) -> tuple[_Candidate | None, ...]:
    if violin1 is None:
        candidates = _deduplicate_candidates(
            candidate
            for note in harmonic_slice.active_notes
            for candidate in _octave_candidates(
                note,
                low=ranges.violin2_low,
                high=ranges.violin2_high,
            )
        )
        return (*candidates, None)
    melody_notes = [
        note
        for note in harmonic_slice.active_notes
        if note.source_voice == melody_voice
    ]
    source_v1 = (
        max(
            melody_notes or harmonic_slice.active_notes,
            key=lambda note: note.pitch.midi_number,
        )
        if harmonic_slice.active_notes
        else None
    )
    support_notes = [
        note
        for note in harmonic_slice.active_notes
        if note is not source_v1
    ]
    candidates = _deduplicate_candidates(
        candidate
        for note in support_notes
        for candidate in _octave_candidates(
            note,
            low=ranges.violin2_low,
            high=ranges.violin2_high,
        )
        if candidate.pitch.midi_number != violin1.midi_number
    )
    return (*candidates, None)


def _prune_support_candidates(
    candidates: tuple[_Candidate | None, ...],
    harmonic_slice: HarmonicSlice,
    violin1: Pitch | None,
    *,
    baseline_pitch: Pitch | None,
    active_span_pitches: tuple[Fraction, ...],
    weights: AssignmentWeights,
    ranges: ViolinRanges,
    limit: int,
) -> tuple[_Candidate | None, ...]:
    if len(candidates) <= limit:
        return candidates
    scored = []
    for candidate in candidates:
        pitch = candidate.pitch if candidate is not None else None
        if violin1 is None:
            cost = _solo_support_cost(
                harmonic_slice,
                pitch,
                previous=None,
                active_span_pitches=active_span_pitches,
                weights=weights,
                ranges=ranges,
            )["total"]
        elif pitch is None:
            cost = _cost(
                harmonic_slice,
                violin1,
                None,
                previous_v1=None,
                previous_v2=None,
                active_span_pitches=active_span_pitches,
                weights=weights,
                ranges=ranges,
            )["total"]
        else:
            cost = _cost(
                harmonic_slice,
                violin1,
                pitch,
                previous_v1=None,
                previous_v2=None,
                active_span_pitches=active_span_pitches,
                weights=weights,
                ranges=ranges,
            )["total"]
        scored.append((cost, candidate))
    selected = [candidate for _, candidate in sorted(scored, key=lambda item: item[0])[:limit]]
    if baseline_pitch is not None and not any(
        candidate is not None and candidate.pitch == baseline_pitch
        for candidate in selected
    ):
        baseline_candidate = next(
            (
                candidate
                for candidate in candidates
                if candidate is not None and candidate.pitch == baseline_pitch
            ),
            None,
        )
        if baseline_candidate is not None:
            selected[-1] = baseline_candidate
    return tuple(selected)


def _deduplicate_candidates(candidates) -> tuple[_Candidate, ...]:
    result: list[_Candidate] = []
    seen: set[Fraction] = set()
    for candidate in candidates:
        if candidate.pitch.midi_number in seen:
            continue
        seen.add(candidate.pitch.midi_number)
        result.append(candidate)
    return tuple(result)


def _previous_violin1_pitches(
    assignments: tuple[SliceAssignment, ...],
) -> tuple[Pitch | None, ...]:
    result = []
    previous = None
    for assignment in assignments:
        result.append(previous)
        if assignment.violin1_pitch is not None:
            previous = assignment.violin1_pitch
    return tuple(result)


def _phrase_ranges(
    slices: tuple[HarmonicSlice, ...],
    phrase_duration: Fraction,
) -> tuple[tuple[int, int], ...]:
    if not slices:
        return ()
    result = []
    start_index = 0
    start_onset = slices[0].start
    for index, harmonic_slice in enumerate(slices):
        reaches_limit = harmonic_slice.end - start_onset >= phrase_duration
        ends_at_cadence = harmonic_slice.is_cadence
        followed_by_silence = (
            index + 1 < len(slices) and not slices[index + 1].active_notes
        )
        if reaches_limit or ends_at_cadence or followed_by_silence:
            result.append((start_index, index + 1))
            start_index = index + 1
            if start_index < len(slices):
                start_onset = slices[start_index].start
    if start_index < len(slices):
        result.append((start_index, len(slices)))
    return tuple(result)


def _measure_at(score: ScoreIR, onset: Fraction) -> tuple[int, str]:
    for measure in score.measures:
        if measure.start <= onset < measure.end:
            return measure.index, measure.number
    if score.measures:
        measure = score.measures[-1]
        return measure.index, measure.number
    return 0, "?"


def _melody_source_voice(score: ScoreIR) -> int | None:
    if not score.parts:
        return None
    first_part = score.parts[0].id
    voices = [voice.id for voice in score.source_voices if voice.part_id == first_part]
    return min(voices, default=None)


def _support_origin(pitch: Pitch, harmonic_slice: HarmonicSlice) -> AssignedOrigin:
    if harmonic_slice.chord is not None:
        pitch_number = _pitch_class_number(pitch.pitch_class.semitone)
        if pitch_number is not None and pitch_number not in chord_pitch_classes(
            harmonic_slice.chord
        ):
            return AssignedOrigin.TENSION_SELECTION
    return AssignedOrigin.BASS_SELECTION


def _origin_for_pitch(
    pitch: Pitch,
    harmonic_slice: HarmonicSlice,
    spans: tuple[SustainSpan, ...],
    *,
    default: AssignedOrigin,
) -> AssignedOrigin:
    for span in spans:
        if (
            span.pitch.midi_number == pitch.midi_number
            and span.start <= harmonic_slice.start < span.end
            and span.span_type in {SpanType.SUSPENSION, SpanType.PEDAL}
        ):
            return AssignedOrigin.SPAN_CONTINUATION
    return default


def _merge_assignments(assignments: list[SliceAssignment]) -> tuple[AssignedNote, ...]:
    merged: list[AssignedNote] = []
    for voice in (AssignedVoice.VIOLIN_1, AssignedVoice.VIOLIN_2):
        voice_notes: list[AssignedNote] = []
        for assignment in assignments:
            if voice is AssignedVoice.VIOLIN_1:
                pitch = assignment.violin1_pitch
                origin = assignment.violin1_origin
            else:
                pitch = assignment.violin2_pitch
                origin = assignment.violin2_origin
            if pitch is None or origin is None:
                continue
            if (
                voice_notes
                and voice_notes[-1].pitch == pitch
                and voice_notes[-1].origin is origin
                and voice_notes[-1].end == assignment.start
            ):
                previous = voice_notes[-1]
                voice_notes[-1] = AssignedNote(
                    voice=voice,
                    pitch=pitch,
                    onset=previous.onset,
                    duration=assignment.end - previous.onset,
                    origin=origin,
                    cost_breakdown=previous.cost_breakdown,
                )
            else:
                voice_notes.append(
                    AssignedNote(
                        voice=voice,
                        pitch=pitch,
                        onset=assignment.start,
                        duration=assignment.end - assignment.start,
                        origin=origin,
                        cost_breakdown=assignment.cost_breakdown,
                    )
                )
        merged.extend(voice_notes)
    return tuple(sorted(merged, key=lambda note: (note.onset, note.voice.value)))


def _pitch_class_number(value: Fraction) -> int | None:
    normalized = value % 12
    if normalized.denominator != 1:
        return None
    return int(normalized)
