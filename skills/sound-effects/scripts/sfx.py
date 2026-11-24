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
  music      read a music-production project: tempo, sections, declared harmony, parts; quote a part's written notes
  samples    fill and search the sample library: FSD50K's CC0 recordings, sounds a person has, a recording's time map
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

from sfxlib import (VERSION, audition, check, clean, denoise, export, ffmpeg, fsd50k, instruments,  # noqa: E402
                    licences, mpbridge, music, project, recipes, record, render, samples, spectrogram, surge, variants, wav)

LIMITED_SHOWN = 5                   # how many limited files a render or a check names
LIMITED_FROM_DB = 0.05              # less than this is not reported as limiting
FIND_SHOWN = 25                     # how many recordings a search lists unless told otherwise
EVENTS_SHOWN = 24                   # how many strikes a recording's map names
EVENT_LEAD_S = 0.002                # a take starts this long before its event's onset


def _load_project(path):
    try:
        return project.load(path)
    except project.ProjectError as error:
        print(f'{path}: {len(error.errors)} problem(s)')
        for line in error.errors:
            print(f'  {line}')
        return None


def cmd_validate(args):
    proj = _load_project(args.project)
    if proj is None:
        return 1
    files = sum(s['variants'] for s in proj['sounds'])
    n = len(proj['sounds'])
    print(f"OK: {proj['title']}: {n} sound{'s' if n != 1 else ''}, {files} files to render "
          f"(seed {proj['seed']}, level {proj['level']['mode']} under {proj['level']['ceiling_dbtp']} dBTP)")
    return 0


def _print_flags(report):
    flags = report['flags']
    print(f'{len(flags)} flags' + (':' if flags else ''))
    for f in flags:
        print(f"  [{f['kind']}] {Path(f['file']).stem}: {f['message']}")
    for note in report.get('notes', ()):
        print(f"note: {note['sound']} {note['message']}")


def _print_level(folder):
    """The render's loudness target, the file that set it and the files the limiter took the most from."""
    resolved = json.loads((Path(folder) / 'resolved.json').read_text(encoding='utf-8'))
    level = resolved['level']
    if level.get('target_lufs') is not None:
        has_loops = any(s.get('use') == 'loop' for s in resolved['sounds'])
        measure = 'of the loudest 200 ms (one-shots), integrated (loops)' if has_loops else 'of the loudest 200 ms'
        if level.get('limited_by'):
            print(f"level: target {level['target_lufs']:.1f} LUFS {measure}, set by {level['limited_by']}"
                  + (' (the level.target_lufs asked for is out of its reach)' if level.get('held') else ''))
        else:
            print(f"level: held at {level['target_lufs']:.1f} LUFS {measure} by level.target_lufs")
        holds = level.get('next')
        if holds:
            then = f"{holds['then']} then sets the level" if holds['then'] else 'the set then reaches level.target_lufs'
            print(f"  optional: lowering {holds['sound']}'s trim_db by {holds['rise_db']:.1f} brings everything else up "
                  f"{holds['rise_db']:.1f} dB against it ({then}). Leave it if {holds['sound']} should stand out; "
                  'level.target_lufs holds the set where you put it')
    rows = sorted(((v.get('limited_db') or 0.0, Path(v['file']).stem) for s in resolved['sounds'] for v in s['variants']),
                  key=lambda r: -r[0])
    rows = [r for r in rows if r[0] >= LIMITED_FROM_DB]
    if rows:
        more = f' and {len(rows) - LIMITED_SHOWN} more' if len(rows) > LIMITED_SHOWN else ''
        print('limited: ' + ', '.join(f'{name} {db:.1f} dB' for db, name in rows[:LIMITED_SHOWN]) + more)
        print("  (normal: the set's level rises until its peakiest file reaches the limiter's allowance, level.limit_db, "
              '6 dB by default; a lower allowance keeps more of the peaks and leaves the set a little quieter)')


def _ffmpeg(args, label):
    """(ok, path or None): the FFmpeg ffmpeg.find settles on for this command's --ffmpeg and --config."""
    config = None
    if getattr(args, 'config', None):
        try:
            config = mpbridge.load_config(args.config)
        except mpbridge.MusicSkillError as error:
            print(f'{label}: {error}')
            return False, None
    try:
        return True, ffmpeg.find(getattr(args, 'ffmpeg', None), config)
    except ffmpeg.FFmpegError as error:
        print(f'{label}: {error}')
        return False, None


def _library(args, must_exist=True):
    """The library folder: --library, else the default one (samples.default_library)."""
    if getattr(args, 'library', None):
        return Path(args.library)
    lib = samples.default_library()
    if must_exist and not (lib / samples.INDEX).is_file():
        return None
    return lib


