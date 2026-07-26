"""MusicXML to Score IR parser."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from xml.etree import ElementTree

from miduo.errors import InvalidScoreError
from miduo.io import read_score_xml
from miduo.model import (
    KeySignature,
    MeasureInfo,
    NoteEvent,
    PartInfo,
    Pitch,
    ScoreIR,
    SourceVoice,
    TimeSignature,
)


@dataclass(slots=True)
class _PartState:
    divisions: int | None = None
    time_signature: tuple[int, int] | None = None
    absolute_onset: Fraction = Fraction(0)


def parse_score(
    path: Path,
    *,
    musescore_executable: Path | None = None,
) -> ScoreIR:
    """Parse a partwise MusicXML or MuseScore file into the core Score IR."""

    loaded = read_score_xml(path, musescore_executable=musescore_executable)
    root = loaded.root
    if _local_name(root.tag) != "score-partwise":
        raise InvalidScoreError("Score IR parsing currently requires score-partwise MusicXML")

    title = _first_text(root, "work-title") or _first_text(root, "movement-title")
    part_names = _part_name_map(root)
    parts: list[PartInfo] = []
    notes: list[NoteEvent] = []
    time_signatures: set[TimeSignature] = set()
    key_signatures: set[KeySignature] = set()
    voice_ids: dict[tuple[str, str, str | None], int] = {}
    max_duration = Fraction(0)
    measures: list[MeasureInfo] = []

    for part_index, part_element in enumerate(_children(root, "part")):
        part_id = part_element.attrib.get("id", f"P{len(parts) + 1}")
        parts.append(PartInfo(id=part_id, name=part_names.get(part_id, part_id)))
        state = _PartState()

        for measure in _children(part_element, "measure"):
            measure_start = state.absolute_onset
            measure_notes, measure_duration, signatures, keys = _parse_measure(
                measure,
                part_id=part_id,
                state=state,
                voice_ids=voice_ids,
            )
            notes.extend(measure_notes)
            time_signatures.update(signatures)
            key_signatures.update(keys)
            state.absolute_onset += measure_duration
            if part_index == 0:
                measures.append(
                    MeasureInfo(
                        index=len(measures) + 1,
                        number=measure.attrib.get("number", str(len(measures) + 1)),
                        start=measure_start,
                        end=state.absolute_onset,
                    )
                )
        max_duration = max(max_duration, state.absolute_onset)

    source_voices = tuple(
        SourceVoice(id=voice_id, part_id=key[0], voice=key[1], staff=key[2])
        for key, voice_id in sorted(voice_ids.items(), key=lambda item: item[1])
    )
    notes.sort(key=lambda note: (note.onset, note.source_voice, note.pitch.midi_number))
    return ScoreIR(
        title=title,
        parts=tuple(parts),
        source_voices=source_voices,
        notes=tuple(notes),
        time_signatures=tuple(
            sorted(time_signatures, key=lambda signature: (signature.onset, signature.beats))
        ),
        duration=(
            max_duration
            if max_duration > 0
            else max((note.end for note in notes), default=Fraction(0))
        ),
        key_signatures=tuple(
            sorted(key_signatures, key=lambda signature: signature.onset)
        ),
        measures=tuple(measures),
    )


def _parse_measure(
    measure: ElementTree.Element,
    *,
    part_id: str,
    state: _PartState,
    voice_ids: dict[tuple[str, str, str | None], int],
) -> tuple[
    list[NoteEvent],
    Fraction,
    set[TimeSignature],
    set[KeySignature],
]:
    cursor = Fraction(0)
    furthest_position = Fraction(0)
    last_note_onset = Fraction(0)
    notes: list[NoteEvent] = []
    signatures: set[TimeSignature] = set()
    keys: set[KeySignature] = set()

    for element in measure:
        tag = _local_name(element.tag)
        if tag == "attributes":
            divisions_text = _child_text(element, "divisions")
            if divisions_text is not None:
                state.divisions = _positive_int(divisions_text, "divisions")
            time_element = _child(element, "time")
            if time_element is not None:
                beats = _positive_int(_required_child_text(time_element, "beats"), "beats")
                beat_type = _positive_int(
                    _required_child_text(time_element, "beat-type"),
                    "beat-type",
                )
                state.time_signature = (beats, beat_type)
                signatures.add(
                    TimeSignature(
                        onset=state.absolute_onset + cursor,
                        beats=beats,
                        beat_type=beat_type,
                    )
                )
            key_element = _child(element, "key")
            if key_element is not None:
                fifths_text = _required_child_text(key_element, "fifths")
                try:
                    fifths = int(fifths_text)
                except ValueError as error:
                    raise InvalidScoreError(
                        f"invalid MusicXML fifths: {fifths_text}"
                    ) from error
                keys.add(
                    KeySignature(
                        onset=state.absolute_onset + cursor,
                        fifths=fifths,
                        mode=_child_text(key_element, "mode"),
                    )
                )
            continue

        if tag in {"backup", "forward"}:
            duration = _duration(element, state.divisions)
            cursor = cursor - duration if tag == "backup" else cursor + duration
            if cursor < 0:
                raise InvalidScoreError(
                    f"MusicXML backup moved before measure start in part {part_id}"
                )
            furthest_position = max(furthest_position, cursor)
            continue

        if tag != "note":
            continue

        is_chord = _child(element, "chord") is not None
        is_grace = _child(element, "grace") is not None
        duration = Fraction(0) if is_grace else _duration(element, state.divisions)
        onset_in_measure = last_note_onset if is_chord else cursor
        if not is_chord:
            last_note_onset = onset_in_measure

        pitch_element = _child(element, "pitch")
        if pitch_element is not None and not is_grace and duration > 0:
            voice = _child_text(element, "voice") or "1"
            staff = _child_text(element, "staff")
            voice_key = (part_id, voice, staff)
            source_voice = voice_ids.setdefault(voice_key, len(voice_ids) + 1)
            tie_types = {
                tie.attrib.get("type")
                for tie in _children(element, "tie")
                if tie.attrib.get("type")
            }
            notes.append(
                NoteEvent(
                    pitch=_parse_pitch(pitch_element),
                    onset=state.absolute_onset + onset_in_measure,
                    duration=duration,
                    source_voice=source_voice,
                    tie_prev="stop" in tie_types,
                    tie_next="start" in tie_types,
                )
            )

        if not is_chord:
            cursor += duration
            furthest_position = max(furthest_position, cursor)
        else:
            furthest_position = max(furthest_position, onset_in_measure + duration)

    expected_duration = (
        Fraction(state.time_signature[0] * 4, state.time_signature[1])
        if state.time_signature
        else Fraction(0)
    )
    if measure.attrib.get("implicit") == "yes":
        measure_duration = furthest_position
    elif expected_duration > 0:
        measure_duration = expected_duration
    else:
        measure_duration = furthest_position
    return notes, measure_duration, signatures, keys


def _parse_pitch(element: ElementTree.Element) -> Pitch:
    step = _required_child_text(element, "step")
    octave_text = _required_child_text(element, "octave")
    alter_text = _child_text(element, "alter")
    try:
        alter = Fraction(alter_text) if alter_text is not None else Fraction(0)
        octave = int(octave_text)
        return Pitch(step=step, alter=alter, octave=octave)
    except (ValueError, ZeroDivisionError) as error:
        raise InvalidScoreError(f"invalid MusicXML pitch: {error}") from error


def _duration(element: ElementTree.Element, divisions: int | None) -> Fraction:
    if divisions is None:
        raise InvalidScoreError("encountered a duration before MusicXML divisions was defined")
    duration_text = _required_child_text(element, "duration")
    try:
        duration = int(duration_text)
    except ValueError as error:
        raise InvalidScoreError(f"invalid MusicXML duration: {duration_text}") from error
    if duration < 0:
        raise InvalidScoreError(f"MusicXML duration cannot be negative: {duration}")
    return Fraction(duration, divisions)


def _part_name_map(root: ElementTree.Element) -> dict[str, str]:
    result: dict[str, str] = {}
    for score_part in root.iter():
        if _local_name(score_part.tag) != "score-part":
            continue
        part_id = score_part.attrib.get("id")
        part_name = _child_text(score_part, "part-name")
        if part_id and part_name:
            result[part_id] = part_name
    return result


def _positive_int(value: str, field: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise InvalidScoreError(f"invalid MusicXML {field}: {value}") from error
    if parsed <= 0:
        raise InvalidScoreError(f"MusicXML {field} must be positive: {value}")
    return parsed


def _required_child_text(element: ElementTree.Element, name: str) -> str:
    value = _child_text(element, name)
    if value is None:
        raise InvalidScoreError(f"MusicXML <{_local_name(element.tag)}> has no <{name}>")
    return value


def _child_text(element: ElementTree.Element, name: str) -> str | None:
    child = _child(element, name)
    if child is None:
        return None
    value = (child.text or "").strip()
    return value or None


def _first_text(root: ElementTree.Element, name: str) -> str | None:
    for element in root.iter():
        if _local_name(element.tag) == name:
            value = (element.text or "").strip()
            if value:
                return value
    return None


def _child(element: ElementTree.Element, name: str) -> ElementTree.Element | None:
    return next((child for child in element if _local_name(child.tag) == name), None)


def _children(element: ElementTree.Element, name: str) -> list[ElementTree.Element]:
    return [child for child in element if _local_name(child.tag) == name]


def _local_name(tag: str) -> str:
    return tag.rsplit("}", maxsplit=1)[-1]
