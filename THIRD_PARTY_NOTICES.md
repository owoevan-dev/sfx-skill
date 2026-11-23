# Third-party notices

Nothing in this list is bundled in the repository, apart from what [Code](#code) and [Brand](#brand) name.

## Code

A few small helpers (WAV reading and writing, loudness measurement) are copied from the
[music-production](https://github.com/owoevan-dev/music-skill) skill under its MIT licence:
[skills/sound-effects/LICENSES/music-production-MIT.txt](skills/sound-effects/LICENSES/music-production-MIT.txt). The files
that hold them say so.

## Software

| Software | Licence | How it is used |
|---|---|---|
| [NumPy](https://numpy.org) | BSD-3-Clause | required; installed from PyPI |
| [FFmpeg](https://ffmpeg.org) | LGPL or GPL, by build | optional; your own copy, run as a separate program for Ogg, MP3 and FLAC |
| [music-production](https://github.com/owoevan-dev/music-skill) with FluidSynth | MIT and GPL-3.0-or-later; FluidSynth LGPL-2.1-or-later | optional; run as separate programs for layers on sampled instruments |

## Recordings

This repository holds no recordings. With the user's agreement, the skill fetches a selection of FSD50K from this project's
release, or the dataset itself from Zenodo.

**FSD50K**, by Eduardo Fonseca, Xavier Favory, Jordi Pons, Frederic Font and Xavier Serra, created at the Music Technology
Group of Universitat Pompeu Fabra: <https://doi.org/10.5281/zenodo.4060432>. If you use it, or part of it, please cite the
authors' paper:

> E. Fonseca, X. Favory, J. Pons, F. Font and X. Serra, "FSD50K: An Open Dataset of Human-Labeled Sound Events", *IEEE/ACM
> Transactions on Audio, Speech, and Language Processing*, vol. 30, pp. 829-852, 2022.

```bibtex
@article{fonseca2022FSD50K,
  title={{FSD50K}: an open dataset of human-labeled sound events},
  author={Fonseca, Eduardo and Favory, Xavier and Pons, Jordi and Font, Frederic and Serra, Xavier},
  journal={IEEE/ACM Transactions on Audio, Speech, and Language Processing},
  volume={30},
  pages={829--852},
  year={2022},
  publisher={IEEE}
}
```

- The dataset as a whole is licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), which gives no
  warranties. Its authors have no part in this project and do not endorse it.
- What this project changed: it selected recordings (those under CC0 that pass its own quality screens and the few its
  examples play, leaving out those whose source, terms of use or content left room for doubt when their descriptions were
  read), reorganised the dataset's labels and metadata for them and added its own measurements. The audio files are unchanged.
- Each recording keeps the licence its author gave it on [Freesound](https://freesound.org). The skill uses those dedicated
  to the public domain under [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/), and records each one's author,
  title and page. The release lists them all in `CREDITS.csv`.

## Brand

The wordmark in `assets/brand` is set in [Noto Serif](https://fonts.google.com/noto) (SIL Open Font License 1.1) and outlined
into paths.

## Sources

The rules the skill states are cited where they appear, in `skills/sound-effects/references/`. Game engines and products
named in this repository belong to their owners, and this project is not affiliated with them.
