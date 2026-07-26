from fractions import Fraction
from pathlib import Path

from miduo.parser import parse_score

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_minimal_score():
    score = parse_score(FIXTURES / "minimal.musicxml")
    assert score.title == "Fixture"
    assert len(score.parts) == 1
    assert len(score.source_voices) == 1
    assert len(score.notes) == 1
    assert score.notes[0].onset == 0
    assert score.notes[0].duration == 4
    assert score.duration == 4
    assert len(score.measures) == 1
    assert score.measures[0].number == "1"
    assert score.measures[0].start == 0
    assert score.measures[0].end == 4
    assert score.key_signatures[0].fifths == 0
    assert score.key_signatures[0].mode == "major"


def test_parse_polyphony_chords_and_ties():
    score = parse_score(FIXTURES / "polyphonic.musicxml")
    assert len(score.notes) == 4
    assert len(score.source_voices) == 2
    assert score.duration == 2

    c4, e4, g3 = (
        next(note for note in score.notes if str(note.pitch) == pitch)
        for pitch in ("C4", "E4", "G3")
    )
    assert c4.onset == e4.onset == g3.onset == 0
    assert c4.tie_next
    assert g3.duration == Fraction(2)
