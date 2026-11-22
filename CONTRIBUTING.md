# Contributing

Thanks for wanting to help. sfx-skill is maintained by one person and has a fairly fixed direction, so here is how help works best.

## Questions and ideas: Discussions

Questions about using or setting up the skill go to [Q&A](https://github.com/owoevan-dev/sfx-skill/discussions/categories/q-a), where an answer can be marked, and ideas to [Ideas](https://github.com/owoevan-dev/sfx-skill/discussions/categories/ideas). Sounds you made with the skill are welcome in [Show and tell](https://github.com/owoevan-dev/sfx-skill/discussions/categories/show-and-tell).

## Issues are welcome

- **A render that went wrong.** Paste the command, the error or the flags from `check.json`, and say what you expected. Attach the project file if you can share it.
- **A sound that is wrong.** Say which sound and what you hear: a click, a thin hit, a loop you can hear join. Listening notes like these are the most useful feedback the project gets.
- **A recording of yours.** If you made a recording in the release and would rather it were left out, use the [removal form](https://github.com/owoevan-dev/sfx-skill/issues/new?template=remove_recording.yml).

Write in any language you like.

## Pull requests

Pull requests from outside the project are closed for now. If you have a fix, describe it in an issue or paste the patch there. When an idea or a patch from an issue is used, the changelog says whose it was. By posting a patch you agree that it may be used under the project's MIT licence.

## What the project will not take

- An audio-generation model inside the skill. Sounds are designed, not generated.
- Recordings under NC, ND or unclear licences, or sounds taken from commercial libraries.
- Other people's sound packs, even free ones: we would rather send users to their creators.

## Running the tests

```bash
python3 -m venv .venv && . .venv/bin/activate        # Windows: py -m venv .venv, then .venv\Scripts\activate
python -m pip install -r skills/sound-effects/requirements.txt
python -m pip install pyyaml
python tools/check_all.py
```

Tests that need FFmpeg or Godot are skipped when those are missing. [DEVELOPMENT.md](DEVELOPMENT.md) has the rest.
