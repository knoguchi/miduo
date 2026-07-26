"""MusicXML and MuseScore output writer."""

from __future__ import annotations

from dataclasses import dataclass, replace
from fractions import Fraction
from math import lcm
from pathlib import Path
from tempfile import TemporaryDirectory
from xml.etree import ElementTree
from zipfile import ZIP_DEFLATED, ZipFile

from miduo.errors import UnsupportedFormatError
from miduo.model import (
    AssignedNote,
    AssignedVoice,
    AssignmentResult,
    KeySignature,
    ScoreIR,
    TimeSignature,
)
from miduo.musescore import MuseScoreConverter


@dataclass(frozen=True, slots=True)
class _MeasureWindow:
    number: int
    start: Fraction
    end: Fraction
    time_signature: TimeSignature
    key_signature: KeySignature | None
    attributes_changed: bool


@dataclass(frozen=True, slots=True)
class _RhythmValue:
    duration: Fraction
    note_type: str
    dots: int = 0
    actual_notes: int | None = None
    normal_notes: int | None = None


_BASE_NOTE_TYPES = (
    (Fraction(8), "breve"),
    (Fraction(4), "whole"),
    (Fraction(2), "half"),
    (Fraction(1), "quarter"),
    (Fraction(1, 2), "eighth"),
    (Fraction(1, 4), "16th"),
    (Fraction(1, 8), "32nd"),
    (Fraction(1, 16), "64th"),
    (Fraction(1, 32), "128th"),
)


def _rhythm_values() -> tuple[_RhythmValue, ...]:
    result = []
    for base_duration, note_type in _BASE_NOTE_TYPES:
        for dots, dot_multiplier in (
            (0, Fraction(1)),
            (1, Fraction(3, 2)),
            (2, Fraction(7, 4)),
        ):
            result.append(
                _RhythmValue(
                    duration=base_duration * dot_multiplier,
                    note_type=note_type,
                    dots=dots,
                )
            )
            result.append(
                _RhythmValue(
                    duration=base_duration * dot_multiplier * Fraction(2, 3),
                    note_type=note_type,
                    dots=dots,
                    actual_notes=3,
                    normal_notes=2,
                )
            )
    return tuple(sorted(result, key=lambda value: value.duration, reverse=True))


_RHYTHM_VALUES = _rhythm_values()


def write_arrangement(
    score: ScoreIR,
    assignment: AssignmentResult,
    output_path: Path,
    *,
    musescore_executable: Path | None = None,
) -> Path:
    """Write a validated arrangement in the format selected by its suffix."""

    output = output_path.expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    suffix = output.suffix.lower()
    xml_bytes = build_musicxml(score, assignment)

    if suffix in {".musicxml", ".xml"}:
        output.write_bytes(xml_bytes)
    elif suffix == ".mxl":
        _write_mxl(xml_bytes, output)
    elif suffix == ".mscz":
        converter = MuseScoreConverter.discover(musescore_executable)
        with TemporaryDirectory(prefix="miduo-") as temporary_directory:
            musicxml_path = Path(temporary_directory) / "arrangement.musicxml"
            musicxml_path.write_bytes(xml_bytes)
            converter.convert_to_musescore(musicxml_path, output)
    else:
        raise UnsupportedFormatError(
            "output filename must end in .musicxml, .xml, .mxl, or .mscz"
        )
    return output


def build_musicxml(score: ScoreIR, assignment: AssignmentResult) -> bytes:
    """Build a complete two-part MusicXML document."""

    assignment = quantize_assignment_for_notation(score, assignment)
    divisions = _required_divisions(score, assignment.notes)
    root = ElementTree.Element("score-partwise", version="4.0")
    work = ElementTree.SubElement(root, "work")
    ElementTree.SubElement(work, "work-title").text = (
        f"{score.title} – Violin Duo" if score.title else "Violin Duo Arrangement"
    )
    identification = ElementTree.SubElement(root, "identification")
    encoding = ElementTree.SubElement(identification, "encoding")
    ElementTree.SubElement(encoding, "software").text = "miduo"

    part_list = ElementTree.SubElement(root, "part-list")
    for part_id, name in (("P1", "Violin 1"), ("P2", "Violin 2")):
        score_part = ElementTree.SubElement(part_list, "score-part", id=part_id)
        ElementTree.SubElement(score_part, "part-name").text = name
        instrument = ElementTree.SubElement(
            score_part,
            "score-instrument",
            id=f"{part_id}-I1",
        )
        ElementTree.SubElement(instrument, "instrument-name").text = "Violin"
        ElementTree.SubElement(instrument, "instrument-sound").text = (
            "strings.violin"
        )

    measures = _measure_windows(score)
    for part_id, voice in (
        ("P1", AssignedVoice.VIOLIN_1),
        ("P2", AssignedVoice.VIOLIN_2),
    ):
        part = ElementTree.SubElement(root, "part", id=part_id)
        notes = tuple(note for note in assignment.notes if note.voice is voice)
        for window in measures:
            measure = ElementTree.SubElement(
                part,
                "measure",
                number=str(window.number),
            )
            if window.number == 1 or window.attributes_changed:
                _write_attributes(measure, window, divisions)
            _write_measure_notes(measure, window, notes, divisions)

    ElementTree.indent(root, space="  ")
    return ElementTree.tostring(
        root,
        encoding="utf-8",
        xml_declaration=True,
    )


