# Changelog

## Unreleased

- `check` flags a loop within 3 dB of the loudest one-shot (`loop_level`). The level step brings a bed to the one-shots' loudness,
  so an untrimmed one sits on top of them. The worked example's sea now sits 9 dB under its set.
- The READMEs and the guide give the library's whole disk cost: a 3.1 GB download, 4.7 GB once imported, about 8 GB until the
  downloaded archives are removed.

## 0.1.0 (2026-11-22)

The first version.

**Designing sounds.** An agent writes a game's sounds into a project file, as layers it can read and change: oscillators,
two-operator FM, chip voices after the NES, noise, struck modes, bubble populations, wind, dry notes from an installed
music-production skill's sample banks, and recordings. Each layer has its own pitch curve, envelope, filter, drive,
vibrato and tremolo; each sound a short chain (console filters, EQ, tilt, saturation, compressor, delay, room); a project
a shared style. 43 recipes (interface, retro, impact, sci-fi, magic, ambience) are starting points, each with the rules
it encodes and their sources.

**Recordings.** `sfx.py samples` fetches and imports 7,405 recordings of the FSD50K dataset that their authors dedicated
to the public domain (CC0), screened for quality and packed with this release, each with its author, labels and measures,
and imports folders or zips
the user already has, showing them the licence text that came with the sounds and importing once they confirm it. What
a licence asks for is carried into every record of the sounds made with it. A recording can be trimmed, reversed, moved
up to four octaves, stretched in time without changing pitch, and cleaned of steady noise.

**Working without ears.** `samples --find` ranks recordings by whole-word match and how clean they measured, `--events`
lists a recording's strikes as takes ready to paste into a layer, and `--map` shows its level, pitch and noise over time.
A render reports each layer's peak and when it comes. `scripts/sfxkit.py` holds the usual building blocks for writing a
whole set in one script.

**Rendering and checking.** Variants of each sound, one loudness for a set under a true-peak ceiling (one-shots matched by
their loudest 200 ms, loops by integrated loudness, and the set can be held at a level you choose), seamless loops, 48 kHz
24-bit masters, measured traits for the agent to read, flags for faults it cannot hear (clicks, cut endings, DC, a loudest
moment in the wrong place, a layer that runs out of its recording, variants landing at different times, an event buried
under a loop's bed), spectrogram pictures, and a single-file audition page where the developer listens and leaves notes.

**Export.** Files and a manifest of suggested settings for Godot, Unity, Unreal, Roblox and the web, with notes on what to
watch for when playing them in a browser.

**Keeping a project tidy.** `clean` lists old renders and moves them whole into the project's `.sfx-trash`, from where they
can be moved back. Nothing is deleted.
