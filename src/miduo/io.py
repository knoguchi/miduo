"""Input format detection and lightweight MusicXML inspection."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from tempfile import TemporaryDirectory
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from miduo.errors import InvalidScoreError, UnsupportedFormatError
from miduo.musescore import MuseScoreConverter


class ScoreFormat(StrEnum):
    MUSICXML = "musicxml"
    COMPRESSED_MUSICXML = "mxl"
    MUSESCORE = "mscz"


@dataclass(frozen=True, slots=True)
class ScoreSummary:
    """Small, serializable description of an input score."""

    path: Path
    format: ScoreFormat
    title: str | None
    part_names: tuple[str, ...]
    measure_count: int
    note_count: int


@dataclass(frozen=True, slots=True)
class LoadedScoreXml:
    path: Path
    format: ScoreFormat
    root: ElementTree.Element


_MUSICXML_SUFFIXES = {".musicxml", ".xml"}


def detect_format(path: Path) -> ScoreFormat:
    """Return the score format inferred from its extension."""

    suffix = path.suffix.lower()
    if suffix in _MUSICXML_SUFFIXES:
        return ScoreFormat.MUSICXML
    if suffix == ".mxl":
        return ScoreFormat.COMPRESSED_MUSICXML
    if suffix == ".mscz":
        return ScoreFormat.MUSESCORE
    supported = ", ".join(sorted((*_MUSICXML_SUFFIXES, ".mxl", ".mscz")))
    raise UnsupportedFormatError(
        f"unsupported input format {suffix or '(no extension)'}; expected one of: {supported}"
    )


def inspect_score(
    path: Path,
    *,
    musescore_executable: Path | None = None,
) -> ScoreSummary:
    """Read enough of a MusicXML score to validate it and report its shape."""

    loaded = read_score_xml(path, musescore_executable=musescore_executable)
    root = loaded.root
    tag = _local_name(root.tag)
    if tag not in {"score-partwise", "score-timewise"}:
        raise InvalidScoreError(
            f"expected a MusicXML score root, found <{tag}> in {loaded.path}"
        )

    part_names = tuple(
        text
        for element in root.iter()
        if _local_name(element.tag) == "part-name"
        and (text := (element.text or "").strip())
    )
    title = _first_text(root, "work-title") or _first_text(root, "movement-title")
    measure_count = sum(1 for element in root.iter() if _local_name(element.tag) == "measure")
    note_count = sum(
        1
        for element in root.iter()
        if _local_name(element.tag) == "note" and not _has_child(element, "rest")
    )
    return ScoreSummary(
        path=loaded.path,
        format=loaded.format,
        title=title,
        part_names=part_names,
        measure_count=measure_count,
        note_count=note_count,
    )


def read_score_xml(
    path: Path,
    *,
    musescore_executable: Path | None = None,
) -> LoadedScoreXml:
    """Load supported input into an in-memory MusicXML element tree."""

    resolved_path = path.expanduser()
    if not resolved_path.is_file():
        raise InvalidScoreError(f"input file does not exist: {resolved_path}")

    score_format = detect_format(resolved_path)
    if score_format is ScoreFormat.MUSESCORE:
        converter = MuseScoreConverter.discover(musescore_executable)
        with TemporaryDirectory(prefix="miduo-") as temporary_directory:
            converted_path = Path(temporary_directory) / f"{resolved_path.stem}.musicxml"
            converter.convert_to_musicxml(resolved_path, converted_path)
            root = _read_musicxml_root(converted_path, ScoreFormat.MUSICXML)
    else:
        root = _read_musicxml_root(resolved_path, score_format)

    return LoadedScoreXml(
        path=resolved_path,
        format=score_format,
        root=root,
    )


def _read_musicxml_root(path: Path, score_format: ScoreFormat) -> ElementTree.Element:
    try:
        if score_format is ScoreFormat.MUSICXML:
            return ElementTree.parse(path).getroot()
        return _read_mxl_root(path)
    except (ElementTree.ParseError, OSError, BadZipFile, KeyError) as error:
        raise InvalidScoreError(f"could not parse {path} as MusicXML: {error}") from error


def _read_mxl_root(path: Path) -> ElementTree.Element:
    with ZipFile(path) as archive:
        container = ElementTree.fromstring(archive.read("META-INF/container.xml"))
        rootfile = next(
            (
                element.attrib.get("full-path")
                for element in container.iter()
                if _local_name(element.tag) == "rootfile"
            ),
            None,
        )
        if not rootfile:
            raise InvalidScoreError(f"compressed MusicXML has no rootfile: {path}")
        return ElementTree.fromstring(archive.read(rootfile))


def _first_text(root: ElementTree.Element, name: str) -> str | None:
    for element in root.iter():
        if _local_name(element.tag) == name:
            value = (element.text or "").strip()
            if value:
                return value
    return None


def _has_child(element: ElementTree.Element, name: str) -> bool:
    return any(_local_name(child.tag) == name for child in element)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", maxsplit=1)[-1]
