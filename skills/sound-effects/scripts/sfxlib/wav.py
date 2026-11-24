"""WAV files: the reader (info, read) is music-production's; the writer (write) is this skill's.

Reader: copied unchanged from music-production 0.8.0 (main ba4338a), scripts/musiclib/wavfile.py, which describes it as
a bounded RIFF/WAVE reader for any rate, PCM 8/16/24/32-bit or float32, returning float32 frames and the forward loops
of a `smpl` chunk as [start, end) frame pairs.

    MIT License, Copyright (c) 2026 music-skill contributors; the full notice is in
    LICENSES/music-production-MIT.txt beside this skill.
"""
import struct
from pathlib import Path
import numpy as np

def info(path, max_bytes=2_000_000_000):
    path = Path(path)
    size = path.stat().st_size
    if size > max_bytes:
        raise ValueError('WAV exceeds size bound')
    with path.open('rb') as f:
        head = f.read(12)
        if head[:4] != b'RIFF' or head[8:12] != b'WAVE':
            raise ValueError(f'Not RIFF/WAVE: {path.name}')
        fmt = data = smpl = None
        pos = 12
        while pos + 8 <= size:
            f.seek(pos)
            cid, csize = struct.unpack('<4sI', f.read(8))
            if pos + 8 + csize > size + 1:
                # Trailing metadata that runs past the end (a writer that left out the pad byte after an odd-sized
                # data chunk shifts the next header by one) is ignored once the audio has been found.
                if fmt is not None and data is not None:
                    break
                raise ValueError(f'Truncated chunk in {path.name}')
            if cid == b'fmt ':
                fmt = f.read(min(csize, 64))
            elif cid == b'data':
                data = (pos + 8, min(csize, size - pos - 8))
            elif cid == b'smpl':
                smpl = f.read(min(csize, 36 + 24 * 16))
            pos += 8 + csize + (csize & 1)
    if fmt is None or data is None:
        raise ValueError(f'Missing fmt/data in {path.name}')
    if len(fmt) < 16:  # the fields read below; a shorter chunk stopped the build with struct.error (0.8 step 1.7)
        raise ValueError(f'Short WAV format chunk in {path.name}')
    tag, channels, rate, _, align, bits = struct.unpack_from('<HHIIHH', fmt)
    if tag == 0xFFFE and len(fmt) >= 26:
        tag = struct.unpack_from('<H', fmt, 24)[0]
    if channels not in (1, 2) or not 8000 <= rate <= 192000 or align != channels * bits // 8:
        raise ValueError(f'Unsupported WAV layout in {path.name}')
    if (tag, bits) not in ((1, 8), (1, 16), (1, 24), (1, 32), (3, 32)):
        raise ValueError(f'Unsupported WAV encoding {tag}/{bits} in {path.name}')
    frames = data[1] // align
    # Forward loops of the `smpl` chunk as [start, end) frame pairs (the chunk names the last frame played).
    loops = []
    if smpl is not None and len(smpl) >= 36:
        count = min(struct.unpack_from('<I', smpl, 28)[0], (len(smpl) - 36) // 24)
        for i in range(count):
            _, kind, start, end, _, _ = struct.unpack_from('<6I', smpl, 36 + 24 * i)
            if kind == 0 and start < end < frames:
                loops.append((start, end + 1))
    return {'path': str(path), 'tag': tag, 'channels': channels, 'rate': rate, 'bits': bits,
            'offset': data[0], 'frames': frames, 'loops': loops}

def read(path):
    i = info(path)
    with open(i['path'], 'rb') as f:
        f.seek(i['offset'])
        raw = f.read(i['frames'] * i['channels'] * i['bits'] // 8)
    return _samples(raw, i)


def decode(data, name='WAV bytes'):
    """(frames, info) of a whole WAV file already in memory, read as `read` reads a file."""
    i = _info_of(bytes(data), name)
    return _samples(bytes(data)[i['offset']:i['offset'] + i['frames'] * i['channels'] * i['bits'] // 8], i)


def _info_of(data, name):
    import io
    size = len(data)
    f = io.BytesIO(data)
    head = f.read(12)
    if head[:4] != b'RIFF' or head[8:12] != b'WAVE':
        raise ValueError(f'Not RIFF/WAVE: {name}')
    fmt = chunk = None
    pos = 12
    while pos + 8 <= size:
        f.seek(pos)
        cid, csize = struct.unpack('<4sI', f.read(8))
        if pos + 8 + csize > size + 1:
            if fmt is not None and chunk is not None:
                break
            raise ValueError(f'Truncated chunk in {name}')
        if cid == b'fmt ':
            fmt = f.read(min(csize, 64))
        elif cid == b'data':
            chunk = (pos + 8, min(csize, size - pos - 8))
        pos += 8 + csize + (csize & 1)
    if fmt is None or chunk is None or len(fmt) < 16:
        raise ValueError(f'Missing or short fmt/data in {name}')
    tag, channels, rate, _, align, bits = struct.unpack_from('<HHIIHH', fmt)
    if tag == 0xFFFE and len(fmt) >= 26:
        tag = struct.unpack_from('<H', fmt, 24)[0]
    if channels not in (1, 2) or not 8000 <= rate <= 192000 or align != channels * bits // 8 or (tag, bits) not in ((1, 8), (1, 16), (1, 24), (1, 32), (3, 32)):
        raise ValueError(f'Unsupported WAV layout in {name}')
    return {'path': name, 'tag': tag, 'channels': channels, 'rate': rate, 'bits': bits, 'offset': chunk[0], 'frames': chunk[1] // align, 'loops': []}


def _samples(raw, i):
    if i['tag'] == 3:
        x = np.frombuffer(raw, '<f4').astype(np.float32)
    elif i['bits'] == 8:
        x = (np.frombuffer(raw, 'u1').astype(np.float32) - 128) / 128
    elif i['bits'] == 16:
        x = np.frombuffer(raw, '<i2').astype(np.float32) / 32768
    elif i['bits'] == 24:
        b = np.frombuffer(raw, 'u1').reshape(-1, 3).astype(np.int32)
        x = (((b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)) ^ 0x800000) - 0x800000).astype(np.float32) / 8388608
    else:
        x = np.frombuffer(raw, '<i4').astype(np.float32) / 2147483648
    x = x.reshape(-1, i['channels'])
    if not np.isfinite(x).all():
        raise ValueError(f'Non-finite samples in {i["path"]}')
    return x, i


# ---- Writer (this skill) ------------------------------------------------------------------------------------------

def _chunk(cid, payload):
    return struct.pack('<4sI', cid, len(payload)) + payload + (b'\0' if len(payload) & 1 else b'')


def quantize(data, bits, dither=True, seed=0):
    """Float frames (n x channels) moved onto the grid of `bits`-bit integers, with encode's seeded triangular dither:
    the values encode writes, as floats, so frames can be repeated after quantizing (a loop's repeated frame)."""
    x = np.asarray(data, dtype=np.float64)
    scale = float(2 ** (bits - 1))
    q = x * scale
    if dither:
        rng = np.random.default_rng(seed)
        q = q + (rng.random(q.shape) - rng.random(q.shape))
    return np.clip(np.round(q), -scale, scale - 1) / scale


def encode(data, rate=48000, bits=24, dither=True, seed=0, loop=None):
    """Float frames (n, or n x channels, within [-1, 1]) as the bytes of a RIFF/WAVE file: bits 16 or 24 (integer PCM) or 32
    (IEEE float). Integer formats are quantized with triangular-pdf dither of two steps peak to peak when dither=True,
    seeded so that a rerender is byte-identical; with such dither the mean and variance of the quantization error do
    not depend on the signal (nonsubtractive dither: Wannamaker, Lipshitz, Vanderkooy and Wright 2000, IEEE
    Transactions on Signal Processing 48(2), 499-516). loop=(start, end) adds a `smpl` chunk with one forward loop
    over frames [start, end); the chunk stores end - 1, the last frame played, which is how the reader reads it.
    Non-finite samples and values beyond full scale are refused: the level step keeps every file under its ceiling,
    so such a value is a fault upstream."""
    x = np.asarray(data, dtype=np.float64)
    if x.ndim == 1:
        x = x[:, None]
    if x.ndim != 2 or x.shape[1] not in (1, 2) or len(x) == 0:
        raise ValueError(f'expected n or n x 1-2 frames, got shape {np.shape(data)}')
    if not np.isfinite(x).all():
        raise ValueError('non-finite samples')
    if np.max(np.abs(x)) > 1.0 + 1e-9:
        raise ValueError(f'samples beyond full scale (peak {np.max(np.abs(x)):.4f})')
    channels = x.shape[1]
    if bits == 32:
        fmt = struct.pack('<HHIIHHH', 3, channels, rate, rate * channels * 4, channels * 4, 32, 0)
        body = x.astype('<f4').tobytes()
        chunks = [_chunk(b'fmt ', fmt), _chunk(b'fact', struct.pack('<I', len(x))), _chunk(b'data', body)]
    elif bits in (16, 24):
        scale = float(2 ** (bits - 1))
        q = (quantize(x, bits, dither, seed) * scale).round().astype('<i4')
        if bits == 16:
            body = q.astype('<i2').tobytes()
        else:
            body = q.reshape(-1).view(np.uint8).reshape(-1, 4)[:, :3].tobytes()
        width = bits // 8
        fmt = struct.pack('<HHIIHH', 1, channels, rate, rate * channels * width, channels * width, bits)
        chunks = [_chunk(b'fmt ', fmt), _chunk(b'data', body)]
    else:
        raise ValueError(f'bits must be 16, 24 or 32, not {bits}')
    if loop is not None:
        start, end = int(loop[0]), int(loop[1])
        if not 0 <= start < end <= len(x):
            raise ValueError(f'loop {loop} outside the {len(x)} frames')
        head = struct.pack('<9I', 0, 0, int(round(1e9 / rate)), 60, 0, 0, 0, 1, 0)
        chunks.append(_chunk(b'smpl', head + struct.pack('<6I', 0, 0, start, end - 1, 0, 0)))
    payload = b'WAVE' + b''.join(chunks)
    return b'RIFF' + struct.pack('<I', len(payload)) + payload


def write(path, data, rate=48000, bits=24, dither=True, seed=0, loop=None):
    """encode() into a file."""
    blob = encode(data, rate=rate, bits=bits, dither=dither, seed=seed, loop=loop)
    with open(path, 'wb') as f:
        f.write(blob)