def cmd_render(args):
    proj = _load_project(args.project)
    if proj is None:
        return 1
    config = None
    if args.config:
        try:
            config = mpbridge.load_config(args.config)
        except mpbridge.MusicSkillError as error:
            print(f'render: {error}')
            return 1
    ok, ff = _ffmpeg(args, 'render')
    if not ok:
        return 1
    lib = args.samples
    if not lib and (samples.default_library() / samples.INDEX).is_file():
        lib = samples.default_library()
    if args.sound:
        unknown = sorted(set(args.sound) - {s['id'] for s in proj['sounds']})
        if unknown:
            print(f"render: no sound {', '.join(unknown)} in the project (it has: {', '.join(s['id'] for s in proj['sounds'])})")
            return 1
        proj = dict(proj, sounds=[s for s in proj['sounds'] if s['id'] in args.sound],
                    flows=[f for f in proj['flows'] if set(f['sounds']) <= set(args.sound)])
    try:
        report = render.render(proj, args.output, config=config, samples=lib, ffmpeg=ff, page=not args.no_page,
                               takes=Path(args.project).absolute().parent / surge.FOLDER, record_takes=args.record_takes,
                               music_skill=args.music_skill)
    except render.RenderError as error:
        print(f'render: {error}')
        return 1
    print(f"{report['title']}: {report['files']} files in {args.output} ({report['seconds']} s)")
    if 'surge_takes' in report:
        takes = report['surge_takes']
        print(f"Surge takes: {takes['recorded']} recorded, {takes['played'] - takes['recorded']} replayed; {surge.FOLDER} beside the project holds "
              f"{takes['folder_bytes'] / 1e6:.1f} MB and belongs with it")
    _print_level(args.output)
    _print_flags(json.loads((Path(args.output) / 'check.json').read_text(encoding='utf-8')))
    if args.sound and proj['level'].get('target_lufs') is None:
        print('These sounds were levelled among themselves: render the whole project for the set\'s level.')
    elif args.sound:
        print('The level is held by level.target_lufs, though a full render can still lower it if a sound left out cannot reach it.')
    if not args.no_page:
        print(f"audition page: {Path(args.output) / 'audition.html'}")
    return 0


def cmd_audition(args):
    folder = Path(args.render)
    if not (folder / 'check.json').is_file():
        print(f'audition: {folder} is not a checked render folder (no check.json)')
        return 1
    try:                                 # FFmpeg only makes the page smaller, so a missing or stale one is no failure
        ff = ffmpeg.find(args.ffmpeg, mpbridge.load_config(args.config) if args.config else None)
    except (ffmpeg.FFmpegError, mpbridge.MusicSkillError) as error:
        print(f'audition: {error}; the page holds WAV')
        ff = None
    path = audition.build(folder, ffmpeg=ff)
    render.refresh_listing(folder, ['audition.html'])
    print(f'audition page: {path}')
    return 0


def _print_traits(report):
    """One line per sound: the medians of its variants (references/checks.md says what each is)."""
    def num(value, digits=1):
        return '-' if value is None else f'{value:.{digits}f}'
    width = max([len(s['id']) for s in report['sounds']] + [5])
    print(f"{'sound':<{width}}  {'LUFS':>6} {'moment':>6} {'plr':>5} {'centroid':>8} {'flat':>5} {'active':>6} {'attack':>6} {'decay':>6} "
          f"{'small':>6}  bands %")
    for s in report['sounds']:
        rows = [f for f in report['files'] if f['sound'] == s['id']]
        keys = [f.get('brief_lufs') if f.get('brief_lufs') is not None else f.get('integrated_lufs') for f in rows]
        loud = sorted(v for v in keys if v is not None)
        t = s['traits']
        moments = sorted(f['max_momentary_lufs'] for f in rows if f.get('max_momentary_lufs') is not None)
        print(f"{s['id']:<{width}}  {num(loud[len(loud) // 2] if loud else None):>6} "
              f"{num(moments[len(moments) // 2] if moments else None):>6} {num(t.get('plr_db')):>5} "
              f"{num(t.get('centroid_hz'), 0):>8} {num(t.get('flatness'), 2):>5} {num(t.get('active_s'), 2):>6} "
              f"{num(t.get('attack_ms'), 0):>6} {num(t.get('decay20_ms'), 0):>6} {num(t.get('small_speaker_db')):>6}  "
              + '/'.join(str(round(100 * (t.get('bands') or {}).get(k, 0))) for k in ('sub', 'low', 'low_mid', 'mid', 'high_mid', 'high')))
    print('  LUFS: what the level step matched (loudest 200 ms; integrated for a loop). moment: the loudest 400 ms, to compare a '
          "loop's loud moments with the one-shots. plr dB, centroid Hz, active s, attack and decay ms; small: dB left at 400 Hz "
          'and above, roughly what a phone plays; bands: % of the energy in sub, low, low-mid, mid, high-mid and high.')


def cmd_check(args):
    folder = Path(args.render)
    if not (folder / 'resolved.json').is_file():
        print(f'check: {folder} is not a render folder (no resolved.json)')
        return 1
    report = check.run(folder)
    render.refresh_listing(folder, ['check.json'])
    print(f"{len(report['files'])} files measured")
    _print_level(folder)
    if args.traits:
        _print_traits(report)
    _print_flags(report)
    return 0


