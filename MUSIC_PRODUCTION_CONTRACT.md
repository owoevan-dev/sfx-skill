# What sound-effects uses from music-production

sound-effects needs music-production only for instrument layers (its FluidSynth and sample banks), for finding Surge patches
(`sfx.py surge` reads its catalog) and for recording the takes of Surge layers (its Surge XT); FFmpeg can come from its configuration or from elsewhere (`--ffmpeg`,
`SFX_SKILL_FFMPEG`, the PATH: sfxlib/ffmpeg.py), and everything else works without it, also a project whose Surge takes
are already recorded. `sfx.py music` reads a music-production project file and needs nothing else of it. It uses
music-production only through its configuration file, its command line and the data files named below, and never imports
its modules. This list is the whole contract: each item is exercised by
`tests/test_music_production_contract.py` against the installed copy, so a change on music-production's side shows as
a failing test. Written against music-production 0.8.0; checked again against 0.9.1 on 2026-11-22, and the Surge part
written against 0.9.2.

## Finding music-production

In this order: a path given with `--music-skill`; a `music-production` folder beside this skill (the two installed in
the same skills folder); `~/.claude/skills/music-production`. A folder counts when it holds `scripts/studio.py`.

## Configuration file (the one music-production's `bootstrap.py` writes)

| Field | Used for |
|---|---|
| `ffmpeg.path` | one of the places FFmpeg is found (encoding exports, decoding recordings that are not WAV) |
| `fluidsynth.path`, `fluidsynth.sha256` | the FluidSynth program that plays instrument layers (`sfx.py render --config`); the hash goes into each layer's cache key |
| `soundfont.path`, `.license`, `.sha256` | the default bank (`"soundfont": "default"` in an instrument layer) |
| `soundfonts.<name>.path`, `.license`, `.sha256` | the named banks; a bank is used only when its licence starts with CC0, University of Iowa, Unlicense or MIT (music-production's soundbanks.md: music rendered with the banks its setup installs needs no credit). `sfx.py banks` lists them all, marking those it may not use |
| `surge.path`, `.binary`, `.sha256`, `.version`, `.catalog` | the Surge XT plugin (its bundle, and the file inside it checked by hash), and the folder of its 3,008 patches |
| `pedalboard.path`, `.record_sha256`, `.version` | the folder of the pedalboard package the host loads; each of its files is checked against its `RECORD` before a take is recorded |

The FFmpeg it names must have these encoders, decoders and filters: `pcm_s16le`, `pcm_s24le`, `pcm_f32le`,
`libvorbis`, `libmp3lame`, `flac`; `vorbis`, `mp3`, `flac`; `ebur128`, `aresample`.

## FluidSynth command line (instrument layers)

`fluidsynth -n -i -q -R 0 -C 0 -g 0.5 -r 48000 -o synth.midi-bank-select=mma -O float -T wav -F OUT.wav BANK.sf2 IN.mid`:
no MIDI input, no shell, quiet; its own reverb and chorus off (the layer comes back dry); 48 kHz floating point; MMA
bank select as music-production uses it. The MIDI file is written by `sfxlib/midi.py` (format 0, 960 ticks a quarter
at 60 quarters a minute) and ends three seconds after the last note, so releases ring out.

## FFmpeg command lines (sfx.py export and sample layers)

| Command line | Used for |
|---|---|
| `ffmpeg -hide_banner -loglevel error -i MASTER.wav -c:a libvorbis -q:a 5 -n OUT.ogg` | Ogg Vorbis from a 24-bit master |
| `ffmpeg -hide_banner -loglevel error -i MASTER.wav -c:a libmp3lame -q:a 2 -n OUT.mp3` | MP3 from a 24-bit master |
| `ffmpeg -hide_banner -loglevel error -f wav -i pipe:0 -c:a flac -n OUT.flac` | FLAC from the 16-bit dithered WAV bytes given on standard input |
| `ffmpeg -hide_banner -loglevel error -i FILE -f f32le -c:a pcm_f32le -ac C -ar 48000 pipe:1` | every encoded file decoded back to float frames for measurement |
| `ffmpeg -hide_banner -i FILE` (its stderr: `Audio: ..., RATE Hz, mono` or `stereo`) | a library recording's own rate and channels (`sfx.py samples --add`, sample layers) |
| `ffmpeg -hide_banner -loglevel error -i FILE -f f32le -c:a pcm_f32le -ac C pipe:1` | a library recording (Ogg, FLAC, MP3) decoded at its own rate; this skill resamples it to 48 kHz itself |

Observed with the FFmpeg of music-production's configuration (9.0.2): MP3 files decode to their master's length (the
encoder's gapless information is honoured); Ogg one-shots of a fraction of a second may decode up to one long Vorbis
block longer or shorter at their end, and ten-second loops to their exact length. The export reports each file's
difference rather than relying on either.

## Commands

| Command | Used for | Expected |
|---|---|---|
| `studio.py doctor --config <config>` | `sfx.py doctor` reports whether music-production's own setup passes | exit status 0 when it passes |
| `studio.py validate song.json` | the contract test holds `sfx.py music`'s count of a project against it | exit status 0 and JSON with `bars`, `tracks`, `notes` and `seconds` (the score plus `tail_seconds`) |

## The project file (`sfx.py music`)

A `song.json` of schema `music-production/v1`, read as data.

| Field | Used for |
|---|---|
| `clock.bars`, `.meter`, `.tempo`, `.tail_seconds` | bars, quarter notes a bar and seconds along the step tempo map; the score's length, apart from the render's |
| `clock.rubato` | said, and named when a quote leaves it out; its curve is not applied |
| `title`, `sections[].id`, `.start_bar` (from 0), `.bars`, `.intent` | printed, with bars counted from 1 |
| `harmony[].bar` (from 1), `.beat` (quarter notes into the bar), `.symbol`, `.transpose_semitones`, or an older free-form value | printed as declared, a transposed chord beside its symbol |
| `plan` | printed as written |
| `tracks[].id`, `.name`, `.role`, `.engine`, `.drums`, `.range` | printed; `drums` and the engines `drum_synth` and `noise` have no pitch classes |
| `tracks[].notes[].id`, `.on`, `.duration`, `.pitch` | the written notes, in quarter notes |
| `tracks[].performance.notes.<id>.velocity` | a quoted note's velocity |
| `.performance.notes.<id>.timing_ms`, `.gate`, `.performance.swing`, `.offset_ms`, `.bend`, `tracks[].key_map` | named when a quote leaves them out |

## Surge host (recording a take)

`python <music-production>/scripts/surge_host.py JOB.json`, with the scripts folder as the working directory. It is a GPL-3.0-or-later program of music-production that
runs as its own process: only files pass between it and this skill. The job, the patch copy and the take it writes are in the
render's own folder (`surge_work`), and emptied once the take is read; `surge_takes` beside the project holds only the takes and
their receipts. `JOB.json` holds:

| Field | Value |
|---|---|
| `plugin`, `pylib` | `surge.path`, `pedalboard.path` |
| `patch` | a copy of the checked patch file's bytes, with its tempo-synced delays and LFOs set free for the layer's `bpm` |
| `out` | the WAV to write: 32-bit float, stereo, at `rate`, of `duration` x `rate` frames less `trim` (within 2), or the take is not kept |
| `rate` | 48000 |
| `events` | `[hex MIDI, seconds]`: note on and off, pitch bend, CC1; each 0.1 s late, and at one time controls first, then note-offs, then note-ons |
| `duration`, `trim` | 0.1 s + the last note-off + the layer's `tail`; 4800 frames, which take that lead off again |
| `macros`, `bend_range` | the patch's macros (`"1"` to `"8"`, 0 to 1); the bend range in semitones |

## Surge catalog

`references/surge-catalog.json`, under `presets.<id>`: `path`, `bytes`, `sha256` (the patch file inside `surge.catalog`), `transpose` (added to each written key), `flags` (`MPE` and
`silent on a plain note` are not offered), and for listing `name`, `category`, `role`, `source`, `license`, `character`, `measured`, `macros`, `wheel`.

## Bank files

The SF2 and SF3 files the configuration names are played by FluidSynth (above) and read only for their preset headers
by `sfx.py banks`: the `phdr` chunk of the `pdta` list, as the SoundFont 2.04 specification lays it out (38-byte
records: name, program, bank, ...; the sample data is skipped). Every configured bank lists its presets this way.

## Output files

None but the host's take above: instrument layers are rendered by this skill's own FluidSynth command line, not by music-production's renders.

## Code copied from music-production (MIT)

| Here | From music-production 0.8.0 | What |
|---|---|---|
| `sfxlib/wav.py` (reader part) | `scripts/musiclib/wavfile.py` | bounded RIFF/WAVE reader with `smpl` loops |
| `sfxlib/loudness.py` (K-weighting part) | `scripts/musiclib/measure.py` | BS.1770 K-weighting at 48 kHz, momentary and integrated loudness |
| `sfxlib/surge.py` (`at_tempo`, `board_intact`), from 0.9.2 | `scripts/musiclib/surge.py` | a patch's tempo-synced parameters set free; a package checked against its `RECORD` |
| `sfxlib/music.py` (`Clock`), from 0.9.2 | `scripts/musiclib/model.py` | seconds along the step tempo map, each tempo held as whole microseconds a quarter note |

Each copied block keeps music-production's MIT notice in its header.
