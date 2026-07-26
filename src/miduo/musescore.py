"""MuseScore command-line integration."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from miduo.errors import MuseScoreError

_COMMAND_NAMES = ("mscore", "musescore", "MuseScore4")
_MACOS_APP_EXECUTABLES = (
    Path("/Applications/MuseScore 4.app/Contents/MacOS/mscore"),
    Path("/Applications/MuseScore 3.app/Contents/MacOS/mscore"),
    Path("/Applications/MuseScore.app/Contents/MacOS/mscore"),
)


@dataclass(frozen=True, slots=True)
class MuseScoreConverter:
    """Convert scores by invoking MuseScore's non-interactive CLI."""

    executable: Path

    @classmethod
    def discover(cls, executable: Path | None = None) -> MuseScoreConverter:
        """Find MuseScore or validate an explicitly configured executable."""

        if executable is not None:
            candidate = executable.expanduser()
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return cls(candidate)
            raise MuseScoreError(f"MuseScore executable is not runnable: {candidate}")

        for command in _COMMAND_NAMES:
            if resolved := shutil.which(command):
                return cls(Path(resolved))
        for candidate in _MACOS_APP_EXECUTABLES:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return cls(candidate)

        raise MuseScoreError(
            "MuseScore CLI was not found; install MuseScore, add mscore to PATH, "
            "or pass --musescore /path/to/mscore"
        )

    def convert_to_musicxml(self, input_path: Path, output_path: Path) -> None:
        """Convert a MuseScore score to uncompressed MusicXML."""

        self.convert(input_path, output_path)

    def convert_to_musescore(self, input_path: Path, output_path: Path) -> None:
        """Convert MusicXML to a MuseScore score."""

        self.convert(input_path, output_path)

    def convert(
        self,
        input_path: Path,
        output_path: Path,
        *,
        force: bool = False,
    ) -> None:
        """Convert between formats inferred by MuseScore from file extensions."""

        command = [str(self.executable)]
        if force:
            command.append("-f")
        command.extend(("-o", str(output_path), str(input_path)))
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise MuseScoreError(f"could not run MuseScore: {error}") from error

        if result.returncode != 0:
            details = (result.stderr or result.stdout).strip()
            suffix = f": {details}" if details else ""
            raise MuseScoreError(
                f"MuseScore conversion failed with exit code {result.returncode}{suffix}"
            )
        if not output_path.is_file():
            raise MuseScoreError(
                f"MuseScore reported success but did not create output: {output_path}"
            )