def cmd_recipes(args):
    items = recipes.all_recipes()
    if args.category:
        items = [r for r in items if r.category == args.category]
    if args.name:
        try:
            items = [recipes.get(args.name)]
        except KeyError as error:
            print(error.args[0])
            return 1
    if args.layers:
        if not args.name:
            print('recipes: --layers shows one recipe: add --name')
            return 1
        r = items[0]
        built = r.build(dict(r.defaults(), seconds=project.DEFAULT_LOOP_S) if r.use == 'loop' else r.defaults())
        print(json.dumps({'layers': built['layers'], 'chain': built.get('chain', {})}, indent=1))
        return 0
    if args.json:
        print(json.dumps([{'name': r.name, 'category': r.category, 'use': r.use, 'summary': r.summary, 'trim_db': r.trim_db,
                           'params': [p.__dict__ for p in r.params], 'rules': [list(x) for x in r.rules]}
                          for r in items], indent=1))
        return 0
    for r in items:
        kind = ', loop' if r.use == 'loop' else ''
        print(f'{r.name} ({r.category}{kind}, trim {r.trim_db:+.0f} dB): {r.summary}')
        for p in r.params:
            vary = f', varies {p.amount:g} {p.vary}' if p.vary and p.amount else ''
            print(f'    {p.name} = {p.default:g} {p.unit} ({p.lo:g} to {p.hi:g}{vary}){": " + p.help if p.help else ""}')
        if args.name:
            for rule, source in r.rules:
                print(f'    rule: {rule} [{source}]')
    if not items:
        print('no recipes')
    return 0


def cmd_expand(args):
    raw_path = Path(args.project)
    proj = _load_project(raw_path)
    if proj is None:
        return 1
    out = Path(args.output)
    if out.exists():
        print(f'expand: {out} exists; expand writes a new file')
        return 1
    raw = json.loads(raw_path.read_text(encoding='utf-8'))
    done = []
    for i, sound in enumerate(proj['sounds']):
        if 'recipe' not in sound or (args.sound and sound['id'] not in args.sound):
            continue
        spec, _, _ = variants.spec_for(sound, proj['seed'], 0)
        recipe = recipes.get(sound['recipe'])
        cents = max([sound['vary'].get(p.name, 0.0) for p in recipe.params if p.vary == 'cents'] or [0.0])
        pct = max([sound['vary'].get(p.name, 0.0) for p in recipe.params if p.vary == 'pct'] or [0.0])
        new = {k: v for k, v in raw['sounds'][i].items() if k not in ('recipe', 'params', 'vary')}
        if sound.get('crackle'):
            new['crackle'] = True                  # what the recipe says of its sound, which the check goes by
        if sound.get('loop'):
            new['use'] = 'loop'
            new['loop'] = dict(sound['loop'])
            pct = 0.0                              # a loop keeps its length
        new['layers'] = spec['layers']
        if spec.get('chain'):
            new['chain'] = spec['chain']
        new['vary'] = {k: v for k, v in (('pitch_cents', cents), ('time_pct', pct)) if v}
        new['trim_db'] = sound['trim_db']
        new['notes'] = (sound.get('notes', '') + f' (expanded from {recipe.name})').strip()
        raw['sounds'][i] = new
        done.append(sound['id'])
    out.write_text(json.dumps(raw, indent=1, ensure_ascii=False) + '\n', encoding='utf-8', newline='\n')
    print(f"{out}: {len(done)} sound(s) expanded ({', '.join(done) or 'none'}); variation now by pitch_cents and "
          'time_pct only (timbre and level draws of the recipe have no layer equivalent)')
    return 0


def _samples_map(args):
    """The time map of one recording (denoise.time_map), with its quietest stretch and that stretch's octave bands."""
    ok, ff = _ffmpeg(args, 'samples')
    if not ok:
        return 1
    try:
        pack, _, name = args.map.partition('/')
        library = samples.Library(_library(args, must_exist=False), ff)
        _, entry = library.entry(pack, name)
        x, rate = samples.decode(library.path(pack, entry), ff)
    except (samples.SampleError, OSError) as error:
        print(f'samples: {error}')
        return 1
    rows = denoise.time_map(x, rate, args.hop, args.start or 0.0, args.end)
    events = denoise.events(x, rate)
    q0, q1, q_db = denoise.quietest(x, rate)
    out = {'recording': args.map, 'seconds': round(len(x) / rate, 3), 'rate': rate,
           'quietest': {'start': q0, 'end': q1, 'rms_db': q_db, 'bands': denoise.bands(x, rate, q0, q1)},
           'events': events, 'frames': rows}
    if args.json:
        print(json.dumps(out, indent=1))
        return 0
    print(f"{args.map}: {out['seconds']} s at {rate} Hz. Quietest 0.3 s: {q0}-{q1} s at {q_db} dBFS RMS; its octave "
          'bands (dBFS RMS): ' + ', '.join(f'{k} Hz {v}' for k, v in out['quietest']['bands'].items()))
    more = f', and {len(events) - EVENTS_SHOWN} more' if len(events) > EVENTS_SHOWN else ''
    print('Events: ' + (', '.join(f"{e['onset']:.2f} s ({e['db']:+.0f} dB)" for e in events[:EVENTS_SHOWN]) + more if events
                        else 'no clear strikes') + ' (level against the loudest moment; --events for their onsets and ends)')
    digits = 3 if args.hop < 0.01 else 2
    print('      t   rms dB  peak dB  centroid Hz  <150 Hz  pitch Hz (20-2000 Hz; - for none)')
    for r in rows:
        print(f"{r['t']:7.{digits}f} {r['rms_db']:8.1f} {r['peak_db']:8.1f} {r['centroid_hz']:12d} {r['low_share']:8.2f} "
              f"{(format(r['pitch_hz'], '.0f') if r['pitch_hz'] else '-'):>9}  {'#' * max(0, int((r['rms_db'] + 60) / 2))}")
    print('The noise stretch of a sample layer ("denoise": {"noise": [start, end]}) must hold only noise.')
    return 0


