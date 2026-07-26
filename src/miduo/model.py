"""Core score intermediate representation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction

_STEP_TO_SEMITONE = {
    "C": 0,
    "D": 2,
    "E": 4,
    "F": 5,
    "G": 7,
    "A": 9,
    "B": 11,
}


@dataclass(frozen=True, slots=True)
class PitchClass:
    """A diatonic pitch class with an exact chromatic alteration."""

    step: str
    alter: Fraction = Fraction(0)

    def __post_init__(self) -> None:
        normalized_step = self.step.upper()
        if normalized_step not in _STEP_TO_SEMITONE:
            raise ValueError(f"invalid pitch step: {self.step}")
        object.__setattr__(self, "step", normalized_step)

    @property
    def semitone(self) -> Fraction:
        return Fraction(_STEP_TO_SEMITONE[self.step]) + self.alter

    def __str__(self) -> str:
        if self.alter == 0:
            accidental = ""
        elif self.alter == 1:
            accidental = "#"
        elif self.alter == -1:
            accidental = "b"
        else:
            sign = "+" if self.alter > 0 else ""
            accidental = f"({sign}{self.alter})"
        return f"{self.step}{accidental}"


@dataclass(frozen=True, slots=True)
class Pitch:
    """A written MusicXML pitch."""

    step: str
    octave: int
    alter: Fraction = Fraction(0)

    def __post_init__(self) -> None:
        pitch_class = PitchClass(self.step, self.alter)
        object.__setattr__(self, "step", pitch_class.step)

    @property
    def pitch_class(self) -> PitchClass:
        return PitchClass(self.step, self.alter)

    @property
    def midi_number(self) -> Fraction:
        """MIDI note number, preserving microtonal alterations."""

        return Fraction(12 * (self.octave + 1)) + self.pitch_class.semitone

    def __str__(self) -> str:
        return f"{self.pitch_class}{self.octave}"


@dataclass(frozen=True, slots=True)
class NoteEvent:
    """One pitched, non-grace note in quarter-note time units."""

    pitch: Pitch
    onset: Fraction
    duration: Fraction
    source_voice: int
    tie_prev: bool = False
    tie_next: bool = False

    @property
    def end(self) -> Fraction:
        return self.onset + self.duration


@dataclass(frozen=True, slots=True)
class PartInfo:
    id: str
    name: str


@dataclass(frozen=True, slots=True)
class SourceVoice:
    """Mapping from a stable IR voice number back to MusicXML."""

    id: int
    part_id: str
    voice: str
    staff: str | None


@dataclass(frozen=True, slots=True)
class TimeSignature:
    onset: Fraction
    beats: int
    beat_type: int

    @property
    def measure_duration(self) -> Fraction:
        return Fraction(self.beats * 4, self.beat_type)


@dataclass(frozen=True, slots=True)
class KeySignature:
    onset: Fraction
    fifths: int
    mode: str | None


@dataclass(frozen=True, slots=True)
class MeasureInfo:
    index: int
    number: str
    start: Fraction
    end: Fraction


class ChordQuality(StrEnum):
    MAJOR = "maj"
    MINOR = "min"
    DOMINANT_SEVENTH = "dom7"
    MAJOR_SEVENTH = "maj7"
    MINOR_SEVENTH = "min7"
    DIMINISHED_SEVENTH = "dim7"
    HALF_DIMINISHED_SEVENTH = "halfdim7"
    AUGMENTED = "aug"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class ChordLabel:
    root: PitchClass
    quality: ChordQuality
    tensions_present: frozenset[str]
    bass: PitchClass
    confidence: float
    analysis_backend: str = "internal"
    key: str | None = None
    roman_numeral: str | None = None

    @property
    def symbol(self) -> str:
        return f"{self.root}:{self.quality.value}"


@dataclass(frozen=True, slots=True)
class HarmonicSlice:
    start: Fraction
    end: Fraction
    active_notes: tuple[NoteEvent, ...]
    chord: ChordLabel | None
    beat_weight: float
    is_cadence: bool = False

    @property
    def duration(self) -> Fraction:
        return self.end - self.start


class SpanType(StrEnum):
    PEDAL = "pedal"
    SUSPENSION = "suspension"
    PLAIN_SUSTAIN = "plain_sustain"


@dataclass(frozen=True, slots=True)
class SustainSpan:
    pitch: Pitch
    start: Fraction
    end: Fraction
    span_type: SpanType
    resolves_to: PitchClass | None
    source_voice: int

    @property
    def duration(self) -> Fraction:
        return self.end - self.start


class AssignedVoice(StrEnum):
    VIOLIN_1 = "violin1"
    VIOLIN_2 = "violin2"


class AssignedOrigin(StrEnum):
    MELODY = "melody"
    TENSION_SELECTION = "tension_selection"
    BASS_SELECTION = "bass_selection"
    SPAN_CONTINUATION = "span_continuation"
    RHYTHM_REDUCTION = "rhythm_reduction"


@dataclass(frozen=True, slots=True)
class AssignedNote:
    voice: AssignedVoice
    pitch: Pitch
    onset: Fraction
    duration: Fraction
    origin: AssignedOrigin
    cost_breakdown: dict[str, float]

    @property
    def end(self) -> Fraction:
        return self.onset + self.duration


@dataclass(frozen=True, slots=True)
class SliceAssignment:
    start: Fraction
    end: Fraction
    violin1_pitch: Pitch | None
    violin2_pitch: Pitch | None
    violin1_origin: AssignedOrigin | None
    violin2_origin: AssignedOrigin | None
    cost_breakdown: dict[str, float]


@dataclass(frozen=True, slots=True)
class AssignmentResult:
    slices: tuple[SliceAssignment, ...]
    notes: tuple[AssignedNote, ...]


@dataclass(frozen=True, slots=True)
class RhythmReductionResult:
    assignment: AssignmentResult
    reduced_beat_count: int
    removed_attack_count: int


class ValidationIssueType(StrEnum):
    RANGE_VIOLATION = "range_violation"
    POLYPHONY = "polyphony"
    VOICE_CROSSING = "voice_crossing"


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    issue_type: ValidationIssueType
    onset: Fraction
    voice: AssignedVoice | None
    message: str


@dataclass(frozen=True, slots=True)
class ValidationResult:
    assignment: AssignmentResult
    issues: tuple[ValidationIssue, ...]
    retry_count: int

    @property
    def is_valid(self) -> bool:
        return not self.issues


@dataclass(frozen=True, slots=True)
class ScoreIR:
    """Parsed subset of a score needed by downstream analysis stages."""

    title: str | None
    parts: tuple[PartInfo, ...]
    source_voices: tuple[SourceVoice, ...]
    notes: tuple[NoteEvent, ...]
    time_signatures: tuple[TimeSignature, ...]
    duration: Fraction
    key_signatures: tuple[KeySignature, ...] = ()
    measures: tuple[MeasureInfo, ...] = ()
