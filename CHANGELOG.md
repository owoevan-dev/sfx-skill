# Changelog

## 0.2.0

- **Surge XT layers.** With [music-skill](https://github.com/owoevan-dev/music-skill) installed, a `surge` layer plays one of the
  2,915 Surge XT patches that take a plain note, with its macros, pitch bend and mod wheel, then the filter, envelope and chain every
  layer has. Two plays of a note differ, so each take is recorded once (`render --record-takes`) into `surge_takes` beside the
  project, and every later render plays that file: a project with its takes renders the same without Surge. `sfx.py surge` finds
  patches by word, role and measured character, `doctor` says whether Surge XT is ready, and the guide shows how to build casts,
  charges, heals, shimmers and portals with them.
- **The game's music.** `sfx.py music song.json` reads a music-skill project: tempo, meter, sections by bar and second, the harmony
  and plan it declares, and what each part holds. `--track ID --bars 5-6` prints those bars' written notes, ready for an instrument
  or Surge layer, so a pickup can quote the game's theme. It works out no key and changes no music.
- **Music on the audition page.** A track picked on your computer loops under the sounds at its own level. It stays in the page.
- **The page in your language.** English, Traditional and Simplified Chinese, Japanese and Korean, from the browser's language, with a
  menu. The feedback is the same in every language.
- **A smaller page.** With FFmpeg, the page holds each clip as FLAC where that decodes to the same samples and is smaller than the WAV
  (a very short clip stays WAV): about half the size for a game's set of sounds.
- The audition page open in two windows keeps both windows' feedback: each change is written into what was saved last.
- **Search.** Recordings about as clean as each other come in an order of the project's own, so two games start from different
  recordings. When fewer than five recordings hold every word, the list goes on with those holding some of them, marked (in `--json`,
  `files` and `matched` still hold only recordings with every word, and the others are in `some_words`). Among recordings that pass
  the same screens, those labelled only as music come after the others unless you ask for music; then the better match comes first,
  and the order of the project's own sorts only recordings within 6 dB of each other's cleanliness (the range between body and
  noise floor, counted up to 60 dB).
- `doctor --library LIB` reports on a sample library kept elsewhere.
- A sound still at its recipe's default parameters gets a note (not a flag) asking to set them for the game.
- `check` flags an attack inside an instrument layer's sample that none of its notes explains (`stray_attack`): a tick left in a
  sample, which the click test let through.

## 0.1.3 (2026-11-23)

- The library now holds 6,718 recordings. Two more whose source or author's wishes left room for doubt were taken out. As before, the
  skill no longer offers them and a library that imported them earlier keeps the files, and a mistyped number no longer suggests one.
- The credits no longer repeat an author name that is an email address. The recording's page still credits them.
- A search with no Latin letters or digits now says so and asks for English words. It used to match the whole library.
- When a game needs sounds that are hard to synthesize, the agent now suggests the recordings once and mentions that you can hand it
  sounds of your own.
- The removal form also works for someone whose sound was uploaded by another person, with no Freesound account needed, and a recording
  comes out as soon as we can rather than waiting for the next release.
- A file that is no longer on the release stops the download with a clear message, instead of retrying as if the connection had dropped.
- The guide now says that with `reverse`, `offset` counts from the recording's end, and that a check with no flags does not prove a sound
  is clean.

## 0.1.2 (2026-11-23)

- The library now holds 6,720 recordings in the same 190 classes. 283 more whose source, terms of use or content left room for
  doubt when their descriptions were read were taken out, to keep it clean. As in 0.1.1, the skill no longer offers them, and a
  library that imported them earlier keeps the files. The download is now 2.9 GB, 4.3 GB once imported, about 7.2 GB until the
  downloaded archives are removed.

## 0.1.1 (2026-11-23)

- The library now holds 7,003 recordings. 402 whose source, terms of use or content left room for doubt when their descriptions
  were read were taken out, to keep it clean. Search, the packs and the dataset import no longer offer them, and a project that
  names one is asked to choose another. A library that imported them earlier keeps the files.
- The READMEs and the packs' notice say plainly that a recording's CC0 mark is its author's own statement, with no warranty.
- `check` flags a loop within 3 dB of the loudest one-shot (`loop_level`). The level step brings a bed to the one-shots' loudness,
  so an untrimmed one sits on top of them. The worked example's sea now sits 9 dB under its set.
- The READMEs and the guide give the library's whole disk cost: a 3.0 GB download, 4.5 GB once imported, about 7.4 GB until the
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
