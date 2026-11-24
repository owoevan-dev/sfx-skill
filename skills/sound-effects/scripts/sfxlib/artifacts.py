"""Faults the agent cannot hear: isolated clicks, and a sound's loudest moment away from where it was meant to be.

Clicks. A click is a short impulsive disturbance, recognised by how far it stands out from the signal around it. Here
a click is a millisecond whose energy above 3 kHz lies more than CLICK_RISE_DB above the median of the 10 ms before it,
while most of the 3rd to the 12th milliseconds after it stay more than CLICK_FALL_DB under it, at no more than
CLICK_FLOOR_DB under the file's loudest millisecond above 3 kHz, and fewer than TRAIN_EDGES others like it (within 6 dB)
lie within 30 ms either side. A struck object rings on above 3 kHz for tens of milliseconds, and a buzzy tone or a pulse
train repeats its sharp edges, so neither is taken for one; two clicks close together are still two clicks. A click is
also broadband: a cut or a spike spreads its
energy to the top of the spectrum (a step falls only 6 dB an octave), so at least BROADBAND_SHARE of the 3-20 kHz
energy of the 2 ms around it must lie above 10 kHz; a band-passed tick (a debris crackle, a filtered impact) is a
designed sound, not a click. A cut into a waveform, a reversed transient ending abruptly or a broadband crackle in a
recording is reported. Clicks within
PEAK_GUARD_MS of the sound's loudest moment are its own attack (an interface tick is one click on purpose) and are
not reported. The thresholds are this skill's choices.

Stray attacks. In an instrument's own performance the notes are known, so an attack that no note explains can be named: a tick
left inside a sample passes the click test, since it rings on for tens of milliseconds as a small hit does. Between one note
start and the next, it is a millisecond whose energy above 3 kHz stands at least STRAY_RISE_DB over the loudest of the
STRAY_BEFORE_MS before the millisecond ahead of it (so an attack that rises over two milliseconds is still one), within STRAY_TOP_DB of the interval's loudest, more than STRAY_AFTER_MS after the note starts (a
sample's own attack can come late), and the only such moment of the interval (several are the instrument's own pulses or
sparkles). These numbers are this skill's choices, set on its own measurement of the notes of the sample banks it may use.

The loudest moment. A sound can say where its main event should fall (`peak_at`, seconds); the check compares that
with the loudest millisecond of the first variant (the one that keeps the written values) and flags a difference of
more than PEAK_AT_TOLERANCE_S, this skill's choice: about the time under which two onsets fuse into one event."""
import numpy as np
from . import measure

RATE = 48000
HF_HZ = 3000.0
CLICK_RISE_DB = 18.0
CLICK_FALL_DB = 12.0
CLICK_FLOOR_DB = 40.0
PEAK_GUARD_MS = 5
BROADBAND_SHARE = 0.1
PEAK_AT_TOLERANCE_S = 0.02
TRAIN_EDGES = 2
STRAY_RISE_DB = 18.0
STRAY_BEFORE_MS = 50
STRAY_TOP_DB = 12.0
STRAY_AFTER_MS = 250
STRAY_JOIN_MS = 3                # a rise over this few milliseconds is one attack


def per_ms(power, rate=RATE):
    """The mean of `power` over each millisecond, the last (perhaps shorter) one included."""
    hop = rate // 1000
    full = len(power) // hop
    out = power[:full * hop].reshape(full, hop).mean(axis=1) if full else np.zeros(0)
    tail = power[full * hop:]
    return np.append(out, tail.mean()) if len(tail) else out


def _hf_envelope_db(x, rate=RATE):
    """Per millisecond, the power above HF_HZ of all channels together (channels in opposite polarity do not cancel)."""
    x = np.asarray(x, dtype=np.float64).reshape(len(x), -1)
    if len(x) == 0:
        return np.array([-200.0])
    spec = np.fft.rfft(x, axis=0)
    spec[np.fft.rfftfreq(len(x), 1 / rate) < HF_HZ] = 0
    hf = np.fft.irfft(spec, len(x), axis=0)
    return 10 * np.log10(np.maximum(per_ms(np.sum(hf ** 2, axis=1), rate), 1e-20))


