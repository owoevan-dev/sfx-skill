"""Exports: a render's masters as the files a game engine takes, with a manifest of what each file is and how the
engine should treat it.

Every file is 48 kHz with the render's channels. WAV and FLAC files are 16-bit with triangular-pdf dither
(wav.encode); the generic target also keeps the 24-bit masters. Ogg Vorbis and MP3 are encoded from the 24-bit masters
by the FFmpeg that music-production's configuration names (Vorbis at quality VORBIS_QUALITY, MP3 at LAME's VBR
quality MP3_QUALITY: this skill's choices), and every encoded file is decoded back and measured again: its length,
how far its sound moved in time, its true peak and loudness, and for a loop how its end meets its start. A new folder
holds `manifest.json`, `files.json` and `sfx/` with the files (names as in the render, `<sound>_<nn>.<ext>`).

Targets (the engines' documentation, read for this; see SOURCES):
- godot: WAV by default ("WAV for short and repetitive sound effects, Ogg Vorbis for music, speech and long sound
  effects"). A loop's WAV carries a `smpl` loop, which Godot follows by default ("Detect from WAV"). Godot 4.7's
  player plays frames loop_begin + 1 to loop_end and then returns (scene/resources/audio_stream_wav.cpp), so a loop of
  n frames is written with its first frame repeated after its end and loop_end = n: every pass then plays the period
  exactly once. Ogg and MP3 carry no loop points; their loop is switched on in Godot's Import dock. Each one-shot with
  variants gets an AudioStreamRandomizer resource (`<sound>.tres`): random without repeats, no pitch or volume
  randomization of the engine's own.
- unity: WAV; the manifest suggests import settings read from Unity's manual (PCM for short sound effects, ADPCM for
  noisy sounds played in large numbers such as impacts, Vorbis for medium-length sounds; Decompress On Load for small
  sounds, Streaming for continuous audio) and an Audio Random Container for variants. Where the manual gives no length,
  this skill's lines apply: ADPCM up to NOISY_HIT_S (a hit, not a long creak with quiet detail), Streaming for loops from
  LONG_LOOP_S (several sources can share a short loop in memory). No .meta files: Unity makes them.
- unreal: 16-bit WAV (Unreal converts every import to 16 bits and does not dither 24-bit files, so it recommends
  16-bit files); loops use the Wave Player's Looping flag, variants a Sound Cue Random node without replacement.
- roblox: WAV by default (FLAC, Ogg and MP3 on request); files must stay under 20 MB, 7 minutes and 48 kHz, and each
  file is one asset against the import allowance (100 per 30 days unverified, 2000 when ID verified).
- web: WAV by default; Ogg and MP3 on request, with their decoded timing measured (MDN gives Vorbis and MP3 a latency
  of at least 100 ms, and Safari plays Ogg Vorbis only from 18.4); loops play through an AudioBufferSourceNode with `loop` set.
- generic: 16-bit and 24-bit WAV, nothing engine-specific.

Exports hold audio files, data and an engine's own resource types only; nothing chooses or makes sounds at run time."""
import hashlib
import json
import math
import shutil
import subprocess
from pathlib import Path

import numpy as np

from . import RATE, VERSION, check, dsp, licences, loudness, mpbridge, render, wav
from . import record as render_record

TARGETS = ('godot', 'unity', 'unreal', 'roblox', 'web', 'generic')
FORMATS = ('wav', 'flac', 'ogg', 'mp3')
ALLOWED = {'godot': ('wav', 'ogg', 'mp3'), 'unity': ('wav', 'flac', 'ogg', 'mp3'),
           'unreal': ('wav', 'flac', 'ogg', 'mp3'), 'roblox': ('wav', 'flac', 'ogg', 'mp3'),
           'web': ('wav', 'ogg', 'mp3'), 'generic': ('wav', 'flac', 'ogg', 'mp3')}
