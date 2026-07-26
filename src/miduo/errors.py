"""Application-specific errors."""


class MiduoError(Exception):
    """Base class for errors that should be shown to CLI users."""


class UnsupportedFormatError(MiduoError):
    """Raised when an input file format is unsupported."""


class InvalidScoreError(MiduoError):
    """Raised when a score cannot be parsed as valid MusicXML."""


class MuseScoreError(MiduoError):
    """Raised when MuseScore cannot be found or fails to convert a score."""


class OutputError(MiduoError):
    """Raised when an output path or write operation is invalid."""


class ArrangementNotImplementedError(MiduoError):
    """Raised while the arrangement engine is not implemented."""
