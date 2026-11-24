"""sound-effects: synthesize original game sound effects from a project file, measure them and audition them.

Commands (python sfx.py COMMAND --help for each):
  recipes    list the recipes and their parameters
  validate   check a project file
  expand     write a project in which recipe sounds become explicit layers you can edit
  render     synthesize every variant, level the set, write the masters, check them
  check      measure a render again and list its flags
  audition   write the render's audition page again
  export     write a render's files for a game engine (godot, unity, unreal, roblox, web, generic)
  clean      list old renders the kept ones no longer need; with --trash move them to .sfx-trash
  banks      list the sample banks of music-production's configuration that recorded layers may use, and their presets
  surge      find the Surge XT patches a surge layer can play, with their measured character
  music      read a music-production project or a MIDI file: tempo, meter, parts; quote a part's written notes
  samples    fill and search the sample library: FSD50K's CC0 recordings, sounds a person has, a recording's time map
  readout    read each sound of a render (or one WAV) as numbers: octave bands over time, tonal lines, events
  spectrogram  draw a picture of each sound of a render (or of one WAV) to look at: level, spectrum, loudest moment
  doctor     check what synthesis needs, and FFmpeg and music-production when named
"""
import argparse
import errno
import json
import os
import platform
import sys
import textwrap
from pathlib import Path