def _samples_spectrograms(args):
    """Draw recordings of the library (spectrogram.py) into --output, to look at before building on them."""
    ok, ff = _ffmpeg(args, 'samples')
    if not ok:
        return 1
    out = Path(args.output or '.')
    try:
        library = samples.Library(_library(args, must_exist=False), ff)
        for name in args.spectrogram:
            pack, _, file = name.partition('/')
            _, entry = library.entry(pack, file)
            x, rate = samples.decode(library.path(pack, entry), ff)
            out.mkdir(parents=True, exist_ok=True)
            png = out / f'{pack}_{file}.spectrogram.png'
            scale = spectrogram.write(x, png, rate)
            print(f"{png}: {scale['seconds']} s, {scale['column_ms']} ms a column, loudest at {scale['peak_s']} s")
    except (samples.SampleError, OSError) as error:
        print(f'samples: {error}')
        return 1
    print('Top: the level (the loudest millisecond marked white). Below: frequency on a log scale, 20 Hz at the bottom to '
          '20 kHz at the top, faint lines at 100 Hz, 1 kHz and 10 kHz; time to the right, a faint line every 0.1 s.')
    return 0


def _samples_events(args):
    """The strikes of one recording (denoise.events) and a take for each, ready to paste into a sample layer."""
    ok, ff = _ffmpeg(args, 'samples')
    if not ok:
        return 1
    try:
        pack, _, name = args.events.partition('/')
        library = samples.Library(_library(args, must_exist=False), ff)
        _, entry = library.entry(pack, name)
        x, rate = samples.decode(library.path(pack, entry), ff)
    except (samples.SampleError, OSError) as error:
        print(f'samples: {error}')
        return 1
    found = denoise.events(x, rate)
    if args.min_gap:
        found = denoise.spaced(found, args.min_gap)
    takes = [{'file': name, 'offset': round(max(0.0, e['onset'] - EVENT_LEAD_S), 3)} for e in found]
    if args.even:                       # each take brought to the level of the loudest strike
        loudest = max((e['db'] for e in found), default=0.0)
        for t, e in zip(takes, found):
            if round(loudest - e['db'], 1):
                t['gain_db'] = round(loudest - e['db'], 1)
    if args.json:
        print(json.dumps({'recording': args.events, 'seconds': round(len(x) / rate, 3), 'events': found,
                          'takes': takes[:project.MAX_TAKES]}, indent=1))
        return 0
    print(f"{args.events}: {len(x) / rate:.3f} s, {len(found)} event{'s' if len(found) != 1 else ''} "
          '(seconds; level against the loudest moment)')
    if not found:
        print('No clear strikes: a steady sound, or one that swells. --map shows its level over time.')
        return 0
    shown = (args.limit if args.limit is not None else EVENTS_SHOWN) or len(found)
    print('  onset    peak     end    level')
    for e in found[:shown]:
        print(f"{e['onset']:7.3f} {e['peak']:7.3f} {e['end']:7.3f} {e['db']:+6.1f} dB")
    if len(found) > shown:
        print(f'... the first {shown} of {len(found)} (--limit 0 lists all)')
    print(f'Takes starting just before each onset (a sample layer\'s "file", library {pack}):')
    paste = takes[:min(shown, project.MAX_TAKES)]
    print(json.dumps(paste))
    if len(paste) < len(takes):
        print(f'(the first {len(paste)} of {len(takes)}: a layer holds at most {project.MAX_TAKES} takes)')
    return 0


