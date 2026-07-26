"""Arrangement pipeline boundary.

The public API is intentionally small so each stage in DESIGN.md can be added
without coupling it to argument parsing.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from miduo.assignment import assign_voices
from miduo.errors import OutputError, UnsupportedFormatError
from miduo.harmony import HarmonyBackend, analyze_harmony
from miduo.model import (
    AssignmentResult,
    HarmonicSlice,
    RhythmReductionResult,
    ScoreIR,
    SustainSpan,
    ValidationResult,
)
from miduo.parser import parse_score
from miduo.rhythm import reduce_violin2_rhythm
from miduo.slicing import build_harmonic_slices
from miduo.spans import detect_sustain_spans
from miduo.validation import validate_with_retries
from miduo.writer import write_arrangement


@dataclass(frozen=True, slots=True)
class ArrangementRequest:
    input_path: Path
    output_path: Path
    musescore_executable: Path | None = None
    harmony_backend: HarmonyBackend = HarmonyBackend.INTERNAL


@dataclass(frozen=True, slots=True)
class ArrangementPlan:
    request: ArrangementRequest
    input_score: ScoreIR
    harmonic_slices: tuple[HarmonicSlice, ...]
    sustain_spans: tuple[SustainSpan, ...]
    assignment: AssignmentResult
    rhythm_reduction: RhythmReductionResult
    validation: ValidationResult
    stages: tuple[str, ...]


PIPELINE_STAGES = (
    "parse",
    "analyze-harmony",
    "detect-spans",
    "assign-voices",
    "reduce-rhythm",
    "validate",
    "write",
)


class ArrangementPipeline:
    """Facade for validating and eventually executing an arrangement."""

    def __init__(
        self,
        *,
        progress: Callable[[str, str], None] | None = None,
    ) -> None:
        self._progress = progress

    def _report(self, stage: str, message: str) -> None:
        if self._progress is not None:
            self._progress(stage, message)

    def plan(self, request: ArrangementRequest) -> ArrangementPlan:
        if request.output_path.suffix.lower() not in {".musicxml", ".xml", ".mxl", ".mscz"}:
            raise UnsupportedFormatError(
                "output filename must end in .musicxml, .xml, .mxl, or .mscz"
            )
        self._report("input", f"reading {request.input_path}")
        score = parse_score(
            request.input_path,
            musescore_executable=request.musescore_executable,
        )
        self._report(
            "parse",
            f"{len(score.measures)} measures, {len(score.notes)} note events",
        )
        self._report("slices", "building harmonic slices")
        raw_slices = build_harmonic_slices(score)
        self._report(
            "harmony",
            f"analyzing {len(raw_slices)} slices with {request.harmony_backend.value}",
        )
        slices = analyze_harmony(
            raw_slices,
            backend=request.harmony_backend,
            score=score,
        )
        self._report("spans", "detecting sustained notes")
        spans = detect_sustain_spans(score, slices)
        self._report("assign-voices", "starting global voice assignment")
        last_reported_measure = 0

        def report_assignment(current: int, total: int, number: str) -> None:
            nonlocal last_reported_measure
            if current <= last_reported_measure:
                return
            last_reported_measure = current
            percent = round(current / total * 100) if total else 0
            self._report(
                "assign-voices",
                f"measure {number} ({current}/{total}, {percent}%)",
            )

        assignment = assign_voices(
            score,
            slices,
            spans,
            progress=report_assignment,
        )
        self._report("reduce-rhythm", "simplifying Violin 2")
        rhythm_reduction = reduce_violin2_rhythm(score, assignment, slices)
        self._report("validate", "checking hard constraints")
        validation = validate_with_retries(
            score,
            slices,
            spans,
            rhythm_reduction.assignment,
        )
        self._report(
            "validate",
            f"{'valid' if validation.is_valid else 'invalid'}, "
            f"{validation.retry_count} retries",
        )
        return ArrangementPlan(
            request=request,
            input_score=score,
            harmonic_slices=slices,
            sustain_spans=spans,
            assignment=assignment,
            rhythm_reduction=rhythm_reduction,
            validation=validation,
            stages=PIPELINE_STAGES,
        )

    def run(self, request: ArrangementRequest) -> Path:
        if request.input_path.expanduser().resolve() == request.output_path.expanduser().resolve():
            raise OutputError("input and output paths must be different")
        plan = self.plan(request)
        self._report("write", f"writing {request.output_path}")
        output = write_arrangement(
            plan.input_score,
            plan.validation.assignment,
            request.output_path,
            musescore_executable=request.musescore_executable,
        )
        self._report("done", str(output))
        return output
