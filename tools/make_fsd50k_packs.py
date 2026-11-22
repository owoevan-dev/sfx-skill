"""Build the packs of FSD50K's CC0 recordings that a release publishes, from a sample library that imported both sets.

    python tools/make_fsd50k_packs.py LIBRARY OUT_FOLDER [--part-bytes N]

Takes every recording of the packs fsd50k-eval and fsd50k-dev that is CC0 and passes all of the skill's screens (so
none whose author's description asks for something), and those the project's examples play, and writes, into a new
folder:

- fsd50k-cc0-NN.zip: the recordings as they came, unchanged, in parts small enough for a release asset (2 GiB);
- fsd50k-cc0-index.json: their library entries and each part's size and SHA-256 (what `sfx.py samples` reads);
- CREDITS.csv: each recording's author, title and Freesound page;
- NOTICE.txt: where the recordings and the dataset come from, their licences and what was changed;
- SHA256SUMS.txt: the sums of all of the above.

Nothing is uploaded here, and nothing in the library is changed."""
import argparse
import csv
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'skills' / 'sound-effects' / 'scripts'))
from sfxlib import fsd50k, samples  # noqa: E402

PACKS = ('fsd50k-eval', 'fsd50k-dev')
PART_BYTES = 1_900_000_000          # of audio per part, under a release asset's 2 GiB even if nothing compresses
DATE = (2026, 1, 1, 0, 0, 0)        # the same bytes each time the packs are built
ENTRY_BYTES = 110                   # a zip entry's two headers, without its name (counted twice)
PART_END_BYTES = 200                # a part's end records
MAX_PARTS = 999
NEVER = {'author note', 'adult content', 'speech or singing'}      # screens that keep a recording out whatever else
NOTICE = """FSD50K recordings, packed for sfx-skill

This is a selection from the FSD50K dataset, made by the sfx-skill project. The dataset's authors have no part in this
project and do not endorse it.

The recordings. Each audio file is a recording from FSD50K whose author dedicated it to the public domain under CC0 1.0
on Freesound (https://creativecommons.org/publicdomain/zero/1.0/). No credit is required. CREDITS.csv names each
recording's author, title and Freesound page all the same: the people who recorded these sounds gave them away. If you
recorded one of them and would rather it were not included, tell us and it will be left out of the next release:
https://github.com/owoevan-dev/sfx-skill/issues/new?template=remove_recording.yml

The dataset. FSD50K, by Eduardo Fonseca, Xavier Favory, Jordi Pons, Frederic Font and Xavier Serra, created at the Music
Technology Group of Universitat Pompeu Fabra: https://doi.org/10.5281/zenodo.4060432. If you use it, or part of it,
please cite the authors' paper: E. Fonseca, X. Favory, J. Pons, F. Font and X. Serra, "FSD50K: An Open Dataset of
Human-Labeled Sound Events", IEEE/ACM Transactions on Audio, Speech, and Language Processing, vol. 30, pp. 829-852, 2022.
The dataset as a whole (its selection, labels and metadata) is licensed under CC BY 4.0
(https://creativecommons.org/licenses/by/4.0/). No warranties are given.

What was changed: a subset was selected (the CC0 recordings that pass this project's own quality screens, and a few more
that its examples play; none whose author's description asks for something, and no adult content, speech or singing), and the dataset's metadata for them was
reorganised into fsd50k-cc0-index.json with this project's measurements added. The audio files themselves are unchanged.
"""


def example_files(folder=REPO / 'skills' / 'sound-effects' / 'examples'):
    """(pack, file) of every recording the project's examples play."""
    found = set()

    def walk(value):
        if isinstance(value, dict):
            if value.get('type') == 'sample':
                takes = value.get('file')
                for take in takes if isinstance(takes, list) else [takes]:
                    found.add((value.get('library'), take['file'] if isinstance(take, dict) else take))
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)
    for path in sorted(Path(folder).glob('*.json')):
        walk(json.loads(path.read_text(encoding='utf-8')))
    return found