def cmd_samples(args):
    lib = _library(args, must_exist=False)
    if args.download_fsd50k:
        from_release = args.source == 'release' or (args.source is None and bool(fsd50k.RELEASE_URL))
        try:
            if from_release:
                got = fsd50k.download_packs(args.download_fsd50k)
                print(f"FSD50K packs: {len(got['parts'])} parts in {args.download_fsd50k}, checked against their "
                      'SHA-256 sums')
            else:
                fsd50k.download(args.download_fsd50k, args.set)
                print(f'FSD50K {args.set} set: all files in {args.download_fsd50k}, checked against their MD5 sums')
        except (fsd50k.FSD50KError, OSError) as error:
            print(f'samples: {error}')
            return 1
        if args.download_only:
            return 0
        args.add_fsd50k = args.download_fsd50k
    if args.add_fsd50k and args.source != 'zenodo' and (Path(args.add_fsd50k) / fsd50k.PACK_INDEX).is_file():
        try:
            r = fsd50k.add_packs(args.add_fsd50k, lib)
        except (fsd50k.FSD50KError, samples.SampleError, OSError) as error:
            print(f'samples: {error}')
            return 1
        print(f"FSD50K packs ({', '.join(r['packs'])}): {r['files']} recordings, {r['added']} of them new, in "
              f"{r['folder']}")
        return 0
    if args.add_fsd50k:
        try:
            r = fsd50k.add(args.add_fsd50k, lib, args.set, check_md5=not args.no_md5_check)
        except (fsd50k.FSD50KError, samples.SampleError, OSError) as error:
            print(f'samples: {error}')
            return 1
        print(f"{r['pack']}: {r['status']}, {r['files']} CC0 recordings ({r['passing']} pass every screen) in "
              f"{r['folder']}")
        return 0
    if args.add:
        ok, ff = _ffmpeg(args, 'samples')
        if not ok:
            return 1
        status = 0
        for source in args.add:
            try:
                r = samples.add(source, lib, licence=args.licence, pack=args.pack, title=args.title,
                                author=args.author, page=args.page, ffmpeg=ff,
                                confirm_licence=args.confirm_licence)
            except (samples.SampleError, OSError) as error:
                print(f'{Path(source).name}: not imported: {error}')
                status = 1
                continue
            asks = ', '.join(r['conditions']) or 'nothing (no credit needed)'
            print(f"{r['pack']}: {r['added']} recordings added ({r['files']} in the pack, {r['passing']} pass every "
                  f"screen), licence {r['licence']} ({r['licence_status']}); it asks: {asks}")
            if r['duplicates']:
                print(f"  {r['duplicates']} duplicates of recordings already in the library were left out")
            for rel, why in r['skipped']:
                print(f'  left out {rel}: {why}')
            if r['reasons']:
                print('  screens: ' + ', '.join(f'{k} {v}' for k, v in sorted(r['reasons'].items())))
        print(f'library: {lib}')
        return status
    if not args.map and not args.events and not args.spectrogram and not (lib / samples.INDEX).is_file():
        print('A sample library holds the recordings sample layers play. Fill it in either way:')
        print(f'  FSD50K ({fsd50k.PAGE}): {fsd50k.RELEASE_RECORDINGS:,} screened CC0 recordings in 190 labelled classes, from '
              "this project's release. Fetch and import (ask the person first: a 2.9 GB download, 4.3 GB once imported, about 7.2 GB until the "
              "archives are removed): sfx.py samples "
              f'--download-fsd50k DATA_FOLDER. With --source zenodo, a whole set of the dataset instead: --set eval '
              f'({fsd50k.CC0_COUNTS["eval"]:,} CC0 recordings, 6.3 GB) or --set dev ({fsd50k.CC0_COUNTS["dev"]:,}, 18.4 GB).')
        print('  Sounds the person has (their own recordings, a library they downloaded or bought, the game\'s sounds): '
              'sfx.py samples --add FOLDER_OR_ZIP [--licence own|CC0-1.0|...] [--author NAME] [--page URL]. Nothing '
              'is imported until the person names the licence: without --licence it prints the licence text that came '
              'with them, to show the person.')
        print(f'  The library is {lib} (--library FOLDER, or ${samples.DEFAULT_LIBRARY_ENV}, to use another).')
        return 0
    if args.map:
        return _samples_map(args)
    if args.events:
        return _samples_events(args)
    if args.spectrogram:
        return _samples_spectrograms(args)
    try:
        index = samples.offered(samples.load_index(lib))
    except samples.SampleError as error:
        print(f'samples: {error}')
        return 1
    if args.labels:
        held = samples.labels(index, passing=args.passing)
        if args.json:
            print(json.dumps(held, indent=1))
        else:
            print(f'{len(held)} classes (how many recordings carry each); search them with --find:')
            print(textwrap.fill(', '.join(f'{name} {count}' for name, count in held.items()), 118))
        return 0
    if args.find is not None:
        for text in (args.find, *(args.exclude or ())):
            if not samples.searchable(text):
                print(f"samples: '{text}' holds no letters A-Z or digits to search by. Names, titles, tags and labels are "
                      'matched by those, so search in English, for example --find "wood door"')
                return 1
        files = samples.find(index, args.find, passing=args.passing, exclude=args.exclude or (),
                             min_seconds=args.min_seconds, max_seconds=args.max_seconds, project=Path.cwd().name)
    else:
        files = [f for f in index['files'] if not (args.passing and f.get('screen'))]
    total = len(files)
    some = sum(1 for f in files if 'matched_words' in f)        # listed after those holding every word
    summary = not args.find and not args.json and args.limit is None
    limit = args.limit if args.limit is not None else (FIND_SHOWN if args.find and not args.json else 0)
    files = [] if summary else files[:limit] if limit else files
    if args.json:                        # files and matched hold every word, as before; those holding some of them come apart
        print(json.dumps({'packs': index['packs'], 'files': [f for f in files if 'matched_words' not in f], 'matched': total - some,
                          'some_words': [f for f in files if 'matched_words' in f]}, indent=1, ensure_ascii=False))
        return 0
    shown = {f['library'] for f in files} if args.find else set(index['packs'])
    for pack_id, pack in sorted(index['packs'].items()):        # a search names only the packs whose licence asks for something
        if pack_id in shown and (not args.find or licences.conditions(pack['licence'])):
            asks = ', '.join(licences.conditions(pack['licence'])) or 'no credit needed'
            print(f"{pack_id}: {pack['title']}" + (f" by {pack['author']}" if pack.get('author') else '') +
                  f" ({pack['licence']}: {asks}), {pack['files']} files")
    for f in files:
        layout = 'mono' if f['channels'] == 1 else 'stereo'
        line = f"  {f['library']}/{f['file']}  {f['seconds']:.2f} s {layout}"
        if f.get('title'):
            m = f.get('measures', {})
            line += (f"  '{f['title'][:60]}' [{', '.join(f.get('labels', [])[:4])}] peak {m.get('peak_db')} dB, "
                     f"range {m.get('range_db')} dB, {'; '.join(f['screen']) if f.get('screen') else 'passes'}")
            via = samples.matched_tags(f, args.find) if args.find else []
            if via:
                line += f" (by its tag{'s' if len(via) > 1 else ''} {', '.join(via[:3])})"
        if 'matched_words' in f:
            line += f" (holds only: {', '.join(f['matched_words'])})"
        print(line)
        if f.get('author_note'):
            print(f"      author's note (read and respect it before use): {f['author_note']}")
    if summary:
        print(f"{total} recording{'s' if total != 1 else ''} in all. Search them with --find WORDS, see the classes they are "
              'labelled with by --labels, or list them with --limit N (0 for all)')
    if args.find:
        every = total - some
        print((f"{every} recording{'s' if every != 1 else ''} match '{args.find}', and {some} more hold some of its words (marked)" if some else
               f"{total} recording{'s' if total != 1 else ''} match '{args.find}'")
              + (f' (the best {len(files)} shown; --limit 0 lists all)' if len(files) < total else ''))
    print('A layer plays one: {"type": "sample", "library": PACK, "file": NAME} (or "file": [NAME, ...], a take per '
          'variant)')
    return 0


