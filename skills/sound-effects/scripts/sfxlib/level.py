"""The level step of a render.

match: every file is brought to one loudness plus its sound's trim_db. A one-shot's loudness is that of its loudest
200 ms (loudness.brief); a loop's is its integrated loudness, the measure ITU-R BS.1770-4 gives for programmes (a loop
is heard for long stretches, and a gust in it should not set its level as a one-shot's peak does). The level is the
one asked for (`target`), or without one the highest at which no file passes the true-peak ceiling by more than
limit_db; a level asked for gives way when a file's peaks cannot reach it within limit_db; a file that does pass the ceiling is brought back
under it by a true-peak limiter (dynamics.limit), losing at most limit_db of its highest peaks. With limit_db 0 the
peakiest file sets the level for the whole set; the default lets a peaky sound give up a few dB of its peaks instead
of lowering every other sound (this skill's choice, after CC0 game sound libraries measured 4 to 10 dB louder
than our sets during development). A file may carry its own, lower limit_db: a file whose limiting would leave an
offset is limited less (render.py). The file that sets the level is reported as
`limited_by`. Keeping a set's sounds within a
narrow range of loudness follows Brewster, Wright and Edwards' earcon guidelines; the default ceiling of -1 dBTP is the
maximum true peak PlayStation recommends (Audiokinetic 2024).

peak: each file's true peak goes to the ceiling less any negative trim_db; nothing is limited."""
import math

DEFAULT_LIMIT_DB = 6.0


def plan(files, mode, ceiling, limit_db=0.0, target=None):
    """files: [{'key', 'loudness' (LUFS: its loudest 200 ms, or integrated for a loop), 'true_peak' (dBTP), 'trim_db',
    and optionally 'limit_db', the file's own limit, which can only lower the set's}]; `target`, a level to hold the set
    at; returns {'gains': {key: dB}, 'over_db': {key: dB the gained true peak passes the ceiling by, for the limiter},
    'target_lufs' (match only), 'limited_by' (match only; None when the set sits at the level asked for)}."""
    for f in files:
        if not math.isfinite(f['true_peak']) or (mode == 'match' and not math.isfinite(f['loudness'])):
            raise ValueError(f"{f['key']} is silent and cannot be levelled")
    if mode == 'peak':
        gains = {f['key']: ceiling + min(float(f['trim_db']), 0.0) - f['true_peak'] for f in files}
        return {'gains': gains, 'over_db': {k: 0.0 for k in gains}, 'target_lufs': None, 'limited_by': None}
    if mode != 'match':
        raise ValueError(f'unknown level mode {mode!r}')
    limit_db = max(float(limit_db), 0.0)
    caps = {f['key']: min(limit_db, max(float(f.get('limit_db', limit_db)), 0.0)) for f in files}
    headroom = {f['key']: ceiling + caps[f['key']] - f['true_peak'] - float(f['trim_db']) + f['loudness'] for f in files}
    limited_by = min(headroom, key=headroom.get)
    highest = headroom[limited_by]
    if target is None or target >= highest:
        target = highest
    else:
        limited_by = None
    gains = {f['key']: target + float(f['trim_db']) - f['loudness'] for f in files}
    over = {f['key']: max(0.0, f['true_peak'] + gains[f['key']] - ceiling) for f in files}
    return {'gains': gains, 'over_db': over, 'target_lufs': target, 'limited_by': limited_by}
