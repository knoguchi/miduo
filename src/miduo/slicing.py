"""Build harmonic time slices from parsed note events."""

from __future__ import annotations

from fractions import Fraction

from miduo.model import HarmonicSlice, NoteEvent, ScoreIR, TimeSignature


def build_harmonic_slices(score: ScoreIR) -> tuple[HarmonicSlice, ...]:
    """Split a score at every note attack and release boundary."""

    boundaries = {Fraction(0), score.duration}
    starting: dict[Fraction, list[NoteEvent]] = {}
    ending: dict[Fraction, list[NoteEvent]] = {}
    for note in score.notes:
        boundaries.add(note.onset)
        boundaries.add(note.end)
        starting.setdefault(note.onset, []).append(note)
        ending.setdefault(note.end, []).append(note)

    ordered_boundaries = sorted(boundary for boundary in boundaries if boundary >= 0)
    if len(ordered_boundaries) < 2:
        return ()

    active: list[NoteEvent] = []
    slices: list[HarmonicSlice] = []
    for start, end in zip(ordered_boundaries, ordered_boundaries[1:], strict=False):
        for note in ending.get(start, ()):
            active.remove(note)
        active.extend(starting.get(start, ()))
        if end <= start:
            continue
        slices.append(
            HarmonicSlice(
                start=start,
                end=end,
                active_notes=tuple(
                    sorted(
                        active,
                        key=lambda note: (note.pitch.midi_number, note.source_voice, note.onset),
                    )
                ),
                chord=None,
                beat_weight=beat_weight_at(start, score.time_signatures),
            )
        )
    return tuple(slices)


def beat_weight_at(onset: Fraction, signatures: tuple[TimeSignature, ...]) -> float:
    """Return a metric weight using the time signature active at ``onset``."""

    signature = _active_time_signature(onset, signatures)
    signature_start = signature.onset if signature is not None else Fraction(0)
    position = onset - signature_start

    if signature is not None and signature.measure_duration > 0:
        position %= signature.measure_duration

    beat_length = Fraction(4, signature.beat_type) if signature is not None else Fraction(1)
    position_in_beats = position / beat_length
    if position_in_beats.denominator == 1:
        return 1.0
    if position_in_beats.denominator == 2:
        return 0.5
    return 0.25


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