def cmd_banks(args):
    try:
        config = mpbridge.load_config(args.config)
    except mpbridge.MusicSkillError as error:
        print(error)
        return 1
    rows = instruments.list_banks(config)
    if args.bank:
        rows = [r for r in rows if r['name'] in args.bank]
    if args.find:
        needle = args.find.lower()
        rows = [dict(r, presets=[p for p in r['presets'] if needle in p[2].lower()]) if r['usable'] else r
                for r in rows]
    if args.json:
        print(json.dumps(rows, indent=1))
        return 0
    for r in rows:
        if not r['usable']:
            why = r['problem'] or f"licensed '{r['licence']}', which may put conditions on the sound"
            print(f"{r['name']}: not usable ({why})")
            continue
        if args.find and not r['presets']:
            continue
        print(f"{r['name']} ({r['licence']}): {len(r['presets'])} preset{'s' if len(r['presets']) != 1 else ''}")
        for bank, program, name in r['presets']:
            print(f'  {bank}:{program} {name}')
    print('A layer plays one: {"type": "instrument", "soundfont": NAME, "bank": B, "program": P, "notes": [...]}')
    return 0


def cmd_surge(args):
    try:
        skill = mpbridge.find_music_skill(args.music_skill or None)
    except mpbridge.MusicSkillError as error:
        print(f'surge: {error}')
        return 1
    if skill is None:
        print("surge: Surge XT comes with the music-production skill, which was not found (its setup installs it; --music-skill names its folder)")
        return 1
    try:
        presets = surge.load_catalog(skill)
    except surge.SurgeError as error:
        print(f'surge: {error}')
        return 1
    if args.id:
        entry = presets.get(args.id)
        if entry is None:
            print(f"surge: there is no patch '{args.id}' in Surge's catalog (sfx.py surge --find WORDS lists them)")
            return 1
        why = surge.refused(entry)
        if why:
            print(f"surge: patch '{args.id}' cannot be used: {why}")
            return 1
        print(json.dumps(dict(entry, id=args.id), indent=1, ensure_ascii=False) if args.json else '\n'.join(surge.describe(args.id, entry)))
        return 0
    if args.find is not None and not samples.searchable(args.find):
        print(f"surge: '{args.find}' holds no letters A-Z or digits to search by. Patch names, categories and designers are in English, for example --find bell")
        return 1
    found = surge.find(presets, args.find or '', args.role, args.character, project=Path.cwd().name)
    shown = found if args.limit == 0 else found[:args.limit]
    if args.json:
        print(json.dumps([dict(presets[key], id=key) for key in shown], indent=1, ensure_ascii=False))
        return 0
    print(f'{len(found)} patches match, {len(shown)} shown' + (' (--limit 0 lists all)' if len(shown) < len(found) else '') + ", in this project's own order:")
    for key in shown:
        print(f'  {surge.summary(key, presets[key])}')
    print('--id ID shows what its macros and mod wheel move. A layer plays one: {"type": "surge", "patch": ID, "notes": [[0, 72, 0.5, 100]]}; '
          'render with --record-takes --config CONFIG to record it')
    return 0


