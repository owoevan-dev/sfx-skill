"""Steady background noise taken out of a recording by spectral subtraction, and a time map for reading a recording.

Spectral subtraction (Boll, "Suppression of acoustic noise in speech using spectral subtraction", IEEE Transactions
on Acoustics, Speech, and Signal Processing 27(2), 1979): the recording is cut into overlapping frames, the noise's
power spectrum is estimated from a stretch that holds only noise, and that power is subtracted from every frame.
Subtracting a little more than the estimate (over-subtraction) and never going below a floor that is a fraction of
the noise (Berouti, Schwartz and Makhoul, ICASSP 1979, as Lu and Loizou describe it in "A geometric approach to
spectral subtraction", Speech Communication 50, 2008) keep the residue from turning into the short random tones ('musical noise') that plain subtraction leaves. Here the noise
stretch is named by the agent from the time map, one fixed estimate serves the whole recording, and the floor is
`reduce_db` under the noise: at most that much noise is taken away.

The power a frame's gain is computed from is averaged over that frame and its two neighbours: Boll's magnitude
averaging, which lowers the spread of the noise's power from frame to frame (the error that subtraction leaves); Boll
found that averaging over more than three half-overlapped windows (38.4 ms in all) blurred speech, and three frames
here span 43 ms. Frames: 1024 samples at 48 kHz (21 ms), half overlapped, a square-root Hann window before and after,
which reconstructs the recording exactly where nothing is subtracted. The default over-subtraction of 3 is this
skill's choice: on steady white noise it takes away about 17 dB of a 20 dB `reduce_db` while a tone 34 dB above the
noise stays within 0.1 dB (tests/test_denoise.py).

The time map (`time_map`) lists, frame by frame, the level, peak, spectral centroid, the share of energy under
150 Hz and a pitch estimate (autocorrelation), so events can be found without listening; `events` lists the strikes in
a recording, `quietest` finds the quietest stretch (a candidate for the noise estimate) and `bands` the octave-band
levels of a stretch (where its noise lies)."""
import numpy as np

N = 1024
HOP = N // 2
OCTAVES = (20, 60, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)
ONSET_FRAME_S = 0.01             # the level is read in frames of this length
ONSET_RISE_DB = 9.0              # a rise this large from one frame to the next is a strike (this skill's choice)
ONSET_WITHIN_DB = 30.0           # and it must come within this much of the recording's loudest frame
ONSET_GAP_S = 0.03               # one strike is not counted twice within this time
EVENT_PEAK_S = 0.2               # a strike's peak lies within this time after it
EVENT_ONSET_DB = 24.0            # its onset is where it is this far under its peak
EVENT_END_DB = 30.0              # and it has ended when its level has fallen this far


class DenoiseError(ValueError):
    """The noise stretch is outside the recording or too short."""


def _window():
    return np.sqrt(0.5 - 0.5 * np.cos(2 * np.pi * np.arange(N) / N))      # periodic Hann, square root


def _frames(x):
    """Frames of one channel padded by a frame at each end: (frames, n x N), and the padded length."""
    pad = np.concatenate([np.zeros(N), x, np.zeros(N + (-len(x)) % HOP)])
    count = (len(pad) - N) // HOP + 1
    idx = np.arange(N)[None, :] + HOP * np.arange(count)[:, None]
    return pad[idx], len(pad)


def subtract(x, rate, noise, reduce_db=20.0, over=3.0):
    """x (frames x channels, or mono) with the noise of the stretch `noise` = [start, end] seconds subtracted."""
    x = np.asarray(x, dtype=np.float64)
    mono = x.ndim == 1
    x2 = x.reshape(len(x), -1)
    a, b = float(noise[0]), float(noise[1])
    if not (0 <= a < b <= len(x2) / rate):
        raise DenoiseError(f'the noise stretch {a:g}-{b:g} s is not inside the recording ({len(x2) / rate:.3f} s)')
    if b - a < 0.1:
        raise DenoiseError('the noise stretch must last at least 0.1 s')
    win = _window()
    beta = 10 ** (-float(reduce_db) / 10)
    out = np.empty_like(x2)
    for c in range(x2.shape[1]):
        frames, length = _frames(x2[:, c])
        spec = np.fft.rfft(frames * win, axis=1)
        power = np.abs(spec) ** 2
        starts = (np.arange(len(frames)) * HOP - N) / rate               # each frame's start in the recording
        inside = (starts >= a) & (starts + N / rate <= b)
        if inside.sum() < 2:
            raise DenoiseError('the noise stretch is too short for two analysis frames')
        noise_power = power[inside].mean(axis=0)
        smooth = power.copy()
        smooth[1:-1] = (power[:-2] + power[1:-1] + power[2:]) / 3
        with np.errstate(divide='ignore', invalid='ignore'):
            ratio = np.where(smooth > 0, noise_power[None, :] / smooth, np.inf)
        gain2 = np.maximum(1 - over * ratio, beta * ratio)
        gain = np.sqrt(np.clip(np.nan_to_num(gain2, nan=beta, posinf=beta), 0.0, 1.0))
        y = np.fft.irfft(spec * gain, N, axis=1) * win
        acc = np.zeros(length)
        for i, frame in enumerate(y):
            acc[i * HOP:i * HOP + N] += frame
        out[:, c] = acc[N:N + len(x2)]
    return out[:, 0] if mono else out


