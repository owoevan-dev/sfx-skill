"""A render: every variant of every sound synthesized, levelled as a set, written as 48 kHz 24-bit masters, measured.

The folder holds master/<sound>_<nn>.wav (nn from 01), resolved.json (every value each file was made from: the
parameters drawn, the layers and chain, the seed and the gain), check.json (check.py), audition.html (audition.py),
files.json (each file with its size and SHA-256) and report.json (a summary). A render never writes into a folder that already holds files."""
import hashlib
import json
import math
import time
from pathlib import Path

import numpy as np

from . import (VERSION, RATE, audition, check, dsp, dynamics, instruments, level, licences, loudness, measure, mpbridge,
               surge, synth, variants, wav)
from .samples import Library as SampleLibrary, SampleError


class RenderError(Exception):
    """The render cannot start or finish."""


def file_key(sound_id, variant):
    return f'{sound_id}_{variant + 1:02d}'


def _write_json(path, data):
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + '\n', encoding='utf-8', newline='\n')


def files_record(folder):
    """[{'path', 'bytes', 'sha256'}] for every file under folder except files.json, sorted."""
    out = []
    for p in sorted(Path(folder).rglob('*')):
        if p.is_file() and p.name != 'files.json':
            data = p.read_bytes()
            out.append({'path': p.relative_to(folder).as_posix(), 'bytes': len(data),
                        'sha256': hashlib.sha256(data).hexdigest()})
    return out


