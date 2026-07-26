"""Rhythm reduction for dense Violin 2 passages."""

from __future__ import annotations

from dataclasses import replace
from fractions import Fraction

from miduo.model import (
    AssignedNote,
    AssignedOrigin,
    AssignedVoice,
    AssignmentResult,
    HarmonicSlice,
    RhythmReductionResult,
    ScoreIR,
    TimeSignature,
)


def reduce_violin2_rhythm(
    score: ScoreIR,
    assignment: AssignmentResult,
    slices: tuple[HarmonicSlice, ...],
    *,
    attack_threshold: float = 2.0,
) -> RhythmReductionResult:
    """Reduce Violin 2 attacks in beats whose density exceeds the threshold."""

    if attack_threshold < 0:
        raise ValueError("attack_threshold must not be negative")

    violin1 = [note for note in assignment.notes if note.voice is AssignedVoice.VIOLIN_1]
    violin2 = [note for note in assignment.notes if note.voice is AssignedVoice.VIOLIN_2]
    beat_groups: dict[tuple[Fraction, Fraction], list[int]] = {}
    for index, note in enumerate(violin2):
        beat_start, beat_end = _beat_window(note.onset, score.time_signatures)
        beat_groups.setdefault((beat_start, beat_end), []).append(index)

    replacements: dict[int, AssignedNote] = {}
    removed: set[int] = set()
    reduced_beats = 0

    for (beat_start, beat_end), indices in beat_groups.items():
        if len(indices) <= attack_threshold:
            continue
        reduced_beats += 1
        primary = max(
            indices,
            key=lambda index: (
                _weight_at(violin2[index].onset, slices),
                -(violin2[index].onset - beat_start),
            ),
        )
        keep = {primary}

        ordered_keep = sorted(keep, key=lambda index: violin2[index].onset)
        group_end = beat_end
        removed_in_beat = len(indices) - len(keep)
        for position, index in enumerate(ordered_keep):
            next_onset = (
                violin2[ordered_keep[position + 1]].onset
                if position + 1 < len(ordered_keep)
                else group_end
            )
            original = violin2[index]
            cost_breakdown = dict(original.cost_breakdown)
            cost_breakdown["rhythm_reduction_removed_attacks"] = float(removed_in_beat)
            replacements[index] = replace(
                original,
                duration=max(original.duration, next_onset - original.onset),
                origin=AssignedOrigin.RHYTHM_REDUCTION,
                cost_breakdown=cost_breakdown,
            )
        removed.update(set(indices) - keep)

    reduced_violin2 = [
        replacements.get(index, note)
        for index, note in enumerate(violin2)
        if index not in removed
    ]
    notes = tuple(
        sorted(
            (*violin1, *reduced_violin2),
            key=lambda note: (note.onset, note.voice.value),
        )
    )
    reduced_assignment = AssignmentResult(slices=assignment.slices, notes=notes)
    return RhythmReductionResult(
        assignment=reduced_assignment,
        reduced_beat_count=reduced_beats,
        removed_attack_count=len(removed),
    )


def _beat_window(
    onset: Fraction,
    signatures: tuple[TimeSignature, ...],
) -> tuple[Fraction, Fraction]:
    signature = _active_time_signature(onset, signatures)
    if signature is None:
        signature_start = Fraction(0)
        beat_length = Fraction(1)
    else:
        signature_start = signature.onset
        beat_length = Fraction(4, signature.beat_type)
    beat_index = (onset - signature_start) // beat_length
    start = signature_start + beat_index * beat_length
    return start, start + beat_length


def _active_time_signature(
    onset: Fraction,
    signatures: tuple[TimeSignature, ...],
) -> TimeSignature | None:
    active = None
    for signature in signatures:
        if signature.onset > onset:
            break
        active = signature
    return active


def _weight_at(onset: Fraction, slices: tuple[HarmonicSlice, ...]) -> float:
    for harmonic_slice in slices:
        if harmonic_slice.start <= onset < harmonic_slice.end:
            return harmonic_slice.beat_weight
    return 0.0