def cmd_music(args):
    try:
        song = music.load(args.song)
        if args.track is None and args.bars is None:
            info = music.describe(song)
            print(json.dumps(info, indent=1, ensure_ascii=False) if args.json else '\n'.join(music.overview(info)))
            return 0
        if args.track is None or args.bars is None:
            raise music.MusicError('--track and --bars go together, such as --track lead --bars 5-6')
        quoted = music.quote(song, args.track, *music.parse_bars(args.bars))
    except music.MusicError as error:
        print(f'music: {error}')
        return 1
    print(json.dumps(quoted, indent=1, ensure_ascii=False) if args.json else '\n'.join(music.quote_lines(quoted)))
    return 0


def _library_line(named=None):
    folder = Path(named) if named else samples.default_library()
    if not (folder / samples.INDEX).is_file():
        return f'sample library: none yet at {folder} (needed only for recorded layers; sfx.py samples tells how to fill it)'
    try:
        index = samples.offered(samples.load_index(folder))
    except samples.SampleError as error:
        return f'sample library: {error}'
    packs, files = len(index['packs']), len(index['files'])
    clean = sum(1 for f in index['files'] if not f.get('screen'))
    return f"sample library: {folder}: {packs} pack{'s' if packs != 1 else ''}, {files} recordings, {clean} pass every screen"


def _ffmpeg_line(ff, missing):
    """What doctor says of this FFmpeg. It is optional, so one that lacks an encoder costs that format and fails nothing."""
    if not missing:
        return f'FFmpeg: OK ({ff})'
    lost = [name for encoder, name in (('libvorbis', 'Ogg'), ('libmp3lame', 'MP3'), ('flac', 'FLAC')) if encoder in missing]
    costs = f", so no {' or '.join(lost)} exports" if lost else ''
    hint = " (on macOS, Homebrew's ffmpeg-full has libvorbis)" if 'libvorbis' in missing else ''
    return f"FFmpeg: {ff}, lacks {', '.join(missing)}{costs}; the rest works{hint}"


def cmd_doctor(args):
    ok = True                           # this script stops before here without Python 3.10 and NumPy
    print(f'Python {platform.python_version()}: OK')
    print(f'NumPy {numpy.__version__}: OK')
    print('synthesis: OK')

    try:
        skill = mpbridge.find_music_skill(args.music_skill or None)
    except mpbridge.MusicSkillError as error:
        print(f'music-production: {error}')
        return 1
    print(f'music-production: {skill}' if skill else
          'music-production: not found (needed only for instrument and Surge layers)')
    try:
        config = mpbridge.load_config(args.config) if args.config else None
    except mpbridge.MusicSkillError as error:
        print(f'configuration: {error}')
        return 1
    print(f'Surge XT: {surge.status(config, skill)}')

    print(_library_line(args.library))
    found, ff = _ffmpeg(args, 'FFmpeg')
    if not found:
        return 1
    if ff is None:
        print('FFmpeg: not found (needed only for FLAC, Ogg and MP3 exports and recordings that are not WAV; pass '
              '--ffmpeg PATH or put ffmpeg on the PATH)')
    else:
        print(_ffmpeg_line(ff, mpbridge.missing_features(mpbridge.ffmpeg_features(ff, env=mpbridge.child_env(args.tmp)))))
    if args.config:
        if skill:
            proc = mpbridge.run_studio(skill, ['doctor', '--config', args.config], env=mpbridge.child_env(args.tmp))
            if proc.returncode == 0:
                print('music-production doctor: OK')
            else:
                print('music-production doctor: FAILED')
                print((proc.stdout + proc.stderr).strip()[-600:])
                ok = False
    return 0 if ok else 1


def cmd_export(args):
    formats = [f.strip() for f in args.formats.split(',') if f.strip()] if args.formats else None
    ok, ff = _ffmpeg(args, 'export')
    if not ok:
        return 1
    try:
        manifest = export.export(args.render, args.output, args.target, formats=formats, ffmpeg=ff,
                                 res_path=args.res_path)
    except export.ExportError as error:
        print(f'export: {error}')
        return 1
    print(f"{manifest['title']}: {manifest['files']} files for {manifest['target']} in {args.output} "
          f"({', '.join(manifest['formats'])})")
    flags = manifest['flags']
    print(f'{len(flags)} flag(s)' + (':' if flags else ''))
    for f in flags:
        where = Path(f['file']).name if f.get('file') else (f.get('sound') or 'export')
        print(f"  [{f['kind']}] {where}: {f['message']}")
    print(f"manifest: {Path(args.output) / 'manifest.json'}")
    return 0


