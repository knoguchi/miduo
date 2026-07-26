from fractions import Fraction

from miduo.model import Pitch


def test_pitch_name_and_midi_number():
    pitch = Pitch("C", 4, Fraction(1))
    assert str(pitch) == "C#4"
    assert pitch.midi_number == 61


def test_microtonal_pitch_name():
    assert str(Pitch("D", 5, Fraction(1, 2))) == "D(+1/2)5"
