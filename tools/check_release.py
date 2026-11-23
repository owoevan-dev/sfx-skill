"""What a published release depends on outside this repository is still there.

Usage: python tools/check_release.py                    every pack of the release answers with its size, the index is
                                                        the one this version names, the dataset's record answers
       python tools/check_release.py --full DATA LIB    also downloads the packs, imports them into a new library and
                                                        renders the recorded example from it (about 7.4 GB of disk)

A version that names no release yet has nothing to check."""
import argparse
import hashlib
import json
import sys
import tempfile
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'skills' / 'sound-effects' / 'scripts'))
from sfxlib import fsd50k, project, render  # noqa: E402

DATASET = 'https://zenodo.org/records/4060432'
EXAMPLE = REPO / 'skills' / 'sound-effects' / 'examples' / 'hollow-road-recorded.json'


def size_of(url):
    with urllib.request.urlopen(urllib.request.Request(url, method='HEAD'), timeout=60) as reply:
        return int(reply.headers.get('Content-Length') or -1)


def check_links():
    base = fsd50k.RELEASE_URL.rstrip('/')
    with urllib.request.urlopen(f'{base}/{fsd50k.PACK_INDEX}', timeout=120) as reply:
        blob = reply.read()
    if hashlib.sha256(blob).hexdigest() != fsd50k.RELEASE_INDEX_SHA256:
        raise SystemExit(f'{fsd50k.PACK_INDEX} on the release is not the one this version names')
    parts = json.loads(blob)['parts']
    for part in parts:
        got = size_of(f"{base}/{part['name']}")
        if got != int(part['bytes']):
            raise SystemExit(f"{part['name']}: {got} bytes on the release, {part['bytes']} in the index")
    size_of(DATASET)
    print(f'{len(parts)} packs on the release answer with their sizes; the index is the one named; the dataset record answers')


def check_install(data, library):
    fsd50k.download_packs(data)
    added = fsd50k.add_packs(data, library, log=lambda *a: None)
    print(f"imported {added['files']} recordings into a new library")
    with tempfile.TemporaryDirectory() as scratch:
        report = render.render(project.load(EXAMPLE), Path(scratch) / 'render', samples=library)
    print(f"{EXAMPLE.name}: {report['files']} files, {report['flags']} flags")
    if report['flags']:
        raise SystemExit('the recorded example has flags when rendered from the release')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--full', nargs=2, metavar=('DATA', 'LIBRARY'))
    args = ap.parse_args()
    if not fsd50k.RELEASE_URL:
        if args.full:
            raise SystemExit('this version names no release (RELEASE_URL), so the packs a release must carry cannot be checked')
        print('this version names no release yet: nothing to check')
        return 0
    check_links()
    if args.full:
        check_install(*args.full)
    return 0


if __name__ == '__main__':
    sys.exit(main())