def _mono(x):
    return np.asarray(x, dtype=np.float64).reshape(len(x), -1).mean(axis=1)


NEAR_HIGHEST = 0.9        # this skill's own: a peak this close to the highest counts as the same period's


def _pitch(frame, rate, lo=20.0, hi=2000.0):
    f = frame - frame.mean()
    if len(f) < 4 or np.sqrt(np.mean(f ** 2)) < 1e-4:
        return 0.0
    n = len(f)
    ac = np.fft.irfft(np.abs(np.fft.rfft(f * np.hanning(n), 4 * n)) ** 2)[:n]
    a, b = int(rate / hi), min(int(rate / lo), n - 1)
    if b <= a:
        return 0.0
    below = np.flatnonzero(ac[1:b] < 0)     # the first peak past the first dip is the period; a lag before it only says
    if not len(below):                      # the signal has not changed yet
        return 0.0
    start = max(a, int(below[0]) + 1)
    if start >= b:
        return 0.0
    around = ac[start - 1:b + 1]
    peaks = start + np.flatnonzero((around[1:-1] > around[:-2]) & (around[1:-1] >= around[2:]))
    if not len(peaks):
        return 0.0
    k = int(peaks[np.argmax(ac[peaks] >= NEAR_HIGHEST * ac[peaks].max())])    # later peaks at multiples of it are as high
    if ac[k] <= 0.3 * ac[0]:
        return 0.0
    bend = ac[k - 1] - 2 * ac[k] + ac[k + 1]                                  # the top of the peak, between two samples
    return round(rate / (k + 0.5 * (ac[k - 1] - ac[k + 1]) / bend if bend < 0 else k), 1)


def time_map(x, rate, hop=0.1, start=0.0, end=None):
    """[{'t', 'rms_db', 'peak_db', 'centroid_hz', 'low_share', 'pitch_hz'}] per frame of `hop` seconds."""
    x = _mono(x)
    h = max(16, int(hop * rate))
    first, last = int(start * rate), len(x) if end is None else min(len(x), int(end * rate))
    rows = []
    for i in range(first, last - h + 1, h):
        fr = x[i:i + h]
        sp = np.abs(np.fft.rfft(fr * np.hanning(len(fr))))
        fq = np.fft.rfftfreq(len(fr), 1 / rate)
        total = float(np.sum(sp ** 2)) or 1e-30
        rows.append({'t': round(i / rate, 3),
                     'rms_db': round(20 * np.log10(np.sqrt(np.mean(fr ** 2)) + 1e-10), 1),
                     'peak_db': round(20 * np.log10(np.max(np.abs(fr)) + 1e-10), 1),
                     'centroid_hz': round(float(np.sum(sp * fq) / (np.sum(sp) + 1e-12))),
                     'low_share': round(float(np.sum(sp[fq < 150] ** 2)) / total, 2),
                     'pitch_hz': _pitch(fr[:min(len(fr), int(0.1 * rate))], rate)})
    return rows


def spaced(found, gap):
    """`events` joined so that strikes less than `gap` seconds after the first of a group count as one (a heel and a toe as
    one step): the group's first onset, its loudest strike's peak and level, and its last end."""
    out = []
    for e in found:
        if out and e['onset'] - out[-1]['onset'] < gap:
            group = out[-1]
            if e['db'] > group['db']:
                group['peak'], group['db'] = e['peak'], e['db']
            group['end'] = max(group['end'], e['end'])
        else:
            out.append(dict(e))
    return out


