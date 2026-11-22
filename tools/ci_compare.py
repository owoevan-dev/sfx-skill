"""Compare renders of one project made on different systems: the same files, sample for sample within a tolerance. Floating
point differs a little between CPUs and NumPy builds; a sound that changes is far above that.

Usage: python tools/ci_compare.py RENDER_FOLDER RENDER_FOLDER [...]"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills' / 'sound-effects' / 'scripts'))
from sfxlib import wav  # noqa: E402

LIMIT_DB = -90.0                 # the largest difference between two renders of a file (this project's choice)


def masters(folder):
    return {p.relative_to(folder).as_posix(): p for p in sorted(Path(folder).rglob('*.wav'))}


def main():
    folders = sys.argv[1:]
    if len(folders) < 2:
        raise SystemExit(__doc__)
    base = masters(folders[0])
    if not base:
        raise SystemExit(f'no WAV files in {folders[0]}')
    worst, bad = -np.inf, []
    for other in folders[1:]:
        files = masters(other)
        if set(files) != set(base):
            bad.append(f'{other}: holds other files ({sorted(set(files) ^ set(base))[:5]})')
            continue
        for name, path in base.items():
            a, _ = wav.read(path)
            b, _ = wav.read(files[name])
            if a.shape != b.shape:
                bad.append(f'{other}/{name}: {a.shape} against {b.shape}')
                continue
            peak = float(np.max(np.abs(a.astype(np.float64) - b.astype(np.float64))))
            db = 20 * np.log10(peak) if peak > 0 else -np.inf
            worst = max(worst, db)
            if db > LIMIT_DB:
                bad.append(f'{other}/{name}: differs by {db:.1f} dBFS')
    print(f'{len(base)} files in {len(folders)} renders; the largest difference is {worst:.1f} dBFS (limit {LIMIT_DB:g})')
    for line in bad:
        print(line)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
