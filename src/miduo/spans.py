"""Detection and classification of sustained-note spans."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from miduo.harmony import chord_pitch_classes
from miduo.model import (
    HarmonicSlice,
    NoteEvent,
    Pitch,
    PitchClass,
    ScoreIR,
    SpanType,
    SustainSpan,
)


@dataclass(frozen=True, slots=True)
class _SpanCandidate:
    pitch: Pitch
    start: Fraction
    end: Fraction
    source_voice: int
    tied: bool

    @property
    def duration(self) -> Fraction:
        return self.end - self.start


def detect_sustain_spans(
    score: ScoreIR,
    slices: tuple[HarmonicSlice, ...],
    *,
    long_sustain_duration: Fraction = Fraction(3),
    pedal_min_duration: Fraction = Fraction(8),
) -> tuple[SustainSpan, ...]:
    """Detect contiguous same-pitch spans and classify their harmonic role."""

    candidates = _build_candidates(
        score.notes,
        long_sustain_duration=long_sustain_duration,
    )
    spans: list[SustainSpan] = []
    for candidate in candidates:
        span = _classify_candidate(
            candidate,
            score=score,
            slices=slices,
            pedal_min_duration=pedal_min_duration,
        )
        if (
            span.span_type is SpanType.SUSPENSION
            or candidate.tied
            or candidate.duration >= long_sustain_duration
        ):
            spans.append(span)
    return tuple(
        sorted(
            spans,
            key=lambda span: (span.start, span.source_voice, span.pitch.midi_number),
        )
    )


def _build_candidates(
    notes: tuple[NoteEvent, ...],
    *,
    long_sustain_duration: Fraction,
) -> tuple[_SpanCandidate, ...]:
    grouped: dict[tuple[int, Fraction], list[NoteEvent]] = {}
    for note in notes:
        grouped.setdefault((note.source_voice, note.pitch.midi_number), []).append(note)

    candidates: list[_SpanCandidate] = []
    for (source_voice, _), pitch_notes in grouped.items():
        ordered = sorted(pitch_notes, key=lambda note: (note.onset, note.end))
        chain: list[NoteEvent] = []
        for note in ordered:
            if chain and note.onset != chain[-1].end:
                _append_candidate(
                    candidates,
                    chain,
                    source_voice=source_voice,
                    long_sustain_duration=long_sustain_duration,
                )
                chain = []
            chain.append(note)
        _append_candidate(
            candidates,
            chain,
            source_voice=source_voice,
            long_sustain_duration=long_sustain_duration,
        )
    return tuple(candidates)


def _append_candidate(
    candidates: list[_SpanCandidate],
    chain: list[NoteEvent],
    *,
    source_voice: int,
    long_sustain_duration: Fraction,
) -> None:
    if not chain:
        return
    start = chain[0].onset
    end = chain[-1].end
    tied = any(note.tie_prev or note.tie_next for note in chain)
    if len(chain) < 2 and not tied and end - start < long_sustain_duration:
        return
    candidates.append(
        _SpanCandidate(
            pitch=chain[0].pitch,
            start=start,
            end=end,
            source_voice=source_voice,
            tied=tied,
        )
    )


def _classify_candidate(
    candidate: _SpanCandidate,
    *,
    score: ScoreIR,
    slices: tuple[HarmonicSlice, ...],
    pedal_min_duration: Fraction,
) -> SustainSpan:
    relevant_slices = tuple(
        harmonic_slice
        for harmonic_slice in slices
        if harmonic_slice.start < candidate.end and harmonic_slice.end > candidate.start
    )
    resolution = _find_resolution(candidate, score.notes)
    pitch_number = _integer_pitch_class(candidate.pitch.pitch_class)

    starts_as_chord_tone = False
    becomes_non_chord_tone = False
    if pitch_number is not None:
        for harmonic_slice in relevant_slices:
            if harmonic_slice.chord is None:
                continue
            is_chord_tone = pitch_number in chord_pitch_classes(harmonic_slice.chord)
            if harmonic_slice.start <= candidate.start < harmonic_slice.end:
                starts_as_chord_tone = is_chord_tone
            elif harmonic_slice.start > candidate.start and not is_chord_tone:
                becomes_non_chord_tone = True

    if starts_as_chord_tone and becomes_non_chord_tone and resolution is not None:
        span_type = SpanType.SUSPENSION
    elif (
        candidate.duration >= pedal_min_duration
        and pitch_number is not None
        and _is_root_or_fifth_in_most_slices(pitch_number, relevant_slices)
    ):
        span_type = SpanType.PEDAL
    else:
        span_type = SpanType.PLAIN_SUSTAIN

    return SustainSpan(
        pitch=candidate.pitch,
        start=candidate.start,
        end=candidate.end,
        span_type=span_type,
        resolves_to=resolution if span_type is SpanType.SUSPENSION else None,
        source_voice=candidate.source_voice,
    )


def _find_resolution(
    candidate: _SpanCandidate,
    notes: tuple[NoteEvent, ...],
) -> PitchClass | None:
    following = (
        note
        for note in notes
        if note.source_voice == candidate.source_voice
        and note.onset == candidate.end
        and note.pitch.midi_number != candidate.pitch.midi_number
    )
    for note in sorted(
        following,
        key=lambda item: abs(item.pitch.midi_number - candidate.pitch.midi_number),
    ):
        distance = abs(note.pitch.midi_number - candidate.pitch.midi_number)
        if distance in {1, 2}:
            return note.pitch.pitch_class
    return None


def _is_root_or_fifth_in_most_slices(
    pitch_number: int,
    slices: tuple[HarmonicSlice, ...],
) -> bool:
    labeled = [harmonic_slice for harmonic_slice in slices if harmonic_slice.chord is not None]
    if not labeled:
        return False
    matches = 0
    for harmonic_slice in labeled:
        assert harmonic_slice.chord is not None
        root = _integer_pitch_class(harmonic_slice.chord.root)
        if root is not None and pitch_number in {root, (root + 7) % 12}:
            matches += 1
    return matches / len(labeled) >= 0.75


def _integer_pitch_class(pitch_class: PitchClass) -> int | None:
    normalized = pitch_class.semitone % 12
    if normalized.denominator != 1:
        return None
    return int(normalized)