LOSSY = ('ogg', 'mp3')
ENCODERS = {'flac': ('flac', 'FLAC'), 'ogg': ('libvorbis', 'Ogg'), 'mp3': ('libmp3lame', 'MP3')}
VORBIS_QUALITY = 5          # libvorbis -q:a 5 (this skill's choice)
MP3_QUALITY = 2             # LAME VBR -V 2 (this skill's choice)
LONG_ONE_SHOT_S = 5.0       # this skill's line between Unity's "short" and "medium length" sounds
NOISY_HIT_S = 2.0           # this skill's line: the longest sound still taken for a hit played in large numbers
LONG_LOOP_S = 15.0          # this skill's line: a shorter loop, or a mono one that sources in the world share, stays in memory
ROBLOX_MAX_BYTES = 20_000_000
ROBLOX_MAX_S = 7 * 60
ROBLOX_ALLOWANCE = {'unverified': 100, 'id_verified': 2000}
SHIFT_SEARCH_S = 0.1        # how far a decoded file's sound is searched for against the master, each way
PEAK_TOLERANCE_DB = check.PEAK_TOLERANCE_DB
GODOT_TYPES = {'wav': 'AudioStreamWAV', 'ogg': 'AudioStreamOggVorbis', 'mp3': 'AudioStreamMP3'}

SOURCES = {
    'godot_import': 'Godot Engine documentation (stable): Importing audio samples',
    'godot_randomizer': 'Godot Engine class reference (stable): AudioStreamRandomizer',
    'godot_loop_code': 'Godot 4.7.2 source, scene/resources/audio_stream_wav.cpp (loop playback and smpl import)',
    'unity_clip': 'Unity 6.6 Manual: Audio Clip Import Settings reference',
    'unity_formats': 'Unity 6.6 Manual: Audio file compatibility',
    'unity_random': 'Unity 6.6 Manual: Audio playlist randomization (Audio Random Container)',
    'unreal_import': 'Unreal Engine 5.9 Documentation: Importing Audio Files',
    'unreal_cue': 'Unreal Engine 5.9 Documentation: Sound Cue Reference (Random node, Wave Player Looping)',
    'roblox_assets': 'Roblox Creator Hub: Audio assets',
    'mdn_codecs': 'MDN Web Docs: Web audio codec guide',
    'mdn_buffer_source': 'MDN Web Docs: AudioBufferSourceNode.loop',
}


class ExportError(Exception):
    """The export cannot start or finish."""


def _write_json(path, data):
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + '\n', encoding='utf-8', newline='\n')


