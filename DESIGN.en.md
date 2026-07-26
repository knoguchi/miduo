# General-Purpose Two-Voice Arranger: Design

[日本語](DESIGN.md)

This document describes the current implementation rather than a future
proposal.

## 1. Purpose and scope

The system accepts MusicXML or MuseScore input and reduces the source material
to two monophonic voices.

The architecture is intended to support general two-voice arrangement.
However, the current range constants, output instrument metadata, and clef are
hard-coded for violins. A future profile system is expected to select an
instrument independently for each voice.

Its main goals are:

- preserve the melody in the first voice;
- preserve characteristic harmony and sustained tones in the second voice;
- keep both voices within the currently configured ranges;
- make the second voice work as a coherent melodic line as well as a harmonic
  reduction; and
- reduce dense accompaniment rhythms to a playable density.

Simultaneous notes within a voice, instrument-specific technique, newly
invented dynamics or articulations, and newly composed countermelodies are
outside the current scope.

## 2. Pipeline

```text
[Input: MusicXML / MXL / MSCZ]
          |
          v
[parse] MusicXML -> ScoreIR
          |
          v
[slice] Split at every note onset and release
          |
          v
[harmony] Estimate chords and cadences with internal or music21 analysis
          |
          v
[spans] Detect sustained notes, suspensions, and pedal tones
          |
          v
[assign] Greedy baseline + segment-level beam search
          |
          v
[reduce] Reduce dense second-voice rhythms
          |
          v
[validate] Check range, monophony, and voice crossing; retry if needed
          |
          v
[write] MusicXML / MXL / MSCZ
```

The implementation is divided into the following modules:

| Stage | Module |
|---|---|
| Format detection and MXL extraction | `io.py` |
| MuseScore conversion | `musescore.py` |
| MusicXML parsing | `parser.py` |
| Data model | `model.py` |
| Harmonic slicing | `slicing.py` |
| Harmony estimation | `harmony.py`, `music21_harmony.py` |
| Sustain-span detection | `spans.py` |
| Two-voice assignment | `assignment.py` |
| Rhythm reduction | `rhythm.py` |
| Constraint validation | `validation.py` |
| Output generation | `writer.py` |
| Pipeline facade | `pipeline.py` |
| CLI | `cli.py` |

## 3. Input and output

### 3.1 Input

Supported extensions are `.musicxml`, `.xml`, `.mxl`, and `.mscz`.

- MusicXML is parsed directly.
- MXL is opened as a ZIP container and its rootfile is parsed.
- MSCZ is converted to temporary MusicXML through the MuseScore CLI.
- MuseScore is discovered through `PATH`, common macOS application locations,
  or the `--musescore` option.
- The arrangement parser requires `score-partwise` MusicXML.

The parser reads parts, voices, staves, pitches, offsets, durations, ties, time
signatures, key signatures, and measure numbers. Rests contribute to the
timeline but do not become `NoteEvent` objects. Grace notes are ignored.

Time uses exact `Fraction` values with one quarter note as the unit. A regular
measure advances by the duration implied by its active time signature. A
measure marked `implicit="yes"`, such as a pickup, advances by its actual used
duration.

### 3.2 Output

The writer creates a two-part MusicXML 4.0 score. Each part is monophonic,
non-transposing, and written with a treble clef. MXL wraps the MusicXML in a
standard container. MSCZ output is produced by converting the generated
MusicXML with the MuseScore CLI.

Immediately before writing, note starts and ends are quantized to one quarter
of a quarter note, producing a sixteenth-note grid. This favors readable,
stable MuseScore notation over preservation of very small fractional
durations. Notes crossing measure boundaries are split and tied, and empty
regions are filled with rests.

The current writer rebuilds measures from time and key signatures. It does not
copy source layout, lyrics, dynamics, articulations, or slurs.

## 4. ScoreIR

The main data structures are:

```text
ScoreIR
  title
  parts[]
  source_voices[]
  notes[]
  time_signatures[]
  key_signatures[]
  measures[]
  duration

NoteEvent
  pitch
  onset: Fraction
  duration: Fraction
  source_voice
  tie_prev / tie_next

MeasureInfo
  index
  number
  start / end

HarmonicSlice
  start / end
  active_notes[]
  chord
  beat_weight
  is_cadence

SustainSpan
  pitch
  start / end
  span_type: pedal | suspension | plain_sustain
  resolves_to
  source_voice

AssignedNote
  voice: first | second
  pitch
  onset / duration
  origin
  cost_breakdown
```