def refresh_listing(folder, names):
    """files.json with the entries of the files `names` brought up to date and nothing else: a file someone added to the
    folder or changed by hand stays as it was listed, so that `clean` leaves it alone."""
    path = Path(folder) / 'files.json'
    if not path.is_file():
        return
    rows = {r['path']: r for r in json.loads(path.read_text(encoding='utf-8'))}
    for name in names:
        data = (Path(folder) / name).read_bytes()
        rows[name] = {'path': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    _write_json(path, [rows[k] for k in sorted(rows)])


def as_listed(folder, name):
    """True when the file `name` is what files.json lists under that name (its size and SHA-256)."""
    try:
        rows = {r['path']: r for r in json.loads((Path(folder) / 'files.json').read_text(encoding='utf-8'))}
        data = (Path(folder) / name).read_bytes()
    except (OSError, ValueError, KeyError, TypeError):
        return False
    row = rows.get(name)
    return row is not None and row.get('bytes') == len(data) and row.get('sha256') == hashlib.sha256(data).hexdigest()


LIMIT_HOLD_MS = 25.0      # a period of 40 Hz: the gain stays down across a low wave (dynamics.limit)
OFFSET_MARGIN = 0.8       # a limited file's offset is kept under this share of the check's DC flag


def _limit(audio, ceiling, is_loop):
    """The true-peak limiter on a file, its gain held for LIMIT_HOLD_MS; a loop is limited as it plays on (its period
    repeated three times, the middle one kept), so its join stays seamless."""
    if not is_loop:
        return dynamics.limit(audio, ceiling, hold_ms=LIMIT_HOLD_MS)
    n = len(audio)
    y, reduction = dynamics.limit(np.tile(audio, (3, 1)), ceiling, hold_ms=LIMIT_HOLD_MS, span=(n, 2 * n))
    return y[n:2 * n], reduction


def _offset_free_limit(it, ceiling):
    """The most a file may be limited without leaving an offset the check would flag: a limiter still takes a little
    more off the taller polarity of a lopsided wave, so its own limit is halved until limiting it in full leaves an
    offset under OFFSET_MARGIN of check.DC_FLAG (none below a quarter of a dB)."""
    allowed = it['limit_db']
    while allowed >= 0.25:
        y, _ = _apply(it, ceiling + allowed - it['true_peak'], ceiling)
        if abs(measure.dc_offset(y)) <= OFFSET_MARGIN * check.DC_FLAG:
            return allowed
        allowed /= 2
    return 0.0


MATCH_TOLERANCE_DB = 0.02
HOLDS_FROM_DB = 0.1       # a sound that holds the set down by less than this is not worth naming


def _loudness(y, is_loop):
    value = loudness.loop_integrated(y) if is_loop else loudness.brief(y)
    return -math.inf if value is None else value


def _apply(it, gain_db, ceiling):
    """(the file at gain_db, limited under the ceiling if it passes it; dB of peak reduction)."""
    is_loop = bool(it['sound'].get('loop'))
    y = it['audio'] * 10 ** (gain_db / 20)
    if it['true_peak'] + gain_db <= ceiling:
        return y, 0.0
    return _limit(y, ceiling, is_loop)


def _capped_target(items, target, limited_by, ceiling):
    """(the set's level, the file that sets it) once each file that passes the ceiling is held to the loudness it keeps
    when its peaks give up all of its limit."""
    for it in items:
        trim = float(it['sound']['trim_db'])
        if it['true_peak'] + target + trim - it['loudness'] <= ceiling:
            continue
        it['limit_db'] = _offset_free_limit(it, ceiling)
        g_max = ceiling + it['limit_db'] - it['true_peak']
        cap = _loudness(_apply(it, g_max, ceiling)[0], bool(it['sound'].get('loop'))) - trim
        if cap < target:
            target, limited_by = cap, it['key']
    return target, limited_by


def _what_holds(items, plan, ceiling, limit_db, asked=None):
    """What a lower trim_db on the sound that sets the set's level would free: {'sound', 'rise_db', 'then'}, how far the
    rest of the set could come up before the file `then` sets the level instead (None when nothing would change)."""
    if plan['limited_by'] is None:
        return None
    holder = next(it['sound']['id'] for it in items if it['key'] == plan['limited_by'])
    rest = [dict(it) for it in items if it['sound']['id'] != holder]      # copies: this only asks, it changes no limit
    if not rest:
        return None
    sub = level.plan([{'key': it['key'], 'loudness': it['loudness'], 'true_peak': it['true_peak'],
                       'trim_db': it['sound']['trim_db'], 'limit_db': it['limit_db']} for it in rest],
                     'match', ceiling, limit_db, asked)
    target, then = sub['target_lufs'], sub['limited_by']
    if limit_db > 0 and any(v > 0 for v in sub['over_db'].values()):
        target, then = _capped_target(rest, target, then, ceiling)
    rise = target - plan['target_lufs']
    return {'sound': holder, 'rise_db': round(rise, 2), 'then': then} if rise >= HOLDS_FROM_DB else None


def _refine(items, plan, ceiling):
    """The level step with the limiter: limiting a file lowers its loudness too (a spike that is nearly all peak loses
    loudness with every dB of peak), so the set's level is the lowest of the loudnesses each file reaches when its
    peaks give up all of its limit (its 'limit_db'), and each limited file's gain is then searched (bisection) for
    that loudness. Returns (target, gains, limited_by)."""
    target, limited_by = _capped_target(items, plan['target_lufs'], plan['limited_by'], ceiling)
    loop_of = {it['key']: bool(it['sound'].get('loop')) for it in items}
    gains = {}
    for it in items:
        trim = float(it['sound']['trim_db'])
        want = target + trim
        lo = want - it['loudness']                     # the gain without limiting: limiting only takes loudness away
        if it['true_peak'] + lo <= ceiling:
            gains[it['key']] = lo
            continue
        hi = ceiling + it['limit_db'] - it['true_peak']
        for _ in range(30):
            mid = (lo + hi) / 2
            if _loudness(_apply(it, mid, ceiling)[0], loop_of[it['key']]) < want:
                lo = mid
            else:
                hi = mid
            if hi - lo < MATCH_TOLERANCE_DB / 2:
                break
        gains[it['key']] = hi
    return target, gains, limited_by


def _synthesize(sound, spec, seed, provider, library=None, report=None):
    """(frames, loudness the level step goes by, true peak): a loop read as it plays on, a one-shot as it is. `report`
    receives what each layer contributes."""
    if sound.get('loop'):
        audio = synth.render_loop(spec, seed=seed, channels=sound['channels'], seconds=sound['loop']['seconds'],
                                  instruments=provider, samples=library, report=report)
        level_lufs = loudness.loop_integrated(audio)
        return audio, (-np.inf if level_lufs is None else level_lufs), loudness.loop_true_peak_db(audio)
    audio = synth.render_sound(spec, seed=seed, channels=sound['channels'], instruments=provider, samples=library,
                               report=report)
    return audio, loudness.brief(audio), loudness.true_peak_db(audio)


def _recordings(items, library):
    """Each recording the render's sample layers played, once, with its pack, licence, source page and SHA-256."""
    used = sorted({(layer['source']['library'], layer['source']['file']) for it in items
                   for layer in it['spec']['layers'] if layer.get('source', {}).get('type') == 'sample'})
    return [library.provenance(lib, name) for lib, name in used]


def _surge_takes(proj, folder, config, record, music_skill):
    """The project's Surge takes (surge.Provider), every layer's take checked before the render makes its folder; None for a project
    with no Surge layer."""
    if not any(layer.get('source', {}).get('type') == 'surge' for sound in proj['sounds'] for layer in sound.get('layers', [])):
        return None
    takes = surge.Provider(folder, config, music_skill, record=record)
    for sound in proj['sounds']:
        for v in range(sound['variants']):
            for layer in variants.spec_for(sound, proj['seed'], v)[0]['layers']:
                if layer.get('source', {}).get('type') == 'surge':
                    try:
                        takes.check(layer['source'])
                    except surge.SurgeError as error:
                        raise RenderError(f"{sound['id']}: {error}") from None
    return takes


def render(proj, out_dir, config=None, samples=None, ffmpeg=None, page=True, takes=None, record_takes=False, music_skill=None):
    """Render a validated project (project.validate) into out_dir; returns the report. `config` (music-production's
    configuration, a dict) is needed by instrument layers, which are rendered into out_dir/instruments, and by sample
    layers on files other than WAV (its FFmpeg decodes them); `samples` is the sample library's folder, or a list of
    them (samples.py); page=False leaves out the audition page while iterating (`sfx.py audition` writes it later).
    `takes` is the folder of the project's Surge takes, and only `record_takes` lets a missing take be played by Surge
    (surge.py; `music_skill` names the music-production folder if it is not where it is usually found)."""
    started = time.time()
    out = Path(out_dir)
    if out.exists() and any(out.iterdir()):
        raise RenderError(f'{out} already holds files; a render goes into a new folder')
    if takes is not None and (out.absolute() == Path(takes).absolute() or Path(takes).absolute() in out.absolute().parents):
        raise RenderError(f"{out} is in the project's {surge.FOLDER}, which holds only takes: render into another folder")
    surge_takes = _surge_takes(proj, takes, config, record_takes, music_skill)
    try:
        (out / 'master').mkdir(parents=True, exist_ok=False)      # the render's claim on the folder: a second one at once stops here
    except FileExistsError:
        raise RenderError(f'{out} is being written by another render; a render goes into a new folder') from None
    (out / '.gdignore').write_bytes(b'')               # inside a Godot project the editor would import every file here
    provider = instruments.Provider(config, out / 'instruments') if config else None
    if surge_takes is not None:
        surge_takes.work = out / surge.WORK             # a recording's job, patch copy and raw take, emptied once read
        provider = surge.Players(provider, surge_takes)
    library = None
    if samples:
        try:
            ffmpeg = ffmpeg or (mpbridge.ffmpeg_path(config) if config else None)
            library = SampleLibrary(samples, ffmpeg)
        except (SampleError, mpbridge.MusicSkillError) as error:
            raise RenderError(str(error)) from None
    items = []
    for sound in proj['sounds']:
        for v in range(sound['variants']):
            spec, drawn, params = variants.spec_for(sound, proj['seed'], v)
            spec = variants.with_style(spec, proj.get('style'), sound.get('style', True))
            seed = dsp.seed_for(proj['seed'], sound['id'], v)
            layers = []
            try:
                audio, level_lufs, peak = _synthesize(sound, spec, seed, provider, library, layers)
            except (ValueError, instruments.InstrumentError, SampleError) as error:
                raise RenderError(f"{sound['id']}: {error}") from None
            items.append({'sound': sound, 'variant': v, 'key': file_key(sound['id'], v), 'spec': spec,
                          'drawn': drawn, 'params': params, 'seed': seed, 'audio': audio,
                          'loudness': level_lufs, 'true_peak': peak, 'layers': layers})
    if library is not None:
        for it in items:                               # one file at a time: takes of different variants never mix
            used = {library.provenance(layer['source']['library'], layer['source']['file']).get('licence')
                    for layer in it['spec']['layers'] if layer.get('source', {}).get('type') == 'sample'}
            why = licences.clash(used)
            if why:
                raise RenderError(f"{it['sound']['id']}: {why}. Layer them in separate sounds, or use other recordings")
    ceiling = proj['level']['ceiling_dbtp']
    limit_db = proj['level'].get('limit_db', 0.0)
    for it in items:
        it['limit_db'] = limit_db
    try:
        plan = level.plan([{'key': it['key'], 'loudness': it['loudness'], 'true_peak': it['true_peak'],
                            'trim_db': it['sound']['trim_db'], 'limit_db': it['limit_db']} for it in items],
                          proj['level']['mode'], ceiling, limit_db, proj['level'].get('target_lufs'))
    except ValueError as error:
        raise RenderError(str(error)) from None
    if proj['level']['mode'] == 'match' and limit_db > 0 and any(v > 0 for v in plan['over_db'].values()):
        plan['target_lufs'], plan['gains'], plan['limited_by'] = _refine(items, plan, ceiling)
    holds = _what_holds(items, plan, ceiling, limit_db, proj['level'].get('target_lufs'))
    by_sound = {}
    for it in items:
        gain = plan['gains'][it['key']]
        is_loop = bool(it['sound'].get('loop'))
        audio, limited = _apply(it, gain, ceiling)
        rel = f"master/{it['key']}.wav"
        loop = (0, len(audio)) if is_loop else None
        wav.write(out / rel, audio, rate=RATE, bits=24, dither=True, seed=dsp.seed_for(proj['seed'], it['key'], 'dither'),
                  loop=loop)
        entry = {'n': it['variant'] + 1, 'file': rel, 'seed': it['seed'], 'drawn': it['drawn'], 'gain_db': gain,
                 'loudness_before_lufs': it['loudness'] if math.isfinite(it['loudness']) else None,
                 'true_peak_before_dbtp': it['true_peak'],
                 'limited_db': limited, 'frames': int(len(audio)), 'layers': it['layers'], 'spec': it['spec']}
        if it['params'] is not None:
            entry['params'] = it['params']
        heard = sorted({f"{layer['source']['library']}/{layer['source']['file']}" for layer in it['spec']['layers']
                        if layer.get('source', {}).get('type') == 'sample'})
        if heard:
            entry['recordings'] = heard                 # takes differ between variants, and so can what a file must credit
        by_sound.setdefault(it['sound']['id'], []).append(entry)
    sounds = []
    for sound in proj['sounds']:
        record = {k: sound[k] for k in ('id', 'category', 'use', 'channels', 'trim_db', 'notes', 'vary')}
        if sound.get('peak_at') is not None:
            record['peak_at'] = sound['peak_at']
        if sound.get('crackle'):
            record['crackle'] = True
        record['level_measure'] = 'integrated' if sound.get('loop') else 'brief'
        if sound.get('loop'):
            record['loop'] = dict(sound['loop'])
        if 'recipe' in sound:
            record['recipe'] = sound['recipe']
            record['params'] = sound['params']
        record['variants'] = by_sound[sound['id']]
        played = sorted({f"{layer['source']['library']}/{layer['source']['file']}" for it in items if it['sound'] is sound
                         for layer in it['spec']['layers'] if layer.get('source', {}).get('type') == 'sample'})
        if played:
            record['recordings'] = played
        sounds.append(record)
    resolved = {'schema': proj['schema'], 'made_by': f'sound-effects {VERSION}', 'title': proj['title'],
                'seed': proj['seed'], 'rate': RATE,
                'level': {'mode': proj['level']['mode'], 'ceiling_dbtp': proj['level']['ceiling_dbtp'],
                          'target_lufs': plan['target_lufs'], 'limited_by': plan['limited_by'],
                          'held': proj['level'].get('target_lufs') is not None},
                'flows': proj['flows'], 'sounds': sounds}
    if holds:
        resolved['level']['next'] = holds
    if library is not None and _recordings(items, library):
        resolved['recordings'] = _recordings(items, library)
    if surge_takes is not None:
        resolved['surge_takes'] = surge_takes.listing()
    _write_json(out / 'resolved.json', resolved)
    report_check = check.run(out)
    if page:
        audition.build(out, ffmpeg=ffmpeg)
    report = {'status': 'RENDERED', 'title': proj['title'], 'files': len(items),
              'target_lufs': plan['target_lufs'], 'limited_by': plan['limited_by'],
              'flags': len(report_check['flags']), 'seconds': round(time.time() - started, 2)}
    if surge_takes is not None:
        report['surge_takes'] = {'recorded': surge_takes.recorded, 'played': len(surge_takes.used), 'folder_bytes': surge_takes.size()}
    _write_json(out / 'report.json', report)
    _write_json(out / 'files.json', files_record(out))
    return report