def _run(ffmpeg, args, data=None, timeout=None):
    try:
        proc = subprocess.run([str(ffmpeg), '-hide_banner', '-loglevel', 'error', *args], input=data,
                              capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ExportError(f"FFmpeg did not finish in {timeout} s ({' '.join(args[:6])} ...)") from None
    if proc.returncode != 0:
        raise ExportError(f"FFmpeg failed ({' '.join(args[:6])} ...): {proc.stderr.decode('utf-8', 'replace')[:400]}")
    return proc.stdout


def encode(ffmpeg, fmt, source, target):
    """Encode `source` (a WAV path, or WAV bytes) into `target` with FFmpeg (never overwriting a file)."""
    codec = {'flac': ['-c:a', 'flac'], 'ogg': ['-c:a', 'libvorbis', '-q:a', str(VORBIS_QUALITY)],
             'mp3': ['-c:a', 'libmp3lame', '-q:a', str(MP3_QUALITY)]}[fmt]
    if isinstance(source, (bytes, bytearray)):
        _run(ffmpeg, ['-f', 'wav', '-i', 'pipe:0', *codec, '-n', str(target)], data=bytes(source))
    else:
        _run(ffmpeg, ['-i', str(source), *codec, '-n', str(target)])


def decode(ffmpeg, path, channels):
    """A file decoded by FFmpeg to float frames (n x channels) at 48 kHz."""
    raw = _run(ffmpeg, ['-i', str(path), '-f', 'f32le', '-c:a', 'pcm_f32le', '-ac', str(channels), '-ar', str(RATE),
                        'pipe:1'])
    return np.frombuffer(raw, dtype='<f4').astype(np.float64).reshape(-1, channels)


def time_shift_ms(master, decoded):
    """How far the decoded sound sits after the master's (ms; negative: earlier), by the peak of their
    cross-correlation within SHIFT_SEARCH_S each way, over the first two seconds."""
    a = np.asarray(master, dtype=np.float64).mean(axis=1)
    b = np.asarray(decoded, dtype=np.float64).mean(axis=1)
    n = min(len(a), len(b), 2 * RATE)
    if n < 2:
        return 0.0
    a, b = a[:n], b[:n]
    search = int(SHIFT_SEARCH_S * RATE)
    size = 1 << int(math.ceil(math.log2(2 * n + 2 * search)))
    corr = np.fft.irfft(np.conj(np.fft.rfft(a, size)) * np.fft.rfft(b, size), size)
    lags = np.concatenate([np.arange(0, search + 1), np.arange(-search, 0)])
    values = np.concatenate([corr[:search + 1], corr[size - search:]])
    return 1000.0 * int(lags[int(np.argmax(values))]) / RATE


def godot_loop_frames(x):
    """A loop's period for Godot: its first frame repeated after its end, and the smpl loop (0, n + 1) that stores
    loop_end = n (Godot plays loop_begin + 1 .. loop_end, then returns)."""
    x = np.asarray(x, dtype=np.float64)
    return np.concatenate([x, x[:1]]), (0, len(x) + 1)


def godot_loop_wav(x, seed):
    """The bytes of a loop's 16-bit WAV for Godot: the period dithered and quantized first, then its first frame as
    written repeated after the end (godot_loop_frames), so the two copies are the same bit for bit."""
    q = wav.quantize(x, 16, dither=True, seed=seed)
    frames, smpl = godot_loop_frames(q)
    return wav.encode(frames, rate=RATE, bits=16, dither=False, loop=smpl)


def godot_playback(n_frames, loop_begin, loop_end, count):
    """The frame indices Godot 4.7's WAV player reads for `count` frames of a forward loop: a transcription of the
    position logic of AudioStreamPlaybackWAV::_mix_internal (audio_stream_wav.cpp) at the stream's own rate."""
    offset, out = 0, []
    while len(out) < count:
        if offset >= loop_end:
            offset = loop_begin + (offset - loop_end)
        target = min(loop_end - offset + 1, count - len(out))
        out.extend(range(offset, offset + target))
        offset += target
    return out


def godot_randomizer(stem, names, res_path, fmt):
    """The text of an AudioStreamRandomizer resource (Godot 4 text format) over the files `names` in res_path."""
    kind = GODOT_TYPES[fmt]
    lines = ['[gd_resource type="AudioStreamRandomizer" format=3]', '']
    ids = []
    for i, name in enumerate(names, 1):
        rid = f'{i}_{Path(name).stem}'
        ids.append(rid)
        lines.append(f'[ext_resource type="{kind}" path="{res_path}/{name}" id="{rid}"]')
    lines += ['', '[resource]', 'playback_mode = 0', 'random_pitch = 1.0', 'random_volume_offset_db = 0.0',
              f'streams_count = {len(names)}']
    for i, rid in enumerate(ids):
        lines += [f'stream_{i}/stream = ExtResource("{rid}")', f'stream_{i}/weight = 1.0']
    return '\n'.join(lines) + '\n'


def _unity(sound, seconds, variants):
    if sound.get('use') == 'loop' and seconds >= LONG_LOOP_S and sound.get('channels', 2) == 2:
        out = {'compression_format': 'Vorbis', 'load_type': 'Streaming', 'loop': 'AudioSource.loop = true',
               'why': 'a long loop is continuous audio, which the manual gives to Streaming; Vorbis suits longer sounds. '
                      'If several sources play it at once, Compressed In Memory saves a stream for each'}
    elif sound.get('use') == 'loop':
        out = {'compression_format': 'Vorbis', 'load_type': 'Compressed In Memory', 'loop': 'AudioSource.loop = true',
               'why': 'a short loop stays in memory, where several sources can play it without a stream each'}
    elif sound['category'] in ('impact', 'foley') and seconds <= NOISY_HIT_S:
        out = {'compression_format': 'ADPCM', 'load_type': 'Decompress On Load',
               'why': 'the manual gives ADPCM to noisy sounds played in large numbers, naming impacts'}
    elif seconds > LONG_ONE_SHOT_S:
        out = {'compression_format': 'Vorbis', 'load_type': 'Compressed In Memory',
               'why': 'the manual gives Vorbis to medium-length sounds and Compressed In Memory to larger files'}
    else:
        out = {'compression_format': 'PCM', 'load_type': 'Decompress On Load',
               'why': 'the manual gives PCM to short sound effects'}
    out['sample_rate_setting'] = 'Preserve Sample Rate'
    out['force_to_mono'] = False
    if variants > 1 and sound.get('use') != 'loop':
        out['variants'] = 'an Audio Random Container with these clips'
    out['sources'] = ['unity_clip', 'unity_random']
    return out


def _engine(target, sound, seconds, files, extra):
    loop = sound.get('use') == 'loop'
    variants = len(sound['variants'])
    if target == 'godot':
        out = {'sources': ['godot_import', 'godot_randomizer']}
        if extra.get('tres'):
            out['randomizer'] = extra['tres']
        if loop:
            out['loop'] = ('WAV: the file carries its loop and Godot follows it by default (Loop Mode: Detect from '
                           'WAV); Ogg and MP3: switch Loop on in the Import dock')
            out['sources'].append('godot_loop_code')
        return out
    if target == 'unity':
        return _unity(sound, seconds, variants)
    if target == 'unreal':
        out = {'import': '16-bit WAV, as Unreal recommends', 'sources': ['unreal_import', 'unreal_cue']}
        if loop:
            out['loop'] = 'the Looping flag of the Sound Wave or Wave Player (not a Looping node)'
        elif variants > 1:
            out['variants'] = 'a Sound Cue Random node with Randomize Without Replacement'
        return out
    if target == 'roblox':
        out = {'assets': variants, 'sources': ['roblox_assets']}    # one upload per variant, in one format
        if loop:
            out['loop'] = 'Sound.Looped = true'
        return out
    if target == 'web':
        out = {'play': 'decodeAudioData, then an AudioBufferSourceNode per play', 'sources': ['mdn_buffer_source',
                                                                                              'mdn_codecs']}
        if loop:
            out['loop'] = 'AudioBufferSourceNode.loop = true'
        return out
    return {}


def _measure(entry, y, x, loop, godot_extra_frame=False):
    """Fill `entry` with the measurements of the exported audio y against its master x."""
    period = y[:len(x)] if godot_extra_frame else y
    entry['frames'] = int(len(y))
    entry['frames_added'] = int(len(y) - len(x) - (1 if godot_extra_frame else 0))
    if loop:
        entry['true_peak_dbtp'] = loudness.loop_true_peak_db(period)
        entry['loudness_lufs'] = loudness.loop_integrated(period)
        entry['loudness_measure'] = 'integrated'
        seam = check.loop_seam(period)
        entry['seam_step'] = seam['step']
        entry['seam_largest_inner_step'] = seam['largest_inner_step']
    else:
        entry['true_peak_dbtp'] = loudness.true_peak_db(period)
        entry['loudness_lufs'] = loudness.brief(period)
        entry['loudness_measure'] = 'brief'


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def export(render_dir, out_dir, target, formats=None, ffmpeg=None, res_path='res://sfx'):
    """Export the render in render_dir for `target` into the new folder out_dir; returns the manifest."""
    folder, out = Path(render_dir), Path(out_dir)
    if target not in TARGETS:
        raise ExportError(f"unknown target {target!r}; use one of {', '.join(TARGETS)}")
    formats = tuple(dict.fromkeys(formats or ('wav',)))
    wrong = [f for f in formats if f not in ALLOWED[target]]
    if wrong:
        raise ExportError(f"{target} takes {', '.join(ALLOWED[target])}, not {', '.join(wrong)}")
    if any(f != 'wav' for f in formats) and ffmpeg is None:
        raise ExportError('formats other than WAV are encoded by FFmpeg: give the music-production configuration '
                          'with --config')
    wanted = [f for f in formats if f in ENCODERS]
    if wanted:
        have = mpbridge.ffmpeg_features(ffmpeg)['encoders']
        for f in wanted:
            encoder, name = ENCODERS[f]
            if encoder not in have:
                raise ExportError(f"this FFmpeg ({ffmpeg}) has no {encoder} encoder, so it cannot write {name}: leave {f} "
                                  "out of --formats, or give an FFmpeg that has it (on macOS, Homebrew's ffmpeg-full)")
    res_path = res_path.rstrip('/')
    if target == 'godot' and (not res_path.startswith('res://') or '"' in res_path):
        raise ExportError(f'--res-path must be a Godot path starting with res://, not {res_path!r}')
    if not (folder / 'resolved.json').is_file():
        raise ExportError(f'{folder} is not a render folder (no resolved.json)')
    if out.exists() and any(out.iterdir()):
        raise ExportError(f'{out} already holds files; an export goes into a new folder')
    resolved = render_record.load(folder)
    ceiling = resolved['level']['ceiling_dbtp']
    sfx = out / 'sfx'
    sfx.mkdir(parents=True, exist_ok=True)
    sounds, flags = [], []

    def flag(kind, sound, file, message):
        flags.append({'kind': kind, 'sound': sound, 'file': file, 'message': message})

    for s in resolved['sounds']:
        loop = s.get('use') == 'loop'
        files, lengths = [], []
        for v in s['variants']:
            data = render_record.master(folder, v['file'])
            x = data.astype(np.float64)
            x = x[:, None] if x.ndim == 1 else x
            lengths.append(len(x))
            stem = Path(v['file']).stem
            seed = dsp.seed_for(resolved['seed'], stem, 'export', target)
            for fmt in formats:
                depths = (16, 24) if (target == 'generic' and fmt == 'wav') else ((16,) if fmt in ('wav', 'flac')
                                                                                 else (None,))
                for bits in depths:
                    sub = sfx / f'{bits}bit' if (target == 'generic' and fmt == 'wav') else sfx
                    sub.mkdir(parents=True, exist_ok=True)
                    path = sub / f'{stem}.{fmt}'
                    godot_loop = target == 'godot' and fmt == 'wav' and loop
                    entry = {'path': path.relative_to(out).as_posix(), 'variant': v['n'], 'format': fmt,
                             'bits': bits, 'channels': x.shape[1]}
                    if v.get('recordings'):
                        entry['recordings'] = v['recordings']
                    if fmt == 'wav' and bits == 24:
                        shutil.copyfile(folder / v['file'], path)
                        y = x
                        entry['loop'] = {'start': 0, 'end': len(x), 'smpl_end': len(x) - 1} if loop else None
                    elif fmt == 'wav':
                        if godot_loop:
                            path.write_bytes(godot_loop_wav(x, seed))
                        else:
                            wav.write(path, x, rate=RATE, bits=16, dither=True, seed=seed,
                                      loop=(0, len(x)) if loop else None)
                        y = wav.read(path)[0].astype(np.float64).reshape(-1, x.shape[1])
                        if godot_loop:
                            entry['loop'] = {'start': 0, 'end': len(x), 'smpl_end': len(x),
                                             'note': 'the first frame is repeated after the end for Godot'}
                        else:
                            entry['loop'] = {'start': 0, 'end': len(x), 'smpl_end': len(x) - 1} if loop else None
                    else:
                        if fmt == 'flac':
                            encode(ffmpeg, fmt, wav.encode(x, rate=RATE, bits=16, dither=True, seed=seed), path)
                        else:
                            encode(ffmpeg, fmt, folder / v['file'], path)
                        y = decode(ffmpeg, path, x.shape[1])
                        entry['time_shift_ms'] = time_shift_ms(x, y) if fmt in LOSSY else 0.0
                        entry['loop'] = {'start': 0, 'end': len(x), 'note': 'set the loop in the engine'} if loop \
                            else None
                    _measure(entry, y, x, loop, godot_extra_frame=godot_loop)
                    entry['bytes'] = path.stat().st_size
                    files.append(entry)
                    name = entry['path']
                    if entry['true_peak_dbtp'] > ceiling + PEAK_TOLERANCE_DB:
                        flag('true_peak', s['id'], name, f"true peak {entry['true_peak_dbtp']:.2f} dBTP after "
                             f"encoding, over the ceiling {ceiling:.1f}")
                    if loop and entry['frames_added']:
                        flag('loop_length', s['id'], name, f"decoded {entry['frames_added']:+d} frames longer than "
                             'the master: the loop will not join; use WAV (or Ogg) for loops')
                    elif loop and entry['seam_step'] > check.SEAM_FLAG_RATIO * entry['seam_largest_inner_step']:
                        flag('seam', s['id'], name, 'after encoding the loop jumps where its end meets its start')
                    if fmt in LOSSY and abs(entry.get('time_shift_ms', 0.0)) >= 1.0:
                        flag('time_shift', s['id'], name, f"the decoded sound starts {entry['time_shift_ms']:.1f} ms "
                             'later than the master (codec delay): it lags its trigger by that much')
                    if target == 'roblox' and entry['bytes'] >= ROBLOX_MAX_BYTES:
                        flag('roblox_size', s['id'], name, 'Roblox takes audio files under 20 MB')
                    if target == 'roblox' and len(x) / RATE >= ROBLOX_MAX_S:
                        flag('roblox_length', s['id'], name, 'Roblox takes audio under 7 minutes')
        extra = {}
        one_shot_variants = [f for f in files if f['format'] == formats[0] and f['bits'] in (16, None)]
        if target == 'godot' and not loop and len(s['variants']) > 1:
            tres = sfx / f"{s['id']}.tres"
            tres.write_text(godot_randomizer(s['id'], [Path(f['path']).name for f in one_shot_variants], res_path,
                                             formats[0]), encoding='utf-8', newline='\n')
            extra['tres'] = tres.relative_to(out).as_posix()
        seconds = max(lengths) / RATE
        record = {'id': s['id'], 'category': s['category'], 'use': s.get('use', 'one_shot'), 'channels': s['channels'],
                  'trim_db': s['trim_db'], 'notes': s.get('notes', ''), 'files': files,
                  'engine': _engine(target, s, seconds, files, extra)}
        if s.get('loop'):
            record['loop_seconds'] = s['loop']['seconds']
        sounds.append(record)
    if target == 'web' and 'ogg' in formats:
        flag('web_ogg', None, None, 'Safari plays Ogg Vorbis only from version 18.4 (macOS 15.4, iOS 18.4): keep a WAV or MP3 copy for older devices')
    total = sum(len(s['files']) for s in sounds)
    cited = sorted({src for s in sounds for src in s['engine'].get('sources', [])})
    manifest = {'schema': 'sound-effects-export/1', 'made_by': f'sound-effects {VERSION}', 'target': target,
                'render': folder.name, 'title': resolved['title'], 'rate': RATE, 'formats': list(formats),
                'level': resolved['level'], 'files': total, 'sounds': sounds, 'flags': flags,
                'sources': {k: SOURCES[k] for k in cited}}
    if resolved.get('recordings'):
        manifest['recordings'] = resolved['recordings']
        manifest['recordings_note'] = licences.note(resolved['recordings'], resolved['sounds'])
    if target == 'godot':
        manifest['res_path'] = res_path
    if target == 'roblox':
        uploads = sum(len(s['variants']) for s in resolved['sounds'])
        manifest['roblox_assets'] = {'uploads': uploads, 'allowance_per_30_days': dict(ROBLOX_ALLOWANCE)}
        if uploads > ROBLOX_ALLOWANCE['unverified']:
            flags.append({'kind': 'roblox_allowance', 'sound': None, 'file': None,
                          'message': f"{uploads} uploads (one per variant) are more than the "
                                     f"{ROBLOX_ALLOWANCE['unverified']} assets per 30 days an unverified account may "
                                     f"import ({ROBLOX_ALLOWANCE['id_verified']} when ID verified)"})
    _write_json(out / 'manifest.json', manifest)
    _write_json(out / 'files.json', render.files_record(out))
    return manifest