`origin` is one of `melody`, `tension_selection`, `bass_selection`,
`span_continuation`, or `rhythm_reduction`. Together with `cost_breakdown`, it
supports tracing decisions and tuning weights.

## 5. Harmonic slices

The score is divided at every note onset and release. Each slice contains all
`NoteEvent` objects sounding during that interval, so a release updates the
harmonic state even when no new attack occurs.

Metric weight is derived from the active time signature:

- beat boundary: `1.0`;
- half-beat boundary: `0.5`; and
- other subdivisions: `0.25`.

## 6. Harmony estimation

The analyzer has two backends, selected with `--harmony-backend`. The default
is `internal`.

### 6.1 Internal backend

For each slice, the analyzer compares its integer pitch-class set against all
twelve roots of these templates:

- major: `{0, 4, 7}`
- minor: `{0, 3, 7}`
- augmented: `{0, 4, 8}`
- dominant seventh: `{0, 4, 7, 10}`
- major seventh: `{0, 4, 7, 11}`
- minor seventh: `{0, 3, 7, 10}`
- diminished seventh: `{0, 3, 6, 9}`
- half-diminished seventh: `{0, 3, 6, 10}`

Confidence combines observed-set coverage, template coverage, a root-position
bass bonus, and the amount of pitch evidence. The default threshold is `0.65`.
A low-confidence slice inherits the last confident chord while retaining the
current bass and a reduced confidence value.

Intervals outside the selected template are recorded as `b9`, `9`, `#9`,
`11`, `#11`, `b13`, or `13`.

The internal backend marks a cadence when a confident major or dominant-
seventh chord moves up a perfect fourth to a major or minor chord. Because this
backend has no key context, it can mistake a tonic-to-subdominant progression
for a cadence.

### 6.2 music21 backend

The exact internal timeline and harmonic slices remain authoritative. Each
slice is converted to a music21 `Chord`. The analyzer groups material into
sixteen-quarter-note contexts and runs `Stream.analyze("key")` to estimate a
local key. When local correlation is weak, it prefers a key derived from the
notated MusicXML key signature.

When music21 clearly identifies one of the supported triad or seventh-chord
qualities, its root and `romanNumeralFromChord()` result are stored in
`ChordLabel`. Unsupported or ambiguous qualities fall back to the internal
estimate. Confidence and low-confidence interpolation remain shared with the
internal backend.

A cadence requires a dominant-to-tonic Roman-numeral progression. A resolved
secondary dominant, such as `V/x` moving to `x`, is also treated as a cadence.
This avoids the internal backend's tonic-to-subdominant false positive.

Neither backend currently uses MusicXML `<harmony>` elements or manual chord
overrides.

## 7. Sustain spans

Consecutive notes with the same pitch and source voice are joined when no gap
exists. A chain becomes a candidate when it contains a tie or when a single
note lasts at least three quarter-note units.

- A candidate that begins as a chord tone, becomes a non-chord tone, and then
  resolves by a semitone or whole tone is a `suspension`.
- A candidate lasting at least eight quarter-note units and acting as the root
  or fifth in most related slices is a `pedal`.
- Other candidates are `plain_sustain`.

Selecting an active span pitch during voice assignment receives a continuity
bonus. Active span pitches are indexed once per slice so the search does not
scan every span for every candidate path.

## 8. Two-voice reduction

### 8.1 Melody

The lowest-numbered source voice in the first part is treated as the melody.
When that source voice is active, its highest pitch is assigned to the first
output voice. Octave alternatives are generated only when the source pitch is
outside the configured range.

When the melody source rests, the first output voice also rests. Accompaniment
material is not promoted into it; the second output voice carries the source
backbone alone.

### 8.2 Second-voice candidates

The source pitch selected for the first voice is removed from the currently
sounding material. Octave transpositions of the remaining notes are generated
within G3–A6, the currently hard-coded range for the second voice, and a rest
is included as a choice. Duplicate pitches are removed. The choices are then
locally ranked and pruned to at most eight, while retaining the
greedy-baseline choice when possible.

The current range is G3–E7 for the first voice and G3–A6 for the second. These
values cannot be changed through `arrange`. The Python API accepts a
replacement range-settings object, but this has not yet been generalized into
an instrument-profile abstraction.

### 8.3 Search

A greedy pass first chooses the lowest-cost pair at every slice. The second
voice is then searched again within bounded segments.

A segment ends when any of the following occurs:

- its duration reaches approximately eight quarter-note units;
- the current slice is marked as a cadence; or
- the next slice is silent.