def chosen(index, also=()):
    """The library entries that go into the packs, in a fixed order: the CC0 recordings that pass every screen, and
    those the examples play (`also`) whatever else their screens say, unless their author's description asks for
    something or they are adult content, speech or singing."""
    named = set(also)
    rows = [r for r in index['files'] if r['library'] in PACKS and r.get('licence') == 'CC0-1.0'
            and isinstance(r.get('screen'), list)
            and (not r['screen'] or ((r['library'], r['file']) in named and not NEVER & set(r['screen'])))]
    return sorted(rows, key=lambda r: (PACKS.index(r['library']), int(r['file'])))


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def build(library, out, part_bytes=PART_BYTES, log=print, also=None):
    """Write the packs into the new folder `out`; returns their index. `also`: recordings to include beside those that
    pass every screen (by default those the project's examples play)."""
    library, out = Path(library), Path(out)
    out.mkdir(parents=True, exist_ok=False)
    index = samples.load_index(library)
    absent = [p for p in PACKS if p not in index['packs']]
    if absent:
        raise SystemExit(f"this library has not imported {', '.join(absent)}: import both FSD50K sets first")
    rows = chosen(index, example_files() if also is None else also)
    if not rows:
        raise SystemExit('no recordings to pack: import the FSD50K sets into this library first')
    parts, current, size = [], None, 0

    def finish():
        nonlocal current
        if current is not None:
            current.close()
            path = out / parts[-1]['name']
            parts[-1]['bytes'] = path.stat().st_size
            parts[-1]['sha256'] = fsd50k.sha256_of(path)
            log(f"{parts[-1]['name']}: {parts[-1]['files']} recordings, {parts[-1]['bytes'] / 1e9:.2f} GB")
            current = None

    for row in rows:
        blob = (library / row['path']).read_bytes()
        if _sha256(blob) != row['sha256']:
            raise SystemExit(f"{row['path']} has changed since it was imported")
        name = f"{row['library']}/{row['file']}.wav"
        cost = len(blob) + ENTRY_BYTES + 2 * len(name)
        if cost + PART_END_BYTES > part_bytes:
            raise SystemExit(f'{name} is larger than a part ({part_bytes} bytes)')
        if current is None or size + cost + PART_END_BYTES > part_bytes:
            finish()
            if len(parts) >= MAX_PARTS:
                raise SystemExit(f'more than {MAX_PARTS} parts: use a larger --part-bytes')
            parts.append({'name': f'fsd50k-cc0-{len(parts) + 1:02d}.zip', 'files': 0})
            current = zipfile.ZipFile(out / parts[-1]['name'], 'w', zipfile.ZIP_DEFLATED, compresslevel=6)
            size = 0
        info = zipfile.ZipInfo(name, date_time=DATE)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.create_system = 3                     # the same bytes whichever system builds them
        info.external_attr = 0o100644 << 16
        current.writestr(info, blob)
        parts[-1]['files'] += 1
        size += cost
    finish()
    packs = {}
    for pack_id in PACKS:
        mine = [r for r in rows if r['library'] == pack_id]
        if mine:
            packs[pack_id] = {'title': index['packs'][pack_id]['title'], 'author': index['packs'][pack_id]['author'],
                              'licence': 'CC0-1.0', 'page': fsd50k.PAGE, 'status': fsd50k.PACK_STATUS,
                              'files': len(mine), 'passing': sum(1 for r in mine if not r.get('screen'))}
    result = {'schema': fsd50k.PACK_SCHEMA, 'packs': packs, 'parts': parts, 'files': rows}
    (out / fsd50k.PACK_INDEX).write_text(json.dumps(result, ensure_ascii=False, indent=0) + '\n', encoding='utf-8',
                                         newline='\n')
    text = io.StringIO(newline='')
    writer = csv.writer(text, lineterminator='\n')
    writer.writerow(['pack', 'file', 'title', 'author', 'licence', 'freesound_page'])
    for r in rows:
        writer.writerow([r['library'], r['file'], r.get('title', ''), r.get('author', ''), r['licence'], r.get('page', '')])
    (out / 'CREDITS.csv').write_bytes(text.getvalue().encode('utf-8'))
    (out / 'NOTICE.txt').write_text(NOTICE, encoding='utf-8', newline='\n')
    names = [p['name'] for p in parts] + [fsd50k.PACK_INDEX, 'CREDITS.csv', 'NOTICE.txt']
    sums = ''.join(f'{fsd50k.sha256_of(out / n)}  {n}\n' for n in names)
    (out / 'SHA256SUMS.txt').write_text(sums, encoding='utf-8', newline='\n')
    log(f'{len(rows)} recordings in {len(parts)} parts; the index\'s SHA-256 is {fsd50k.sha256_of(out / fsd50k.PACK_INDEX)}')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('library', help='a sample library holding the fsd50k-eval and fsd50k-dev packs')
    parser.add_argument('out', help='a new folder for the packs')
    parser.add_argument('--part-bytes', type=int, default=PART_BYTES, help='audio bytes per part')
    args = parser.parse_args()
    build(args.library, args.out, args.part_bytes)


if __name__ == '__main__':
    main()
