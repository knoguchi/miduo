"""Hard-constraint validation and bounded reassignment feedback."""

from __future__ import annotations

from dataclasses import replace
from fractions import Fraction

from miduo.assignment import AssignmentWeights, ViolinRanges, assign_voices
from miduo.model import (
    AssignedNote,
    AssignedVoice,
    AssignmentResult,
    HarmonicSlice,
    Pitch,
    ScoreIR,
    SustainSpan,
    ValidationIssue,
    ValidationIssueType,
    ValidationResult,
)
from miduo.rhythm import reduce_violin2_rhythm


def validate_assignment(
    assignment: AssignmentResult,
    *,
    ranges: ViolinRanges = ViolinRanges(),
) -> tuple[ValidationIssue, ...]:
    """Return all hard-constraint violations in an assignment."""

    issues = [
        *_range_issues(assignment.notes, ranges),
        *_polyphony_issues(assignment.notes),
        *_crossing_issues(assignment.notes),
    ]
    return tuple(
        sorted(
            issues,
            key=lambda issue: (
                issue.onset,
                issue.issue_type.value,
                issue.voice.value if issue.voice else "",
            ),
        )
    )


def validate_with_retries(
    score: ScoreIR,
    slices: tuple[HarmonicSlice, ...],
    spans: tuple[SustainSpan, ...],
    assignment: AssignmentResult,
    *,
    max_retries: int = 3,
    attack_threshold: float = 2.0,
    weights: AssignmentWeights = AssignmentWeights(),
    ranges: ViolinRanges = ViolinRanges(),
) -> ValidationResult:
    """Validate and retry assignment with stronger hard-constraint penalties."""

    if max_retries < 0:
        raise ValueError("max_retries must not be negative")

    current = assignment
    issues = validate_assignment(current, ranges=ranges)
    if not issues:
        return ValidationResult(assignment=current, issues=(), retry_count=0)

    for retry in range(1, max_retries + 1):
        multiplier = float(10**retry)
        retry_weights = replace(
            weights,
            range_violation=weights.range_violation * multiplier,
            voice_crossing=weights.voice_crossing * multiplier,
        )
        reassigned = assign_voices(
            score,
            slices,
            spans,
            weights=retry_weights,
            ranges=ranges,
        )
        current = reduce_violin2_rhythm(
            score,
            reassigned,
            slices,
            attack_threshold=attack_threshold,
        ).assignment
        current = _lower_crossing_support_notes(current, ranges=ranges)
        issues = validate_assignment(current, ranges=ranges)
        if not issues:
            return ValidationResult(
                assignment=current,
                issues=(),
                retry_count=retry,
            )

    return ValidationResult(
        assignment=current,
        issues=issues,
        retry_count=max_retries,
    )


def _range_issues(
    notes: tuple[AssignedNote, ...],
    ranges: ViolinRanges,
) -> list[ValidationIssue]:
    result = []
    for note in notes:
        if note.voice is AssignedVoice.VIOLIN_1:
            low, high = ranges.violin1_low, ranges.violin1_high
        else:
            low, high = ranges.violin2_low, ranges.violin2_high
        if not low <= note.pitch.midi_number <= high:
            result.append(
                ValidationIssue(
                    issue_type=ValidationIssueType.RANGE_VIOLATION,
                    onset=note.onset,
                    voice=note.voice,
                    message=(
                        f"{note.voice.value} pitch {note.pitch} is outside "
                        f"MIDI range {low}–{high}"
                    ),
                )
            )
    return result


def _polyphony_issues(notes: tuple[AssignedNote, ...]) -> list[ValidationIssue]:
    result = []
    for voice in AssignedVoice:
        voice_notes = sorted(
            (note for note in notes if note.voice is voice),
            key=lambda note: (note.onset, note.end),
        )
        previous: AssignedNote | None = None
        for note in voice_notes:
            if previous is not None and note.onset < previous.end:
                result.append(
                    ValidationIssue(
                        issue_type=ValidationIssueType.POLYPHONY,
                        onset=note.onset,
                        voice=voice,
                        message=(
                            f"{voice.value} notes {previous.pitch} and {note.pitch} overlap"
                        ),
                    )
                )
                if note.end > previous.end:
                    previous = note
            else:
                previous = note
    return result


def _crossing_issues(notes: tuple[AssignedNote, ...]) -> list[ValidationIssue]:
    violin1 = tuple(note for note in notes if note.voice is AssignedVoice.VIOLIN_1)
    violin2 = tuple(note for note in notes if note.voice is AssignedVoice.VIOLIN_2)
    boundaries = sorted(
        {
            boundary
            for note in (*violin1, *violin2)
            for boundary in (note.onset, note.end)
        }
    )
    result = []
    crossing_active = False
    for start, end in zip(boundaries, boundaries[1:], strict=False):
        if end <= start:
            continue
        active_v1 = _active_note(violin1, start)
        active_v2 = _active_note(violin2, start)
        crossing = (
            active_v1 is not None
            and active_v2 is not None
            and active_v1.pitch.midi_number < active_v2.pitch.midi_number
        )
        if crossing and not crossing_active:
            assert active_v1 is not None and active_v2 is not None
            result.append(
                ValidationIssue(
                    issue_type=ValidationIssueType.VOICE_CROSSING,
                    onset=start,
                    voice=None,
                    message=(
                        f"violin1 {active_v1.pitch} is below violin2 {active_v2.pitch}"
                    ),
                )
            )
        crossing_active = crossing
    return result


def _active_note(notes: tuple[AssignedNote, ...], onset: Fraction) -> AssignedNote | None:
    return next((note for note in notes if note.onset <= onset < note.end), None)


def _lower_crossing_support_notes(
    assignment: AssignmentResult,
    *,
    ranges: ViolinRanges,
) -> AssignmentResult:
    violin1 = tuple(
        note for note in assignment.notes if note.voice is AssignedVoice.VIOLIN_1
    )
    repaired: list[AssignedNote] = []
    for note in assignment.notes:
        if note.voice is not AssignedVoice.VIOLIN_2:
            repaired.append(note)
            continue
        overlapping_melody = [
            melody
            for melody in violin1
            if melody.onset < note.end and melody.end > note.onset
        ]
        if not overlapping_melody:
            repaired.append(note)
            continue
        ceiling = min(melody.pitch.midi_number for melody in overlapping_melody)
        pitch = note.pitch
        octave_shifts = 0
        while pitch.midi_number > ceiling:
            candidate = Pitch(pitch.step, pitch.octave - 1, pitch.alter)
            if candidate.midi_number < ranges.violin2_low:
                break
            pitch = candidate
            octave_shifts += 1
        if octave_shifts:
            cost_breakdown = dict(note.cost_breakdown)
            cost_breakdown["validation_octave_shifts"] = float(octave_shifts)
            note = replace(
                note,
                pitch=pitch,
                cost_breakdown=cost_breakdown,
            )
        repaired.append(note)
    return AssignmentResult(
        slices=assignment.slices,
        notes=tuple(repaired),
    )