At each slice, the search expands at most eight choices including a rest. Paths
that reach the same state retain only the lowest-cost representative, after
which only the best 24 accumulated paths continue. The state contains the
previous pitch, the pitch before it, and the most recent strong-beat anchor.
These values carry across segment boundaries to reduce discontinuity.

At the end of each segment, the single best path is committed. This is a
state-merging beam search and does not guarantee a globally optimal path.

### 8.4 Cost function

Default weights are:

| Term | Weight | Purpose |
|---|---:|---|
| tension loss | 8.0 | Preserve thirds, sevenths, and other quality-defining tones |
| range violation | 100.0 | Strongly discourage pitches outside configured ranges |
| leap | 1.0 | Penalize squared distance from the previous pitch |
| cadence root | 4.0 | Preserve a root or fifth at a cadence |
| voice crossing | 100.0 | Discourage the second voice from moving above the first |
| spacing | 2.0 | Discourage unisons, seconds, and spacing beyond two octaves |
| span continuity bonus | 2.0 | Preserve sustains, suspensions, and pedal tones |
| large leap | 3.0 | Add a penalty for second-voice leaps greater than seven semitones |
| direction change | 0.5 | Discourage large reversals of direction |
| weak-beat change | 1.5 | Discourage unnecessary motion on weak metric positions |
| common-tone bonus | 0.6 | Reward retaining a common tone |
| structural leap | 6.0 | Discourage long-distance movement between strong beats |

The objective is a coherent second melodic line as well as a harmonically
meaningful reduction. Candidates are restricted to source pitches and their
octave transpositions.

## 9. Rhythm reduction

Second-voice attacks are grouped by beat. By default, a beat containing more
than two attacks keeps only the attack with the greatest metric weight, using
proximity to the beat boundary as the tie-breaker. The retained note is
extended to the end of the beat. The number of removed attacks and the
`rhythm_reduction` origin are recorded.

The first-voice rhythm is not changed by this stage.

## 10. Validation

The final assignment is checked for:

- first- and second-voice range;
- overlapping notes within either voice; and
- the second voice crossing above the first.

When violations remain, range and crossing weights are multiplied by ten on
each retry, then assignment and rhythm reduction are rerun. The default retry
limit is three. A final repair also lowers crossing second-voice notes by
octaves while they remain in range. The result records remaining issues and
the retry count.

## 11. Progress and performance

`arrange` flushes progress messages to standard error for input, parsing,
slicing, harmony estimation, span detection, assignment, rhythm reduction,
validation, and writing. After each search segment, assignment progress
reports the source measure number, completed-measure count, and percentage.
`--quiet` disables these messages.

The search is bounded to eight choices including a rest, a beam width of 24,
and segments of approximately eight quarter-note units. Sustain lookup is
pre-indexed by slice. These limits prevent exponential state growth on long
scores but do not guarantee a globally optimal result.

## 12. Configurable values

The CLI currently exposes:

| Commands | Option | Default |
|---|---|---:|
| `analyze`, `spans`, `assign` | `--confidence-threshold` | 0.65 |
| `analyze`, `spans`, `assign`, `reduce`, `validate`, `arrange` | `--harmony-backend` | internal |
| `reduce` | `--attack-threshold` | 2.0 |
| `validate` | `--max-retries` | 3 |
| `arrange` | `--quiet` | false |

Cost weights, ranges, candidate count, beam width, and segment duration are
available through Python APIs or implementation defaults. They are not yet
exposed through a configuration file or `arrange` options.

## 13. Current limitations and next steps

- Melody selection assumes the first source voice in the first part.
- Internal chord estimation has weak contextual understanding of keys,
  modulation, and borrowed harmony.
- The music21 key window has a fixed sixteen-quarter-note duration, so it
  handles modulation boundaries and brief tonicizations coarsely.
- Segment boundaries use duration, cadence, and silence rather than source
  slurs or phrase marks.
- Candidate and path pruning can discard a musically preferable route.
- Sixteenth-note output quantization loses fine rhythmic detail.
- Beat-level second-voice reduction can erase meaningful inner motion.
- The output does not preserve simultaneous notes, ornaments, expression,
  layout, or lyrics.
- Harmonic and melodic quality has not been evaluated against labeled data or
  quantified human judgments.

Potential next steps include explicit melody-source selection, a more
contextual harmony model, source phrase-mark support, selectable instrument
profiles, CLI controls for beam width and weights, expression copying, and
broader MusicXML round-trip tests.
