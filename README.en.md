# miduo

[日本語](README.md)

miduo is a Python CLI that automatically reduces MusicXML and MuseScore scores
to two monophonic voices. It preserves the melody in the first voice and selects
a second melodic line that represents the harmonic backbone of the original
accompaniment.

The arrangement architecture is intended to be general-purpose, but the
current range constraints and output instrument metadata are violin-specific.
The first voice is limited to G3–E7, the second to G3–A6, and both output parts
are identified as violins. A future instrument-profile system is expected to
make profiles selectable independently for each voice.

Supported input and output formats are `.musicxml`, `.xml`, `.mxl`, and `.mscz`.
Reading or writing MSCZ requires the MuseScore CLI.

## Requirements

- Python 3.11 or later
- [uv](https://docs.astral.sh/uv/)
- MuseScore 3 or 4 when working with MSCZ

Prepare the environment and check the CLI:

```console
uv sync
uv run miduo --help
```

Commit `uv.lock` to the repository to keep development and CI environments
reproducible.

## Arrange a score

```console
uv run miduo arrange input.musicxml -o duet.musicxml
uv run miduo arrange input.mscz -o duet.mscz
uv run miduo arrange input.mscz -o duet-music21.mscz \
  --harmony-backend music21
```

Two harmony backends are available: the fast `internal` backend, which is the
default, and the `music21` backend, which adds local-key and Roman-numeral
analysis. Generating both versions of a score and comparing them by ear is
recommended.

`arrange` runs the following stages:

1. Parse MusicXML
2. Build harmonic slices and estimate chords
3. Detect sustained notes, suspensions, and pedal tones
4. Assign material to two voices
5. Simplify the second-voice rhythm
6. Validate range, monophony, and voice crossing
7. Write MusicXML, MXL, or MSCZ

For long scores, progress is written to standard error, including the current
stage and measure during voice assignment:

```text
[parse] 400 measures, 3870 note events
[harmony] analyzing 2419 slices
[assign-voices] measure 214 (215/400, 54%)
[assign-voices] measure 399 (400/400, 100%)
[validate] valid, 0 retries
```

Use `--quiet` to suppress progress or `--dry-run` to run the complete pipeline
without writing the output:

```console
uv run miduo arrange input.mscz -o duet.mscz --quiet
uv run miduo arrange input.mscz -o duet.musicxml --dry-run
```

If MuseScore cannot be detected automatically, specify its executable:

```console
uv run miduo arrange input.mscz -o duet.mscz \
  --musescore "/Applications/MuseScore 4.app/Contents/MacOS/mscore"
```

To run without installing the package:

```console
PYTHONPATH=src python -m miduo --help
```

## Inspect intermediate results

Each pipeline stage can be run independently. Most commands support `--json`.

```console
uv run miduo inspect input.mscz
uv run miduo parse input.musicxml --json
uv run miduo slice input.musicxml --json
uv run miduo analyze input.musicxml --json
uv run miduo analyze input.musicxml --harmony-backend music21 --json
uv run miduo spans input.musicxml --json
uv run miduo assign input.musicxml --json
uv run miduo reduce input.musicxml --json
uv run miduo validate input.musicxml --json
```

`analyze`, `spans`, and `assign` accept `--confidence-threshold`; `reduce`
accepts `--attack-threshold`; and `validate` accepts `--max-retries`.

## How the two-voice reduction works

The source is divided into short harmonic slices at every note onset and
release. The first voice takes the first source voice in the first part as its
melody. Candidates for the second voice come from accompaniment notes and
their octave transpositions. The current ranges are G3–E7 for the first voice
and G3–A6 for the second.

The `internal` backend matches pitch-class sets against built-in chord
templates. It recognizes major, minor, augmented, dominant-seventh,
major-seventh, minor-seventh, diminished-seventh, and half-diminished-seventh
chords. Low-confidence slices inherit the most recent confident chord.

The `music21` backend sends the same slices to music21 and estimates a local
key, chord root, and Roman numeral for each sixteen-quarter-note context. The
notated key signature is used as supporting evidence. Dominant-to-tonic
resolutions and resolved secondary dominants are treated as cadences. When
music21 cannot identify a supported chord quality, the analyzer falls back to
the internal result.

The second voice is selected by assigning costs to lost harmonic information,
range violations, leaps, voice crossing, spacing, broken sustains, and
unnecessary weak-beat motion. Each search segment ends at a cadence, a silence,
or approximately eight quarter-note units. At every slice, the search retains
at most eight choices including a rest, merges paths that reach the same state,
and keeps the best 24 paths.

See [DESIGN.en.md](DESIGN.en.md) for details.

## Current limitations

- The arrangement parser requires `score-partwise` MusicXML.
- The first source voice in the first part is assumed to be the melody.
- Each output voice is monophonic; simultaneous notes within one voice are not
  supported.
- Grace notes, dynamics, articulations, lyrics, and similar notation are not
  copied to the output.
- Output timing is quantized to a sixteenth-note grid for readability and
  MuseScore compatibility.
- The internal chord analyzer does not model key, modulation, or harmonic
  function globally.
- The music21 local-key window spans sixteen quarter-note units and does not
  locate modulation boundaries precisely.
- The system selects a structural line from source notes; it does not compose
  a new countermelody.

## Tests

```console
uv run pytest
uv run ruff check .
```
