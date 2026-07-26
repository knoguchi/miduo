"""Command-line interface for miduo."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from miduo._version import __version__
from miduo.assignment import assign_voices
from miduo.errors import MiduoError
from miduo.harmony import HarmonyBackend, analyze_harmony, chord_histogram
from miduo.io import ScoreSummary, inspect_score
from miduo.model import AssignedVoice, HarmonicSlice, ScoreIR, SpanType, SustainSpan
from miduo.parser import parse_score
from miduo.pipeline import ArrangementPipeline, ArrangementRequest
from miduo.rhythm import reduce_violin2_rhythm
from miduo.slicing import build_harmonic_slices
from miduo.spans import detect_sustain_spans
from miduo.validation import validate_with_retries


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="miduo",
        description="Generate violin-duo arrangements from MusicXML scores.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser(
        "inspect",
        help="validate a score and print a short summary",
    )
    inspect_parser.add_argument(
        "input",
        type=Path,
        help="input .musicxml, .xml, .mxl, or .mscz file",
    )
    inspect_parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    _add_musescore_argument(inspect_parser)

    parse_parser = subparsers.add_parser(
        "parse",
        help="parse a score into the internal Score IR and print statistics",
    )
    parse_parser.add_argument("input", type=Path, help="input score")
    parse_parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    _add_musescore_argument(parse_parser)

    slice_parser = subparsers.add_parser(
        "slice",
        help="build harmonic slices and print statistics",
    )
    slice_parser.add_argument("input", type=Path, help="input score")
    slice_parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    _add_musescore_argument(slice_parser)

    analyze_parser = subparsers.add_parser(
        "analyze",
        help="estimate chords and cadences for harmonic slices",
    )
    analyze_parser.add_argument("input", type=Path, help="input score")
    analyze_parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    analyze_parser.add_argument(
        "--confidence-threshold",
        type=_unit_interval,
        default=0.65,
        metavar="FLOAT",
        help="minimum confidence before interpolation (default: 0.65)",
    )
    _add_harmony_backend_argument(analyze_parser)
    _add_musescore_argument(analyze_parser)

    spans_parser = subparsers.add_parser(
        "spans",
        help="detect and classify sustained-note spans",
    )
    spans_parser.add_argument("input", type=Path, help="input score")
    spans_parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    spans_parser.add_argument(
        "--confidence-threshold",
        type=_unit_interval,
        default=0.65,
        metavar="FLOAT",
        help="harmony confidence threshold (default: 0.65)",
    )
    _add_harmony_backend_argument(spans_parser)
    _add_musescore_argument(spans_parser)

    assign_parser = subparsers.add_parser(
        "assign",
        help="assign pitches to Violin 1 and Violin 2",
    )
    assign_parser.add_argument("input", type=Path, help="input score")
    assign_parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    assign_parser.add_argument(
        "--confidence-threshold",
        type=_unit_interval,
        default=0.65,
        metavar="FLOAT",
        help="harmony confidence threshold (default: 0.65)",
    )
    _add_harmony_backend_argument(assign_parser)
    _add_musescore_argument(assign_parser)

    reduce_parser = subparsers.add_parser(
        "reduce",
        help="reduce dense Violin 2 rhythms",
    )
    reduce_parser.add_argument("input", type=Path, help="input score")
    reduce_parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    reduce_parser.add_argument(
        "--attack-threshold",
        type=_non_negative_float,
        default=2.0,
        metavar="FLOAT",
        help="maximum attacks per beat before reduction (default: 2)",
    )
    _add_harmony_backend_argument(reduce_parser)
    _add_musescore_argument(reduce_parser)

    validate_parser = subparsers.add_parser(
        "validate",
        help="validate ranges, monophony, and voice crossing",
    )
    validate_parser.add_argument("input", type=Path, help="input score")
    validate_parser.add_argument(
        "--json",
        action="store_true",
        help="print machine-readable JSON",
    )
    validate_parser.add_argument(
        "--max-retries",
        type=_non_negative_int,
        default=3,
        metavar="INT",
        help="maximum reassignment attempts (default: 3)",
    )
    _add_harmony_backend_argument(validate_parser)
    _add_musescore_argument(validate_parser)

    arrange_parser = subparsers.add_parser(
        "arrange",
        help="run the violin-duo arrangement pipeline",
    )
    arrange_parser.add_argument("input", type=Path, help="input score")
    arrange_parser.add_argument(
        "-o",
        "--output",
        type=Path,
        required=True,
        help="output score",
    )
    arrange_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate paths and show the planned stages without writing output",
    )
    arrange_parser.add_argument(
        "--quiet",
        action="store_true",
        help="suppress progress messages",
    )
    _add_harmony_backend_argument(arrange_parser)
    _add_musescore_argument(arrange_parser)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "inspect":
            summary = inspect_score(
                args.input,
                musescore_executable=args.musescore,
            )
            _print_summary(summary, as_json=args.json)
            return 0
        if args.command == "parse":
            score = parse_score(
                args.input,
                musescore_executable=args.musescore,
            )
            _print_score_ir(score, as_json=args.json)
            return 0
        if args.command == "slice":
            score = parse_score(
                args.input,
                musescore_executable=args.musescore,
            )
            _print_slices(build_harmonic_slices(score), as_json=args.json)
            return 0
        if args.command == "analyze":
            score = parse_score(
                args.input,
                musescore_executable=args.musescore,
            )
            slices = analyze_harmony(
                build_harmonic_slices(score),
                confidence_threshold=args.confidence_threshold,
                backend=args.harmony_backend,
                score=score,
            )
            _print_harmony(
                slices,
                as_json=args.json,
                confidence_threshold=args.confidence_threshold,
                backend=args.harmony_backend,
            )
            return 0
        if args.command == "spans":
            score = parse_score(
                args.input,
                musescore_executable=args.musescore,
            )
            slices = analyze_harmony(
                build_harmonic_slices(score),
                confidence_threshold=args.confidence_threshold,
                backend=args.harmony_backend,
                score=score,
            )
            _print_spans(
                detect_sustain_spans(score, slices),
                as_json=args.json,
            )
            return 0
        if args.command == "assign":
            score = parse_score(
                args.input,
                musescore_executable=args.musescore,
            )
            slices = analyze_harmony(
                build_harmonic_slices(score),
                confidence_threshold=args.confidence_threshold,
                backend=args.harmony_backend,
                score=score,
            )
            spans = detect_sustain_spans(score, slices)
            _print_assignment(
                assign_voices(score, slices, spans),
                as_json=args.json,
            )
            return 0
        if args.command == "reduce":
            score = parse_score(
                args.input,
                musescore_executable=args.musescore,
            )
            slices = analyze_harmony(
                build_harmonic_slices(score),
                backend=args.harmony_backend,
                score=score,
            )
            spans = detect_sustain_spans(score, slices)
            assignment = assign_voices(score, slices, spans)
            _print_reduction(
                reduce_violin2_rhythm(
                    score,
                    assignment,
                    slices,
                    attack_threshold=args.attack_threshold,
                ),
                before=assignment,
                as_json=args.json,
            )
            return 0
        if args.command == "validate":
            score = parse_score(
                args.input,
                musescore_executable=args.musescore,
            )
            slices = analyze_harmony(
                build_harmonic_slices(score),
                backend=args.harmony_backend,
                score=score,
            )
            spans = detect_sustain_spans(score, slices)
            assignment = assign_voices(score, slices, spans)
            reduced = reduce_violin2_rhythm(score, assignment, slices)
            _print_validation(
                validate_with_retries(
                    score,
                    slices,
                    spans,
                    reduced.assignment,
                    max_retries=args.max_retries,
                ),
                as_json=args.json,
            )
            return 0
        if args.command == "arrange":
            request = ArrangementRequest(
                input_path=args.input,
                output_path=args.output,
                musescore_executable=args.musescore,
                harmony_backend=args.harmony_backend,
            )
            pipeline = ArrangementPipeline(
                progress=None if args.quiet else _print_progress,
            )
            if args.dry_run:
                plan = pipeline.plan(request)
                print(f"Input: {plan.request.input_path}")
                print(f"Output: {plan.request.output_path}")
                print(f"Parsed note events: {len(plan.input_score.notes)}")
                print(f"Harmonic slices: {len(plan.harmonic_slices)}")
                print(
                    "Labeled chords: "
                    f"{sum(item.chord is not None for item in plan.harmonic_slices)}"
                )
                print(f"Sustain spans: {len(plan.sustain_spans)}")
                print(f"Assigned notes: {len(plan.assignment.notes)}")
                print(
                    "Rhythm reduction: "
                    f"{plan.rhythm_reduction.removed_attack_count} attacks removed"
                )
                print(
                    "Validation: "
                    f"{'valid' if plan.validation.is_valid else 'invalid'} "
                    f"({plan.validation.retry_count} retries)"
                )
                print(f"Stages: {' -> '.join(plan.stages)}")
                return 0
            output = pipeline.run(request)
            print(f"Wrote: {output}")
            return 0
    except MiduoError as error:
        print(f"miduo: error: {error}", file=sys.stderr)
        return 2

    return 2


def _add_musescore_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--musescore",
        type=Path,
        metavar="PATH",
        help="MuseScore CLI executable (automatically detected by default)",
    )


def _add_harmony_backend_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--harmony-backend",
        type=HarmonyBackend,
        choices=tuple(HarmonyBackend),
        default=HarmonyBackend.INTERNAL,
        help="chord analysis backend (default: internal)",
    )


def _print_progress(stage: str, message: str) -> None:
    print(f"[{stage}] {message}", file=sys.stderr, flush=True)


def _unit_interval(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a number between 0 and 1") from error
    if not 0 <= parsed <= 1:
        raise argparse.ArgumentTypeError("must be between 0 and 1")
    return parsed


def _non_negative_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a non-negative number") from error
    if parsed < 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return parsed


def _non_negative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a non-negative integer") from error
    if parsed < 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return parsed


def _print_summary(summary: ScoreSummary, *, as_json: bool) -> None:
    if as_json:
        print(
            json.dumps(
                {
                    "path": str(summary.path),
                    "format": summary.format.value,
                    "title": summary.title,
                    "parts": list(summary.part_names),
                    "measure_count": summary.measure_count,
                    "note_count": summary.note_count,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    print(f"Path: {summary.path}")
    print(f"Format: {summary.format.value}")
    print(f"Title: {summary.title or '(untitled)'}")
    print(f"Parts: {', '.join(summary.part_names) if summary.part_names else '(unnamed)'}")
    print(f"Measures: {summary.measure_count}")
    print(f"Notes: {summary.note_count}")


def _print_score_ir(score: ScoreIR, *, as_json: bool) -> None:
    pitches = [note.pitch.midi_number for note in score.notes]
    data = {
        "title": score.title,
        "parts": [{"id": part.id, "name": part.name} for part in score.parts],
        "source_voice_count": len(score.source_voices),
        "note_event_count": len(score.notes),
        "time_signatures": [
            {
                "onset": str(signature.onset),
                "beats": signature.beats,
                "beat_type": signature.beat_type,
            }
            for signature in score.time_signatures
        ],
        "duration_quarter_notes": str(score.duration),
        "lowest_pitch": str(min(score.notes, key=lambda note: note.pitch.midi_number).pitch)
        if pitches
        else None,
        "highest_pitch": str(max(score.notes, key=lambda note: note.pitch.midi_number).pitch)
        if pitches
        else None,
    }
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    print(f"Title: {score.title or '(untitled)'}")
    print(f"Parts: {', '.join(part.name for part in score.parts) or '(unnamed)'}")
    print(f"Source voices: {data['source_voice_count']}")
    print(f"Note events: {data['note_event_count']}")
    print(f"Duration (quarter notes): {score.duration}")
    print(f"Pitch range: {data['lowest_pitch'] or '(none)'}–{data['highest_pitch'] or '(none)'}")


def _print_slices(slices: tuple[HarmonicSlice, ...], *, as_json: bool) -> None:
    sounding = sum(bool(harmonic_slice.active_notes) for harmonic_slice in slices)
    max_polyphony = max((len(harmonic_slice.active_notes) for harmonic_slice in slices), default=0)
    data = {
        "slice_count": len(slices),
        "sounding_slice_count": sounding,
        "silent_slice_count": len(slices) - sounding,
        "max_active_notes": max_polyphony,
        "duration_quarter_notes": str(slices[-1].end) if slices else "0",
        "beat_weight_counts": {
            str(weight): sum(harmonic_slice.beat_weight == weight for harmonic_slice in slices)
            for weight in (1.0, 0.5, 0.25)
        },
    }
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    print(f"Harmonic slices: {data['slice_count']}")
    print(f"Sounding/silent: {sounding}/{data['silent_slice_count']}")
    print(f"Maximum active notes: {max_polyphony}")
    print(f"Duration (quarter notes): {data['duration_quarter_notes']}")


def _print_harmony(
    slices: tuple[HarmonicSlice, ...],
    *,
    as_json: bool,
    confidence_threshold: float,
    backend: HarmonyBackend,
) -> None:
    histogram = chord_histogram(slices)
    key_histogram = Counter(
        harmonic_slice.chord.key
        for harmonic_slice in slices
        if harmonic_slice.chord is not None and harmonic_slice.chord.key is not None
    )
    roman_histogram = Counter(
        harmonic_slice.chord.roman_numeral
        for harmonic_slice in slices
        if harmonic_slice.chord is not None
        and harmonic_slice.chord.roman_numeral is not None
    )
    labeled = sum(harmonic_slice.chord is not None for harmonic_slice in slices)
    confident = sum(
        harmonic_slice.chord is not None
        and harmonic_slice.chord.confidence >= confidence_threshold
        for harmonic_slice in slices
    )
    data = {
        "slice_count": len(slices),
        "labeled_slice_count": labeled,
        "confident_slice_count": confident,
        "confidence_threshold": confidence_threshold,
        "backend": backend.value,
        "cadence_slice_count": sum(item.is_cadence for item in slices),
        "chord_histogram": dict(histogram.most_common()),
        "key_histogram": dict(key_histogram.most_common()),
        "roman_numeral_histogram": dict(roman_histogram.most_common()),
    }
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    print(f"Labeled harmonic slices: {labeled}/{len(slices)}")
    print(f"Backend: {backend.value}")
    print(f"Confident slices: {confident}")
    print(f"Cadence slices: {data['cadence_slice_count']}")
    print(f"Chords: {', '.join(f'{name}={count}' for name, count in histogram.most_common())}")
    if key_histogram:
        keys = ", ".join(
            f"{name}={count}" for name, count in key_histogram.most_common()
        )
        print(f"Keys: {keys}")
    if roman_histogram:
        print(
            "Roman numerals: "
            + ", ".join(f"{name}={count}" for name, count in roman_histogram.most_common())
        )


def _print_spans(spans: tuple[SustainSpan, ...], *, as_json: bool) -> None:
    counts = {
        span_type.value: sum(span.span_type is span_type for span in spans)
        for span_type in SpanType
    }
    data = {
        "span_count": len(spans),
        "type_counts": counts,
        "spans": [
            {
                "pitch": str(span.pitch),
                "start": str(span.start),
                "end": str(span.end),
                "duration": str(span.duration),
                "type": span.span_type.value,
                "resolves_to": str(span.resolves_to) if span.resolves_to else None,
                "source_voice": span.source_voice,
            }
            for span in spans
        ],
    }
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    print(f"Sustain spans: {len(spans)}")
    print(f"Types: {', '.join(f'{name}={count}' for name, count in counts.items())}")


def _print_assignment(result, *, as_json: bool) -> None:
    violin1_notes = [
        note for note in result.notes if note.voice is AssignedVoice.VIOLIN_1
    ]
    violin2_notes = [
        note for note in result.notes if note.voice is AssignedVoice.VIOLIN_2
    ]
    crossings = sum(
        assignment.violin1_pitch is not None
        and assignment.violin2_pitch is not None
        and assignment.violin1_pitch.midi_number < assignment.violin2_pitch.midi_number
        for assignment in result.slices
    )
    data = {
        "slice_assignment_count": len(result.slices),
        "assigned_note_count": len(result.notes),
        "violin1_note_count": len(violin1_notes),
        "violin2_note_count": len(violin2_notes),
        "violin2_rest_slice_count": sum(
            assignment.violin2_pitch is None for assignment in result.slices
        ),
        "voice_crossing_slice_count": crossings,
        "violin1_range": _assigned_range(violin1_notes),
        "violin2_range": _assigned_range(violin2_notes),
        "violin2_leaps": _leap_statistics(violin2_notes),
    }
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    print(f"Assigned slices: {data['slice_assignment_count']}")
    print(f"Violin 1 notes: {data['violin1_note_count']}")
    print(f"Violin 2 notes: {data['violin2_note_count']}")
    print(f"Voice crossings: {crossings}")
    print(f"Ranges: V1={data['violin1_range']}, V2={data['violin2_range']}")
    print(f"Violin 2 leaps: {data['violin2_leaps']}")


def _assigned_range(notes) -> str | None:
    if not notes:
        return None
    lowest = min(notes, key=lambda note: note.pitch.midi_number).pitch
    highest = max(notes, key=lambda note: note.pitch.midi_number).pitch
    return f"{lowest}–{highest}"


def _leap_statistics(notes) -> dict[str, float | int]:
    ordered = sorted(notes, key=lambda note: note.onset)
    distances = [
        float(abs(current.pitch.midi_number - previous.pitch.midi_number))
        for previous, current in zip(ordered, ordered[1:], strict=False)
    ]
    return {
        "average_semitones": round(sum(distances) / len(distances), 3)
        if distances
        else 0.0,
        "maximum_semitones": round(max(distances), 3) if distances else 0.0,
        "leaps_over_seven": sum(distance > 7 for distance in distances),
    }


def _print_reduction(result, *, before, as_json: bool) -> None:
    before_v2 = sum(note.voice is AssignedVoice.VIOLIN_2 for note in before.notes)
    after_v2 = sum(
        note.voice is AssignedVoice.VIOLIN_2 for note in result.assignment.notes
    )
    after_v2_notes = [
        note
        for note in result.assignment.notes
        if note.voice is AssignedVoice.VIOLIN_2
    ]
    data = {
        "reduced_beat_count": result.reduced_beat_count,
        "removed_attack_count": result.removed_attack_count,
        "violin2_note_count_before": before_v2,
        "violin2_note_count_after": after_v2,
        "violin2_leaps_after": _leap_statistics(after_v2_notes),
    }
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    print(f"Reduced beats: {result.reduced_beat_count}")
    print(f"Removed attacks: {result.removed_attack_count}")
    print(f"Violin 2 notes: {before_v2} -> {after_v2}")


def _print_validation(result, *, as_json: bool) -> None:
    data = {
        "valid": result.is_valid,
        "retry_count": result.retry_count,
        "issue_count": len(result.issues),
        "issues": [
            {
                "type": issue.issue_type.value,
                "onset": str(issue.onset),
                "voice": issue.voice.value if issue.voice else None,
                "message": issue.message,
            }
            for issue in result.issues
        ],
    }
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    print(f"Validation: {'valid' if result.is_valid else 'invalid'}")
    print(f"Retries: {result.retry_count}")
    print(f"Issues: {len(result.issues)}")
    for issue in result.issues:
        print(f"- {issue.issue_type.value} at {issue.onset}: {issue.message}")