def cmd_spectrogram(args):
    target = Path(args.target)
    jobs = []
    if target.is_dir():
        resolved_path = target / 'resolved.json'
        if not resolved_path.is_file():
            print(f'spectrogram: {target} is not a render folder (no resolved.json)')
            return 1
        resolved = record.load(target)
        out = Path(args.output) if args.output else target / 'spectrograms'
        ending = '.spectrogram.png' if args.output else '.png'     # only in its own folder does a picture take the plain name
        for snd in resolved['sounds']:
            if args.sound and snd['id'] not in args.sound:
                continue
            chosen = snd['variants'] if args.all_variants else snd['variants'][:1]
            for v in chosen:
                jobs.append((v['file'], out / (Path(v['file']).stem + ending), snd.get('peak_at')))
    elif target.suffix.lower() == '.wav' and target.is_file():
        out = Path(args.output) if args.output else target.parent
        jobs.append((target, out / (target.stem + '.spectrogram.png'), None))   # never over a picture of that name
    else:
        print(f'spectrogram: {target} is neither a render folder nor a WAV file')
        return 1
    if not jobs:
        print('spectrogram: no sound matched')
        return 1
    own_folder = target.is_dir() and not args.output
    for _, png, _ in jobs:
        if record.is_link(png):
            print(f'spectrogram: {png} is a link, and a picture is not written through one')
            return 1
        if own_folder and png.exists() and not render.as_listed(target, png.relative_to(target).as_posix()):
            print(f'spectrogram: {png} is not the picture this skill drew (changed or added since): move it away to draw it '
                  'again')
            return 1
    for src, png, peak_at in jobs:
        png.parent.mkdir(parents=True, exist_ok=True)
        if target.is_dir():
            data, rate = record.master(target, src), record.RATE
        else:
            data, info = wav.read(src)
            rate = info['rate']
        scale = spectrogram.write(data, png, rate)
        wanted = f", peak_at {peak_at:.3f} s" if peak_at is not None else ''
        print(f"{png}: {scale['seconds']} s, {scale['column_ms']} ms a column, loudest at {scale['peak_s']} s{wanted}")
    if own_folder:                                          # its own folder's pictures are part of the render
        render.refresh_listing(target, [png.relative_to(target).as_posix() for _, png, _ in jobs])
    print('Top: the level (the loudest millisecond marked white). Below: frequency on a log scale, 20 Hz at the '
          'bottom to 20 kHz at the top, faint lines at 100 Hz, 1 kHz and 10 kHz; time to the right, a faint line every '
          '0.1 s and a brighter one every second; level from black (80 dB under the loudest) to white-yellow.')
    return 0


def _clean_downloads(args):
    if args.trash or args.exports:
        print('clean: --downloads only lists: this skill does not move or delete downloaded archives')
        return 1
    try:
        result = clean.downloads(args.folder, _library(args, must_exist=False))
    except clean.CleanError as error:
        print(f'clean: {error}')
        return 1
    if args.json:
        print(json.dumps(result, indent=1, ensure_ascii=False))
        return 0
    for item in result['held']:
        print(f"fully imported: {item['name']} ({item['bytes'] / 1e6:.1f} MB)")
    for item in result['left']:
        print(f"left alone: {item['name']} ({item['why']})")
    if result['held']:
        print('The library holds every CC0 recording in the files marked fully imported (it does not take the others). This '
              'skill leaves them where they are: if you will not import them again, you can delete them yourself.')
    else:
        print('no download here is fully imported')
    return 0


def cmd_clean(args):
    if args.downloads:
        return _clean_downloads(args)
    try:
        result = clean.clean(args.folder, args.keep or [], move=args.trash, exports=args.exports)
    except clean.CleanError as error:
        print(f'clean: {error}')
        return 1
    if args.json:
        print(json.dumps(result, indent=1, ensure_ascii=False))
        return 0
    shown, verb = (result['moved'], 'moved') if args.trash else (result['move'], 'would move')
    for item in shown:
        print(f"{verb} to {clean.TRASH}: {item['folder']} ({item['kind']}, {item['bytes'] / 1e6:.1f} MB)")
    for item in result['left']:
        print(f"left alone: {item['folder']} ({item['why']})")
        for s in item.get('stays', []):
            print(f"    {s['path']}: {s['why']}")
    if not shown:
        print('nothing to move')
    elif args.trash:
        print(f"They are in {result['trash']}: to restore one, move it back. This skill never deletes them; when you are "
              f'sure, delete {clean.TRASH} yourself.')
    else:
        print('nothing was moved; run again with --trash to move what is listed (nothing is deleted)')
    if result['trash_bytes']:
        print(f"{clean.TRASH} holds {result['trash_bytes'] / 1e6:.1f} MB")
    return 0


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

    mu = sub.add_parser('music', help="read a music-production project, or quote a part's written notes",
                        description="What a music-production project (song.json) declares: tempo, meter, sections, harmony, plan and parts, counted from "
                        "bar 1. With --track and --bars, those bars' written notes, ready for an instrument or surge layer. It works out no key and changes no music.")
    mu.add_argument('song', help="the project's song.json")
    mu.add_argument('--track', help='the id of the part to quote')
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