def quantize_assignment_for_notation(
    score: ScoreIR,
    assignment: AssignmentResult,
    *,
    grid: Fraction = Fraction(1, 4),
) -> AssignmentResult:
    """Snap output events to a readable sixteenth-note grid."""

    if grid <= 0:
        raise ValueError("notation grid must be positive")
    quantized = []
    for voice in AssignedVoice:
        notes = sorted(
            (note for note in assignment.notes if note.voice is voice),
            key=lambda note: note.onset,
        )
        snapped_starts = [_snap(note.onset, grid) for note in notes]
        for index, note in enumerate(notes):
            onset = max(Fraction(0), snapped_starts[index])
            end = min(score.duration, _snap(note.end, grid))
            if index + 1 < len(notes):
                end = min(end, snapped_starts[index + 1])
            if quantized and quantized[-1].voice is voice:
                onset = max(onset, quantized[-1].end)
            if end <= onset:
                continue
            cost_breakdown = dict(note.cost_breakdown)
            if onset != note.onset or end != note.end:
                cost_breakdown["notation_quantization"] = float(
                    abs(onset - note.onset) + abs(end - note.end)
                )
            quantized.append(
                replace(
                    note,
                    onset=onset,
                    duration=end - onset,
                    cost_breakdown=cost_breakdown,
                )
            )
    return AssignmentResult(
        slices=assignment.slices,
        notes=tuple(sorted(quantized, key=lambda note: (note.onset, note.voice.value))),
    )


def _snap(value: Fraction, grid: Fraction) -> Fraction:
    units = value / grid
    lower = units.numerator // units.denominator
    remainder = units - lower
    rounded = lower + (1 if remainder >= Fraction(1, 2) else 0)
    return rounded * grid


def _write_attributes(
    measure: ElementTree.Element,
    window: _MeasureWindow,
    divisions: int,
) -> None:
    attributes = ElementTree.SubElement(measure, "attributes")
    ElementTree.SubElement(attributes, "divisions").text = str(divisions)
    if window.key_signature is not None:
        key = ElementTree.SubElement(attributes, "key")
        ElementTree.SubElement(key, "fifths").text = str(
            window.key_signature.fifths
        )
        if window.key_signature.mode:
            ElementTree.SubElement(key, "mode").text = window.key_signature.mode
    time = ElementTree.SubElement(attributes, "time")
    ElementTree.SubElement(time, "beats").text = str(window.time_signature.beats)
    ElementTree.SubElement(time, "beat-type").text = str(
        window.time_signature.beat_type
    )
    clef = ElementTree.SubElement(attributes, "clef")
    ElementTree.SubElement(clef, "sign").text = "G"
    ElementTree.SubElement(clef, "line").text = "2"


def _write_measure_notes(
    measure: ElementTree.Element,
    window: _MeasureWindow,
    notes: tuple[AssignedNote, ...],
    divisions: int,
) -> None:
    cursor = window.start
    for note in notes:
        if note.end <= window.start or note.onset >= window.end:
            continue
        segment_start = max(note.onset, window.start)
        segment_end = min(note.end, window.end)
        if segment_start > cursor:
            _write_rest(measure, segment_start - cursor, divisions)
        _write_pitched_segment(
            measure,
            note,
            duration=segment_end - segment_start,
            divisions=divisions,
            tie_stop=note.onset < segment_start,
            tie_start=note.end > segment_end,
        )
        cursor = segment_end
    if cursor < window.end:
        _write_rest(measure, window.end - cursor, divisions)


def _write_pitched_segment(
    measure: ElementTree.Element,
    assigned: AssignedNote,
    *,
    duration: Fraction,
    divisions: int,
    tie_stop: bool,
    tie_start: bool,
) -> None:
    rhythm_values = _decompose_duration(duration, divisions=divisions)
    for index, rhythm_value in enumerate(rhythm_values):
        _write_note(
            measure,
            assigned,
            rhythm_value=rhythm_value,
            divisions=divisions,
            tie_stop=tie_stop or index > 0,
            tie_start=tie_start or index + 1 < len(rhythm_values),
        )


def _write_note(
    measure: ElementTree.Element,
    assigned: AssignedNote,
    *,
    rhythm_value: _RhythmValue,
    divisions: int,
    tie_stop: bool,
    tie_start: bool,
) -> None:
    note = ElementTree.SubElement(measure, "note")
    pitch = ElementTree.SubElement(note, "pitch")
    ElementTree.SubElement(pitch, "step").text = assigned.pitch.step
    if assigned.pitch.alter:
        ElementTree.SubElement(pitch, "alter").text = _fraction_decimal(
            assigned.pitch.alter
        )
    ElementTree.SubElement(pitch, "octave").text = str(assigned.pitch.octave)
    ElementTree.SubElement(note, "duration").text = str(
        _duration_units(rhythm_value.duration, divisions)
    )
    if tie_stop:
        ElementTree.SubElement(note, "tie", type="stop")
    if tie_start:
        ElementTree.SubElement(note, "tie", type="start")
    ElementTree.SubElement(note, "voice").text = "1"
    _write_rhythm_type(note, rhythm_value)
    if tie_stop or tie_start:
        notations = ElementTree.SubElement(note, "notations")
        if tie_stop:
            ElementTree.SubElement(notations, "tied", type="stop")
        if tie_start:
            ElementTree.SubElement(notations, "tied", type="start")