def events(x, rate):
    """The strikes in a recording, in order: [{'onset', 'peak', 'end' (seconds), 'db' (the peak against the recording's
    loudest sample)}]. A strike is where the level jumps within one 10 ms frame. Its peak is the loudest sample in the
    EVENT_PEAK_S after it (or up to the next strike); its onset, walking back from the peak, is where the sound falls
    EVENT_ONSET_DB under the peak; it ends where the level has fallen EVENT_END_DB, or at the next strike. A steady
    sound has none."""
    x = _mono(x)
    h = max(1, int(ONSET_FRAME_S * rate))
    n = len(x) // h
    if n < 2:
        return []
    db = 20 * np.log10(np.sqrt(np.mean(x[:n * h].reshape(n, h) ** 2, axis=1)) + 1e-10)
    top = float(db.max())
    gap = int(round(ONSET_GAP_S / ONSET_FRAME_S))
    after = db[1:1 + int(round(EVENT_PEAK_S / ONSET_FRAME_S))]        # a recording trimmed tight starts on its first strike
    hits = [0] if db[0] > top - ONSET_WITHIN_DB and float(after.min()) < db[0] - ONSET_RISE_DB else []
    for i in np.flatnonzero((db[1:] - db[:-1] > ONSET_RISE_DB) & (db[1:] > top - ONSET_WITHIN_DB)) + 1:
        if not hits or i - hits[-1] >= gap:
            hits.append(int(i))
    loudest = float(np.max(np.abs(x))) or 1.0
    step = max(1, int(0.001 * rate))
    out = []
    for k, i in enumerate(hits):
        stop = hits[k + 1] * h if k + 1 < len(hits) else len(x)
        first = max(0, (i - 1) * h)
        last = min(stop, i * h + int(EVENT_PEAK_S * rate))
        peak = first + int(np.argmax(np.abs(x[first:last])))
        level = abs(float(x[peak]))
        floor = level * 10 ** (-EVENT_ONSET_DB / 20)
        onset = peak
        while onset - step >= first and float(np.max(np.abs(x[onset - step:onset]))) > floor:
            onset -= step
        frame = peak // h
        end = stop
        if frame < n:
            quiet = np.flatnonzero(db[frame:min(n, stop // h)] < float(db[frame:frame + 3].max()) - EVENT_END_DB)
            if len(quiet):
                end = (frame + int(quiet[0])) * h
        out.append({'onset': round(onset / rate, 3), 'peak': round(peak / rate, 3), 'end': round(end / rate, 3),
                    'db': round(20 * np.log10(max(level, 1e-10) / loudest), 1)})
    return out


def quietest(x, rate, length=0.3):
    """(start, end, rms dB) of the quietest stretch of `length` seconds, stepping by a tenth of it. A stretch holding
    digital silence (padding, with no noise in it to learn from) is passed over while any other is left."""
    x = _mono(x)
    n = int(length * rate)
    if len(x) <= n:
        return 0.0, round(len(x) / rate, 3), round(20 * np.log10(np.sqrt(np.mean(x ** 2)) + 1e-10), 1)
    step = max(1, n // 10)
    zero = np.concatenate([[0], np.cumsum(np.abs(x) < 1e-7)])
    power = np.concatenate([[0.0], np.cumsum(x ** 2)])
    starts = range(0, len(x) - n + 1, step)
    usable = [i for i in starts if zero[i + n] - zero[i] < 0.01 * n]
    best = min(usable or starts, key=lambda i: float(power[i + n] - power[i]))
    return round(best / rate, 3), round((best + n) / rate, 3), \
        round(20 * np.log10(np.sqrt(np.mean(x[best:best + n] ** 2)) + 1e-10), 1)


def bands(x, rate, start, end):
    """{low edge Hz: RMS level in dBFS} of the octave bands 20 Hz-Nyquist over [start, end] seconds, on the scale of
    the stretch's own RMS level: the bands' powers add up to it."""
    x = _mono(x)[int(start * rate):int(end * rate)]
    if len(x) < 16:
        return {}
    w = np.hanning(len(x))
    sp = 2 * np.abs(np.fft.rfft(x * w)) ** 2 / (len(x) * np.sum(w ** 2))     # Parseval, one side of the spectrum
    fq = np.fft.rfftfreq(len(x), 1 / rate)
    edges = list(OCTAVES) + [rate / 2]
    return {lo: round(10 * np.log10(np.sum(sp[(fq >= lo) & (fq < hi)]) + 1e-20), 1)
            for lo, hi in zip(edges[:-1], edges[1:]) if lo < rate / 2}
