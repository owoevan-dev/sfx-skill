"""Helpers the commands share: loading a project, printing flags and levels, finding FFmpeg and the sample library."""
import json
from pathlib import Path

from .. import ffmpeg, mpbridge, project, samples


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
