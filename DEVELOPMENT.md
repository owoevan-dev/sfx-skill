# Development

## Layout

```
skills/sound-effects/      the skill: SKILL.md, references/, examples/ (original example projects), scripts/sfx.py and
                           scripts/sfxlib/
tests/                     unit and integration tests (unittest), helpers.py; browser/ opens the audition page in
                           real browsers
tools/                     check_all.py (every test), make_fsd50k_packs.py (the packs of FSD50K's CC0 recordings a
                           release publishes), ci_render.py and ci_compare.py (one project rendered alike on every
                           system), check_release.py (what a release depends on outside the repository)
assets/brand/              the logo and icons
work/                      scratch output of tools and tests (not committed, never cleared by scripts)
```

## Requirements

- Python 3.10 or later with NumPy, the same as music-production. No other package.
- For FLAC, Ogg and MP3 exports and decoding recordings that are not WAV: an FFmpeg (`--ffmpeg`,
  `SFX_SKILL_FFMPEG`, the one music-production's configuration names, or `ffmpeg` on the PATH). For instrument and Surge
  layers and their tests: an installed music-production skill, its FluidSynth, sample banks and Surge XT (see
  [MUSIC_PRODUCTION_CONTRACT.md](MUSIC_PRODUCTION_CONTRACT.md)). For sample layers on real recordings: a library
  holding the release's packs (`sfx.py samples --library LIB --download-fsd50k DATA`, 2.9 GB), which the worked
  example and its render test use. Without them those tests are skipped.

`tools/local.json` (not committed) names the local paths:

```json
{
  "music_skill": ".../skills/music-production",
  "music_config": ".../local-config.full.json",
  "tmp": "a folder for TMP and TEMP of every child process",
  "work": ".../sfx-skill/work",
  "fsd50k": "a sample library holding FSD50K's evaluation set (sfx.py samples --download-fsd50k)"
}
```

## Commands

```bash
python tools/check_all.py            # all tests
python tools/check_all.py --quick    # without the tests that call FFmpeg or music-production
```

## Rules of the repository

- Every behaviour and every fix starts as a failing test; once it passes, the fix is taken out once to show that the
  test catches it.
- Scripts and tests write into their own folders and never delete files. The skill's `clean` command lists first and,
  only with `--trash`, moves whole folders into the project's `.sfx-trash`, which it never empties; a test checks that
  the skill holds no call that deletes a file. Scratch folders under `work/` are cleared by hand.
- `tests/test_export.py` also imports a Godot export into Godot itself when it finds a Godot 4 console build
  (`SFX_TEST_GODOT`, or `work/godot/bin/`; from the engine's official releases, its SHA-512 checked); without it that
  test is skipped. `SFX_TEST_REQUIRE=ffmpeg,node,godot`, as CI sets it, makes a missing tool a failure instead.
- Files in this repository are in English, READMEs aside, and carry no local paths or personal data
  (`tests/test_repository.py`).
- music-production is used only through its command line and configuration file, never by importing its modules.

## Continuous integration

`.github/workflows/tests.yml` runs on every push to main and every pull request: the tests on Ubuntu (Python 3.10, 3.13
and 3.14), macOS and Windows with FFmpeg and, on one job, Godot; one fixed project rendered on each system and compared
sample by sample; the audition page opened in Chromium, Firefox and WebKit. `release-check.yml` runs when a release is
published, every week and by hand: the release's packs and the dataset's record answer, the packs download, import and
render on each system, and instrument layers work on the latest music-production release.

## Releasing

A release may publish packs of FSD50K's CC0 recordings, so that users need not fetch the whole dataset:

1. Import both sets into one library (`sfx.py samples --library LIB --add-fsd50k DATA --set eval`, then `--set dev`).
2. `python tools/make_fsd50k_packs.py LIB OUT` writes the parts, their index, the credits, the notice and the sums, and
   prints the index's SHA-256.
3. Publish everything in `OUT` as the release's assets, and set `RELEASE_URL` and `RELEASE_INDEX_SHA256` in
   `skills/sound-effects/scripts/sfxlib/fsd50k.py` to that release and that sum, so that the skill fetches the packs and
   accepts no other index.