def _broadband(x, ms, rate=RATE):
    """True when the 2 ms around millisecond `ms` carry at least BROADBAND_SHARE of their 3-20 kHz energy above
    10 kHz: a cut or a spike spreads energy to the top of the spectrum, a band-passed tick does not."""
    hop = rate // 1000
    seg = x[max(0, (ms - 1) * hop):(ms + 1) * hop]
    if len(seg) < 8:
        return False
    sp = np.sum(np.abs(np.fft.rfft(seg * np.hanning(len(seg))[:, None], axis=0)) ** 2, axis=1)
    f = np.fft.rfftfreq(len(seg), 1 / rate)
    hf = float(np.sum(sp[(f >= HF_HZ) & (f < 20000)])) or 1e-30
    return float(np.sum(sp[(f >= 10000) & (f < 20000)])) / hf >= BROADBAND_SHARE


def loudest_s(x, rate=RATE):
    """The time (s) of the loudest millisecond (all channels' power together)."""
    p = per_ms(np.sum(np.asarray(x, dtype=np.float64).reshape(len(x), -1) ** 2, axis=1), rate)
    return float(np.argmax(p)) / 1000.0 if len(p) else 0.0


def _edges_around(e, i):
    """How many other spikes within 6 dB of millisecond i lie within 30 ms either side: a pulse train has several."""
    around = np.concatenate([e[max(0, i - 30):max(0, i - 1)], [-400.0], e[i + 2:i + 33]])
    level = e[i] - 6.0
    return int(np.sum((around[1:-1] > around[:-2]) & (around[1:-1] >= around[2:]) & (around[1:-1] >= level)))


def clicks(x, rate=RATE):
    """Times (s) of isolated clicks, as described above."""
    e = _hf_envelope_db(x, rate)
    n = len(e)
    if n < 25:
        return []
    frames = np.asarray(x, dtype=np.float64).reshape(len(x), -1)
    top = float(e.max())
    peak_ms = int(round(loudest_s(x, rate) * 1000))
    found = []
    i = 10
    while i < n - 12:
        before = float(np.median(e[i - 10:i]))
        if e[i] - before > CLICK_RISE_DB and e[i] > top - CLICK_FLOOR_DB:
            after = float(np.percentile(e[i + 3:i + 13], 70))     # a ring stays up; one more spike does not count
            if (e[i] - after > CLICK_FALL_DB and _edges_around(e, i) < TRAIN_EDGES and abs(i - peak_ms) > PEAK_GUARD_MS
                    and _broadband(frames, i, rate)):
                found.append(round(i / 1000.0, 3))
                i += 3
                continue
        i += 1
    return found


def stray_attacks(x, note_starts_s, rate=RATE):
    """Times (s) of attacks in an instrument's performance `x` that no note explains, as described above."""
    if not len(x) or not len(note_starts_s):
        return []
    e = _hf_envelope_db(x, rate)
    starts = sorted({max(0, int(round(float(s) * 1000))) for s in note_starts_s} & set(range(len(e))))   # a note past the recording's end has none
    found = []
    for first, stop in zip(starts, starts[1:] + [len(e)]):
        top = float(e[first:stop].max())
        moments = []
        for i in range(max(first + STRAY_AFTER_MS + 1, STRAY_BEFORE_MS + 1), min(stop, len(e) - 12)):
            if e[i] >= top - STRAY_TOP_DB and e[i] - float(e[i - 1 - STRAY_BEFORE_MS:i - 1].max()) >= STRAY_RISE_DB:
                if not moments or i - moments[-1] >= STRAY_JOIN_MS:
                    moments.append(i)
        if len(moments) == 1:
            found.append(round(moments[0] / 1000.0, 3))
    return found


def peak_at_finding(x, peak_at, rate=RATE, loop=False):
    """None, or a message when the loudest moment misses `peak_at` by more than PEAK_AT_TOLERANCE_S (around the seam for
    a loop, whose end runs into its start)."""
    if peak_at is None:
        return None
    t = loudest_s(x, rate)
    gap = abs(t - float(peak_at))
    if loop:
        period = len(x) / rate
        gap = min(gap % period, period - gap % period)
    if gap <= PEAK_AT_TOLERANCE_S:
        return None
    if not loop and abs(measure.landing(x)['strike_ms'] / 1000 - float(peak_at)) <= PEAK_AT_TOLERANCE_S:
        return None                    # the hit lands there, and a low body just after it holds more energy
    return (f'its loudest moment is at {t:.3f} s, not at {float(peak_at):.3f} s where peak_at puts the main event: '
            'another layer (a swell, a reversed tail, a later hit) is louder than the one meant to lead')
