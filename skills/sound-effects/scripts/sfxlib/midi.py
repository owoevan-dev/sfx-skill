"""A minimal Standard MIDI File writer for recorded layers: one track (format 0), 960 ticks per quarter note at 60
quarter notes per minute, so a tick is 1/960 s and times in seconds map straight onto beats.

Bank select is sent as controller 0 (bank // 128) and controller 32 (bank % 128), the MMA convention FluidSynth follows
with synth.midi-bank-select=mma, as music-production runs it. A drum part plays on channel 10 (index 9)."""
import struct

PPQ = 960
TEMPO_US = 1_000_000        # one quarter note a second


def _vlq(value):
    """A variable-length quantity (the MIDI file format's delta-time encoding)."""
    out = [value & 0x7F]
    value >>= 7
    while value:
        out.append(0x80 | (value & 0x7F))
        value >>= 7
    return bytes(reversed(out))


def smf(notes, program=0, bank=0, drums=False, tail_s=3.0):
    """The bytes of a MIDI file playing `notes` ([(time_s, pitch, duration_s, velocity), ...]) on one preset, and
    holding the end tail_s after the last note ends, so a renderer plays the release out."""
    ch = 9 if drums else 0
    events = [(0, 0, bytes([0xFF, 0x51, 0x03]) + TEMPO_US.to_bytes(3, 'big')),
              (0, 1, bytes([0xB0 | ch, 0, (int(bank) >> 7) & 0x7F])),
              (0, 2, bytes([0xB0 | ch, 32, int(bank) & 0x7F])),
              (0, 3, bytes([0xC0 | ch, int(program) & 0x7F]))]
    last = 0
    for t, pitch, duration, velocity in notes:
        on = int(round(float(t) * PPQ))
        off = max(on + 1, int(round((float(t) + float(duration)) * PPQ)))
        events.append((on, 5, bytes([0x90 | ch, int(pitch) & 0x7F, max(1, min(127, int(velocity)))])))
        events.append((off, 4, bytes([0x80 | ch, int(pitch) & 0x7F, 0])))
        last = max(last, off)
    end = last + int(round(float(tail_s) * PPQ))
    events.sort(key=lambda e: (e[0], e[1]))
    body, now = b'', 0
    for tick, _, data in events:
        body += _vlq(tick - now) + data
        now = tick
    body += _vlq(end - now) + bytes([0xFF, 0x2F, 0x00])
    header = b'MThd' + struct.pack('>IHHH', 6, 0, 1, PPQ)
    return header + b'MTrk' + struct.pack('>I', len(body)) + body