def _write_rest(
    measure: ElementTree.Element,
    duration: Fraction,
    divisions: int,
) -> None:
    for rhythm_value in _decompose_duration(duration, divisions=divisions):
        note = ElementTree.SubElement(measure, "note")
        ElementTree.SubElement(note, "rest")
        ElementTree.SubElement(note, "duration").text = str(
            _duration_units(rhythm_value.duration, divisions)
        )
        ElementTree.SubElement(note, "voice").text = "1"
        _write_rhythm_type(note, rhythm_value)


def _write_rhythm_type(
    note: ElementTree.Element,
    rhythm_value: _RhythmValue,
) -> None:
    ElementTree.SubElement(note, "type").text = rhythm_value.note_type
    for _ in range(rhythm_value.dots):
        ElementTree.SubElement(note, "dot")
    if rhythm_value.actual_notes is not None and rhythm_value.normal_notes is not None:
        modification = ElementTree.SubElement(note, "time-modification")
        ElementTree.SubElement(modification, "actual-notes").text = str(
            rhythm_value.actual_notes
        )
        ElementTree.SubElement(modification, "normal-notes").text = str(
            rhythm_value.normal_notes
        )


def _decompose_duration(
    duration: Fraction,
    *,
    divisions: int,
) -> tuple[_RhythmValue, ...]:
    if duration <= 0:
        raise ValueError(f"duration must be positive: {duration}")
    remaining = duration
    result = []
    smallest = _RHYTHM_VALUES[-1].duration
    while remaining:
        value = next(
            (
                candidate
                for candidate in _RHYTHM_VALUES
                if candidate.duration <= remaining
                and (candidate.duration * divisions).denominator == 1
            ),
            None,
        )
        if value is None or remaining < smallest:
            raise ValueError(f"duration cannot be notated exactly: {duration}")
        result.append(value)
        remaining -= value.duration
    return tuple(result)


def _measure_windows(score: ScoreIR) -> tuple[_MeasureWindow, ...]:
    signatures = score.time_signatures or (
        TimeSignature(onset=Fraction(0), beats=4, beat_type=4),
    )
    keys = score.key_signatures
    windows: list[_MeasureWindow] = []
    start = Fraction(0)
    previous_signature: TimeSignature | None = None
    previous_key: KeySignature | None = None
    while start < score.duration:
        signature = _active_signature(start, signatures)
        key = _active_key(start, keys)
        next_change = min(
            (
                onset
                for onset in (
                    _next_onset(start, signatures),
                    _next_onset(start, keys),
                    score.duration,
                )
                if onset is not None and onset > start
            ),
            default=score.duration,
        )
        end = min(start + signature.measure_duration, next_change)
        windows.append(
            _MeasureWindow(
                number=len(windows) + 1,
                start=start,
                end=end,
                time_signature=signature,
                key_signature=key,
                attributes_changed=(
                    previous_signature != signature or previous_key != key
                ),
            )
        )
        previous_signature = signature
        previous_key = key
        start = end
    return tuple(windows)


def _active_signature(
    onset: Fraction,
    signatures: tuple[TimeSignature, ...],
) -> TimeSignature:
    active = signatures[0]
    for signature in signatures:
        if signature.onset > onset:
            break
        active = signature
    return active


def _active_key(
    onset: Fraction,
    keys: tuple[KeySignature, ...],
) -> KeySignature | None:
    active = None
    for key in keys:
        if key.onset > onset:
            break
        active = key
    return active


def _next_onset(start: Fraction, events) -> Fraction | None:
    return next((event.onset for event in events if event.onset > start), None)


def _required_divisions(
    score: ScoreIR,
    notes: tuple[AssignedNote, ...],
) -> int:
    values = [score.duration]
    values.extend(
        value
        for note in notes
        for value in (note.onset, note.duration, note.end)
    )
    values.extend(signature.onset for signature in score.time_signatures)
    return max(1, lcm(*(value.denominator for value in values)))


def _duration_units(duration: Fraction, divisions: int) -> int:
    units = duration * divisions
    if units.denominator != 1:
        raise ValueError(f"duration {duration} is not representable with divisions {divisions}")
    return units.numerator


def _fraction_decimal(value: Fraction) -> str:
    if value.denominator == 1:
        return str(value.numerator)
    return format(float(value), ".8g")


def _write_mxl(xml_bytes: bytes, output: Path) -> None:
    container = b"""<?xml version="1.0" encoding="UTF-8"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="score.musicxml"
      media-type="application/vnd.recordare.musicxml+xml"/>
  </rootfiles>
</container>
"""
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("score.musicxml", xml_bytes)