def _console_safe():
    """A recording's title, an author's name or a path may hold characters this console's code page cannot show (cp950
    on a Traditional Chinese Windows, for one): they print as escapes instead of stopping the command."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(errors='backslashreplace')


if __name__ == '__main__':
    _console_safe()
if sys.version_info < (3, 10):
    print(f'sound-effects needs Python 3.10 or later; this is {platform.python_version()} ({sys.executable}).')
    sys.exit(1)
try:
    import numpy
except ImportError:
    print(f'sound-effects needs NumPy, which this Python ({platform.python_version()}, {sys.executable}) does not have. '
          f'Install it: "{sys.executable}" -m pip install -r "{Path(__file__).resolve().parents[1] / "requirements.txt"}"')
    sys.exit(1)

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sfxlib import VERSION, export, fsd50k, record  # noqa: E402
from sfxlib.cli.cleanup import cmd_clean  # noqa: E402
from sfxlib.cli.common import EVENTS_SHOWN, FIND_SHOWN  # noqa: E402
from sfxlib.cli.doctor import _ffmpeg_line, _library_line, cmd_doctor  # noqa: E402,F401
from sfxlib.cli.library import cmd_samples  # noqa: E402
from sfxlib.cli.renders import (cmd_audition, cmd_check, cmd_expand, cmd_export, cmd_readout, cmd_render,  # noqa: E402
                                cmd_spectrogram, cmd_validate)
from sfxlib.cli.sources import cmd_banks, cmd_music, cmd_recipes, cmd_surge  # noqa: E402


def build_parser():
    parser = argparse.ArgumentParser(prog='sfx.py', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--version', action='version', version=f'sound-effects {VERSION}')
    sub = parser.add_subparsers(dest='command', required=True, metavar='COMMAND')

    rec = sub.add_parser('recipes', help='list recipes', description='List the recipes with their parameters, '
                         'ranges and how each varies between variants.')
    rec.add_argument('--category', help='only this category (ui, retro, scifi, magic, impact, ambience)')
    rec.add_argument('--name', help='one recipe in full, with the rules it encodes and their sources')
    rec.add_argument('--layers', action='store_true', help="with --name: the recipe's layers and chain at its defaults, as JSON")
    rec.add_argument('--json', action='store_true', help='print JSON')
    rec.set_defaults(func=cmd_recipes)

    val = sub.add_parser('validate', help='check a project file', description='Check a project file against '
                         'schema sound-effects/1; every problem is listed with its place and the nearest known name.')
    val.add_argument('project', help='the project file (JSON)')
    val.set_defaults(func=cmd_validate)

    exp = sub.add_parser('expand', help='turn recipe sounds into layers', description='Write a new project in '
                         'which recipe sounds (all, or those named) are explicit layers and chain, at their written '
                         'values, so every value can be edited by hand.')
    exp.add_argument('project', help='the project file')
    exp.add_argument('--output', required=True, help='a new project file')
    exp.add_argument('--sound', action='append', help='only this sound id (repeatable)')
    exp.set_defaults(func=cmd_expand)

    ren = sub.add_parser('render', help='render a project', description='Synthesize every variant of every '
                         'sound, level the set, write 48 kHz 24-bit masters and check them, into a new folder.')
    ren.add_argument('project', help='the project file')
    ren.add_argument('--output', required=True, help='a new folder for the render')
    ren.add_argument('--config', help="music-production's configuration: needed by instrument layers (its FluidSynth and "
                     'sample banks) and by sample layers on Ogg, FLAC or MP3 files (its FFmpeg)')
    ren.add_argument('--ffmpeg', help='the FFmpeg executable (decodes recordings that are not WAV)')
    ren.add_argument('--sound', action='append', metavar='ID', help='render only this sound (repeatable), for quick '
                     'changes: it is levelled among the sounds rendered')
    ren.add_argument('--no-page', action='store_true', help='leave out the audition page while iterating (sfx.py audition '
                     'writes it later)')
    ren.add_argument('--samples', action='append', metavar='LIB',
                     help='a sample library; repeat it for packs kept in several (default: $SFX_SKILL_LIBRARY or '
                          '~/.sfx-skill/library, when it exists)')
    ren.add_argument('--record-takes', action='store_true', help='let Surge XT record the takes of Surge layers that have none yet (needs --config); '
                     'without it a render plays only the takes kept in surge_takes beside the project')
    ren.add_argument('--music-skill', help='the music-production folder, if not beside this skill or in ~/.claude/skills')
    ren.set_defaults(func=cmd_render)

    chk = sub.add_parser('check', help='measure a render again', description="Measure a render's master files "
                         'again, write check.json and list the flags.')
    chk.add_argument('render', help='a render folder')
    chk.add_argument('--traits', action='store_true', help="a line per sound with its level and traits")
    chk.set_defaults(func=cmd_check)

    aud = sub.add_parser('audition', help="write a render's audition page again", description='Write '
                         'audition.html in a render folder from its masters and check.json (render writes it '
                         'already; use this after check).')
    aud.add_argument('render', help='a render folder')
    aud.add_argument('--ffmpeg', help='the FFmpeg that makes the page smaller (FLAC); without one the page holds WAV')
    aud.add_argument('--config', help="music-production's configuration file, another place to find FFmpeg")
    aud.set_defaults(func=cmd_audition)

    xp = sub.add_parser('export', help="write a render's files for an engine", description='Write the masters of '
                        'a render as the files an engine takes (48 kHz, 16-bit WAV by default), with manifest.json '
                        '(each file, its measurements after encoding and the engine settings suggested with their '
                        'sources), into a new folder.')
    xp.add_argument('render', help='a render folder')
    xp.add_argument('--target', required=True, choices=export.TARGETS, help='the engine')
    xp.add_argument('--output', required=True, help='a new folder for the export')
    xp.add_argument('--formats', help='comma-separated: wav (default), flac, ogg, mp3 (the last three need FFmpeg)')
    xp.add_argument('--config', help="music-production's configuration file (another place to find FFmpeg)")
    xp.add_argument('--ffmpeg', help='the FFmpeg executable (encodes flac, ogg and mp3); default: $SFX_SKILL_FFMPEG, '
                    'the one music-production names, or ffmpeg on the PATH')
    xp.add_argument('--res-path', default='res://sfx', help='godot: the project folder the files will sit in '
                    '(default res://sfx)')
    xp.set_defaults(func=cmd_export)

    sg = sub.add_parser('spectrogram', help='draw a picture of each sound of a render (or one WAV) to look at',
                        description='Draw a PNG for each sound of a render folder (its first variant) or for one WAV: '
                        'the level envelope with the loudest millisecond marked, and the spectrogram on a log '
                        'frequency axis. You cannot hear; look at these to see clicks, strikes, rings, noise and weight.')
    sg.add_argument('target', help='a render folder, or a WAV file')
    sg.add_argument('--output', help='the folder for the pictures (default: RENDER/spectrograms, or beside the WAV)')
    sg.add_argument('--sound', action='append', help='only this sound id (repeatable)')
    sg.add_argument('--all-variants', action='store_true', help='every variant, not only the first')
    sg.set_defaults(func=cmd_spectrogram)

    ro = sub.add_parser('readout', help='read each sound of a render (or one WAV) as numbers',
                        description='Print, for each sound of a render folder (its first variant) or for one WAV file, what '
                        'the spectrogram shows, as numbers to read: ten octave bands over time from the onset, its tonal '
                        'lines (frequency, note, start, end, level) and its events (time, rise, bands). Read this first; '
                        'look at the spectrogram for what it leaves open.')
    ro.add_argument('target', help='a render folder, or a WAV file')
    ro.add_argument('--sound', action='append', help='only this sound id (repeatable)')
    ro.add_argument('--all-variants', action='store_true', help='every variant, not only the first')
    ro.add_argument('--loop', action='store_true', help='with a WAV file: read it as a loop (twelve equal slices); a WAV whose '
                    'smpl chunk loops the whole file is read as one anyway')
    ro.add_argument('--json', action='store_true', help='print JSON')
    ro.set_defaults(func=cmd_readout)

    cl = sub.add_parser('clean', help='list old renders, or move them to .sfx-trash', description='List the render folders '
                        'inside FOLDER that the kept ones no longer need and, with --trash, move them whole into '
                        'FOLDER/.sfx-trash, from where moving one back restores it. Nothing is ever deleted. A folder stays '
                        'when it holds anything this skill did not write, when it is newer than the kept version and, '
                        'without --exports, when it is an export. With --downloads: list the FSD50K archives in FOLDER '
                        'that the library holds in full, and touch nothing.')
    cl.add_argument('folder', help='the folder that holds the project\'s renders and exports (or, with --downloads, '
                    "FSD50K's downloaded archives)")
    cl.add_argument('--keep', action='append', help='a render folder that stays (repeatable): the render you chose; its '
                    'exports stay with it')
    cl.add_argument('--exports', action='store_true', help='consider exports too (one made from a kept render still stays)')
    cl.add_argument('--downloads', action='store_true', help="list FSD50K's downloaded archives that the library holds in "
                    'full (they are left where they are)')
    cl.add_argument('--library', help='with --downloads: the sample library (default: $SFX_SKILL_LIBRARY or '
                    '~/.sfx-skill/library)')
    cl.add_argument('--trash', action='store_true', help='move what the list names into FOLDER/.sfx-trash (without it nothing '
                    'moves)')
    cl.add_argument('--json', action='store_true', help='print the full plan as JSON')
    cl.set_defaults(func=cmd_clean)

    sm = sub.add_parser('samples', help='list, import and find recordings for sample layers', description='List the '
                        'recordings of the sample library, find them, print a time map of one, fetch and import '
                        "FSD50K's CC0 recordings, or import sounds a person has (--add), their licence read from the "
                        'files that came with them or named by the person.')
    sm.add_argument('--library', help='the sample library folder (default: $SFX_SKILL_LIBRARY or ~/.sfx-skill/library)')
    sm.add_argument('--add', action='append', help="a folder or zip of sounds the person has (repeatable): their own "
                    'recordings, a library they downloaded or bought, the game\'s sounds')
    sm.add_argument('--licence', help='the licence of the sounds to --add, as the person says: own, CC0-1.0, public domain, '
                    'CC-BY-4.0, ... or other. Without it nothing is imported and the licence text that came with them is printed')
    sm.add_argument('--confirm-licence', action='store_true', help='the person has seen what the files of --add say and '
                    'holds to the --licence named for all of them')
    sm.add_argument('--pack', help='the pack id for --add (default: from the folder or zip name)')
    sm.add_argument('--title', help='a title for the pack of --add')
    sm.add_argument('--author', help='who made the sounds of --add (recorded for credits)')
    sm.add_argument('--page', help='where the sounds of --add came from (recorded for credits)')
    sm.add_argument('--ffmpeg', help='the FFmpeg executable (decodes FLAC, Ogg, MP3 and AIFF); default: '
                    '$SFX_SKILL_FFMPEG, the one music-production names (--config), or ffmpeg on the PATH')
    sm.add_argument('--find', help='only recordings whose name, pack, title, tags or labels hold this text')
    sm.add_argument('--passing', action='store_true', help="only recordings that pass their library's screens")
    sm.add_argument('--map', metavar='PACK/FILE', help='print the time map of one recording: level, peak, centroid, '
                    'low share and pitch per frame, and its quietest stretch (for finding events and noise)')
    sm.add_argument('--events', metavar='PACK/FILE', help='list the strikes of one recording (onset, peak, end, level) and '
                    'a take for each, ready to paste into a layer')
    sm.add_argument('--hop', type=float, default=0.1, help='the frame of the time map in seconds (default 0.1)')
    sm.add_argument('--start', type=float, help='the time map starts here (seconds)')
    sm.add_argument('--end', type=float, help='the time map ends here (seconds)')
    sm.add_argument('--limit', type=int, help=f'list at most this many recordings or strikes (a search shows the best {FIND_SHOWN}, '
                    f'--events the first {EVENTS_SHOWN}; 0 for all)')
    sm.add_argument('--labels', action='store_true', help='the classes the library is labelled with, and how many recordings '
                    'carry each (FSD50K: 200 everyday sounds)')
    sm.add_argument('--min-gap', type=float, metavar='S', help='with --events: strikes closer than this count as one (a heel '
                    'and a toe as one step; about 0.3 for a walk)')
    sm.add_argument('--even', action='store_true', help="with --events: give each take the gain that brings it to the level "
                    'of the loudest strike')
    sm.add_argument('--spectrogram', action='append', metavar='PACK/FILE', help='draw a recording, to look at it before '
                    'building on it (repeatable)')
    sm.add_argument('--output', help='with --spectrogram: the folder for the pictures (default: here)')
    sm.add_argument('--not', dest='exclude', action='append', metavar='WORD', help='with --find: leave out recordings '
                    'holding this word (repeatable)')
    sm.add_argument('--min-seconds', type=float, help='with --find: at least this long')
    sm.add_argument('--max-seconds', type=float, help='with --find: at most this long')
    sm.add_argument('--download-fsd50k', metavar='DATA_FOLDER', help="fetch FSD50K's CC0 recordings into this folder and "
                    "import them: this release's screened packs when it has them, else the --set from the dataset's "
                    'Zenodo record (checked, resumable)')
    sm.add_argument('--source', choices=('release', 'zenodo'), help="with --download-fsd50k: this release's packs, or the "
                    "dataset's whole --set from Zenodo")
    sm.add_argument('--download-only', action='store_true', help='with --download-fsd50k: fetch the files and stop; '
                    'import them later with --add-fsd50k')
    sm.add_argument('--add-fsd50k', metavar='DATA_FOLDER', help="import FSD50K's CC0 recordings from a folder of "
                    "downloaded files: a release's packs, or the dataset's files for --set")
    sm.add_argument('--set', choices=sorted(fsd50k.SETS), default='eval', help="the set to fetch or import from the dataset's own files (default eval)")
    sm.add_argument('--no-md5-check', action='store_true', help='skip checking the FSD50K files against their MD5 '
                    'sums (when they were checked already)')
    sm.add_argument('--config', help="music-production's configuration (another place to find FFmpeg)")
    sm.add_argument('--json', action='store_true', help='print the library listing as JSON')
    sm.set_defaults(func=cmd_samples)

    bk = sub.add_parser('banks', help='list the sample banks recorded layers may use', description="List the sample "
                        "banks of music-production's configuration with their presets (bank:program name), marking "
                        'those a recorded layer may not use (a licence that may put conditions on the sound).')
    bk.add_argument('--config', required=True, help="music-production's configuration file")
    bk.add_argument('--bank', action='append', help='only this bank (repeatable)')
    bk.add_argument('--find', help='only presets whose name holds this text (any case)')
    bk.add_argument('--json', action='store_true', help='print the listing as JSON')
    bk.set_defaults(func=cmd_banks)

    su = sub.add_parser('surge', help='find Surge XT patches for surge layers', description="List the Surge XT patches of music-production's catalog that a surge "
                        'layer can play, with their measured character, in an order of this project\'s own.')
    su.add_argument('--find', help="only patches holding these words in their id, name, category or designer")
    su.add_argument('--role', help='only this role: lead, brass, pad, chords, keys, pluck, bass, sequence, fx, percussion, special or other')
    su.add_argument('--character', help='comma-separated words their measured character must hold: dark, warm, mid, bright; fast, soft, slow; dry, short, long; '
                    'mono, narrow, wide; steady, moving')
    su.add_argument('--id', help='one patch in full: its measurement and what its macros and mod wheel move')
    su.add_argument('--limit', type=int, default=25, help='list at most this many (default 25; 0 for all)')
    su.add_argument('--music-skill', help='the music-production folder, if not beside this skill or in ~/.claude/skills')
    su.add_argument('--json', action='store_true', help='print them as JSON')
    su.set_defaults(func=cmd_surge)

    mu = sub.add_parser('music', help="read a music-production project or a MIDI file, or quote a part's written notes",
                        description="What a music-production project (song.json) declares: tempo, meter, sections, harmony, plan and parts, counted from "
                        "bar 1; or, for a Standard MIDI File (.mid), its tempo, meter and parts. With --track and --bars, those bars' written notes, "
                        'ready for an instrument or surge layer. It works out no key and changes no music.')
    mu.add_argument('song', help="the project's song.json, or a .mid file")
    mu.add_argument('--track', help='the id of the part to quote (for a MIDI file, the part id its listing gives, such as 2 or 1.10)')
    mu.add_argument('--bars', help='the bars to quote, counted from 1: 5 or 5-6')
    mu.add_argument('--json', action='store_true', help='print it as JSON')
    mu.set_defaults(func=cmd_music)

    doc = sub.add_parser('doctor', help='check the setup', description='Check what synthesis needs (Python, NumPy); '
                         'with --config also FFmpeg and music-production\'s own doctor.')
    doc.add_argument('--config', help="music-production's configuration file (its own doctor; another place to find "
                     'FFmpeg)')
    doc.add_argument('--ffmpeg', help='the FFmpeg executable to check')
    doc.add_argument('--music-skill', help='the music-production folder, if not beside this skill or in '
                     '~/.claude/skills')
    doc.add_argument('--tmp', help='a folder for TMP and TEMP of child processes')
    doc.add_argument('--library', help='the sample library to report on, if not the default one (~/.sfx-skill/library or $SFX_SKILL_LIBRARY)')
    doc.set_defaults(func=cmd_doctor)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        code = args.func(args)
        sys.stdout.flush()
        return code
    except OSError as error:           # a folder that cannot be made, a full disk, a path too long for this system
        if error.filename is None and error.errno in (errno.EPIPE, errno.EINVAL):    # whatever read the output has closed it
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())           # (`| head`): the rest is dropped
            return 0
        print(f'{args.command}: {error}')
        return 1
    except record.RecordError as error:
        print(f'{args.command}: {error}')
        return 1


if __name__ == '__main__':
    sys.exit(main())
