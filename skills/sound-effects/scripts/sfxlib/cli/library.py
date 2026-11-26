"""The samples command: the sample library, its search, time maps, strikes and imports."""
import json
import sys
import textwrap
from pathlib import Path

from .. import denoise, fsd50k, licences, project, samples, spectrogram
from .common import FIND_SHOWN, EVENTS_SHOWN, EVENT_LEAD_S, _ffmpeg, _library


def _samples_map(args):
    """The time map of one recording (denoise.time_map), with its quietest stretch and that stretch's octave bands."""
    ok, ff = _ffmpeg(args, 'samples')
    if not ok:
        return 1
    try:
        pack, _, name = args.map.partition('/')
        library = samples.Library(_library(args, must_exist=False), ff)
        _, entry = library.entry(pack, name)
        x, rate = samples.decode(library.path(pack, entry), ff)
    except (samples.SampleError, OSError) as error:
        print(f'samples: {error}')
        return 1
    rows = denoise.time_map(x, rate, args.hop, args.start or 0.0, args.end)
    events = denoise.events(x, rate)
    q0, q1, q_db = denoise.quietest(x, rate)
    out = {'recording': args.map, 'seconds': round(len(x) / rate, 3), 'rate': rate,
           'quietest': {'start': q0, 'end': q1, 'rms_db': q_db, 'bands': denoise.bands(x, rate, q0, q1)},
           'events': events, 'frames': rows}
    if args.json:
        print(json.dumps(out, indent=1))
        return 0
    print(f"{args.map}: {out['seconds']} s at {rate} Hz. Quietest 0.3 s: {q0}-{q1} s at {q_db} dBFS RMS; its octave "
          'bands (dBFS RMS): ' + ', '.join(f'{k} Hz {v}' for k, v in out['quietest']['bands'].items()))
    more = f', and {len(events) - EVENTS_SHOWN} more' if len(events) > EVENTS_SHOWN else ''
    print('Events: ' + (', '.join(f"{e['onset']:.2f} s ({e['db']:+.0f} dB)" for e in events[:EVENTS_SHOWN]) + more if events
                        else 'no clear strikes') + ' (level against the loudest moment; --events for their onsets and ends)')
    digits = 3 if args.hop < 0.01 else 2
    print('      t   rms dB  peak dB  centroid Hz  <150 Hz  pitch Hz (20-2000 Hz; - for none)')
    for r in rows:
        print(f"{r['t']:7.{digits}f} {r['rms_db']:8.1f} {r['peak_db']:8.1f} {r['centroid_hz']:12d} {r['low_share']:8.2f} "
              f"{(format(r['pitch_hz'], '.0f') if r['pitch_hz'] else '-'):>9}  {'#' * max(0, int((r['rms_db'] + 60) / 2))}")
    print('The noise stretch of a sample layer ("denoise": {"noise": [start, end]}) must hold only noise.')
    return 0


def _samples_spectrograms(args):
    """Draw recordings of the library (spectrogram.py) into --output, to look at before building on them."""
    ok, ff = _ffmpeg(args, 'samples')
    if not ok:
        return 1
    out = Path(args.output or '.')
    try:
        library = samples.Library(_library(args, must_exist=False), ff)
        for name in args.spectrogram:
            pack, _, file = name.partition('/')
            _, entry = library.entry(pack, file)
            x, rate = samples.decode(library.path(pack, entry), ff)
            out.mkdir(parents=True, exist_ok=True)
            png = out / f'{pack}_{file}.spectrogram.png'
            scale = spectrogram.write(x, png, rate)
            print(f"{png}: {scale['seconds']} s, {scale['column_ms']} ms a column, loudest at {scale['peak_s']} s")
    except (samples.SampleError, OSError) as error:
        print(f'samples: {error}')
        return 1
    print('Top: the level (the loudest millisecond marked white). Below: frequency on a log scale, 20 Hz at the bottom to '
          '20 kHz at the top, faint lines at 100 Hz, 1 kHz and 10 kHz; time to the right, a faint line every 0.1 s.')
    return 0


def _samples_events(args):
    """The strikes of one recording (denoise.events) and a take for each, ready to paste into a sample layer."""
    ok, ff = _ffmpeg(args, 'samples')
    if not ok:
        return 1
    try:
        pack, _, name = args.events.partition('/')
        library = samples.Library(_library(args, must_exist=False), ff)
        _, entry = library.entry(pack, name)
        x, rate = samples.decode(library.path(pack, entry), ff)
    except (samples.SampleError, OSError) as error:
        print(f'samples: {error}')
        return 1
    found = denoise.events(x, rate)
    if args.min_gap:
        found = denoise.spaced(found, args.min_gap)
    takes = [{'file': name, 'offset': round(max(0.0, e['onset'] - EVENT_LEAD_S), 3)} for e in found]
    if args.even:                       # each take brought to the level of the loudest strike
        loudest = max((e['db'] for e in found), default=0.0)
        for t, e in zip(takes, found):
            if round(loudest - e['db'], 1):
                t['gain_db'] = round(loudest - e['db'], 1)
    if args.json:
        print(json.dumps({'recording': args.events, 'seconds': round(len(x) / rate, 3), 'events': found,
                          'takes': takes[:project.MAX_TAKES]}, indent=1))
        return 0
    print(f"{args.events}: {len(x) / rate:.3f} s, {len(found)} event{'s' if len(found) != 1 else ''} "
          '(seconds; level against the loudest moment)')
    if not found:
        print('No clear strikes: a steady sound, or one that swells. --map shows its level over time.')
        return 0
    shown = (args.limit if args.limit is not None else EVENTS_SHOWN) or len(found)
    print('  onset    peak     end    level')
    for e in found[:shown]:
        print(f"{e['onset']:7.3f} {e['peak']:7.3f} {e['end']:7.3f} {e['db']:+6.1f} dB")
    if len(found) > shown:
        print(f'... the first {shown} of {len(found)} (--limit 0 lists all)')
    print(f'Takes starting just before each onset (a sample layer\'s "file", library {pack}):')
    paste = takes[:min(shown, project.MAX_TAKES)]
    print(json.dumps(paste))
    if len(paste) < len(takes):
        print(f'(the first {len(paste)} of {len(takes)}: a layer holds at most {project.MAX_TAKES} takes)')
    return 0


def cmd_samples(args):
    lib = _library(args, must_exist=False)
    if args.download_fsd50k:
        from_release = args.source == 'release' or (args.source is None and bool(fsd50k.RELEASE_URL))
        try:
            if from_release:
                got = fsd50k.download_packs(args.download_fsd50k)
                print(f"FSD50K packs: {len(got['parts'])} parts in {args.download_fsd50k}, checked against their "
                      'SHA-256 sums')
            else:
                fsd50k.download(args.download_fsd50k, args.set)
                print(f'FSD50K {args.set} set: all files in {args.download_fsd50k}, checked against their MD5 sums')
        except (fsd50k.FSD50KError, OSError) as error:
            print(f'samples: {error}')
            return 1
        if args.download_only:
            return 0
        args.add_fsd50k = args.download_fsd50k
    if args.add_fsd50k and args.source != 'zenodo' and (Path(args.add_fsd50k) / fsd50k.PACK_INDEX).is_file():
        try:
            r = fsd50k.add_packs(args.add_fsd50k, lib)
        except (fsd50k.FSD50KError, samples.SampleError, OSError) as error:
            print(f'samples: {error}')
            return 1
        print(f"FSD50K packs ({', '.join(r['packs'])}): {r['files']} recordings, {r['added']} of them new, in "
              f"{r['folder']}")
        return 0
    if args.add_fsd50k:
        try:
            r = fsd50k.add(args.add_fsd50k, lib, args.set, check_md5=not args.no_md5_check)
        except (fsd50k.FSD50KError, samples.SampleError, OSError) as error:
            print(f'samples: {error}')
            return 1
        print(f"{r['pack']}: {r['status']}, {r['files']} CC0 recordings ({r['passing']} pass every screen) in "
              f"{r['folder']}")
        return 0
    if args.add:
        ok, ff = _ffmpeg(args, 'samples')
        if not ok:
            return 1
        status = 0
        for source in args.add:
            try:
                r = samples.add(source, lib, licence=args.licence, pack=args.pack, title=args.title,
                                author=args.author, page=args.page, ffmpeg=ff,
                                confirm_licence=args.confirm_licence)
            except (samples.SampleError, OSError) as error:
                print(f'{Path(source).name}: not imported: {error}')
                status = 1
                continue
            asks = ', '.join(r['conditions']) or 'nothing (no credit needed)'
            print(f"{r['pack']}: {r['added']} recordings added ({r['files']} in the pack, {r['passing']} pass every "
                  f"screen), licence {r['licence']} ({r['licence_status']}); it asks: {asks}")
            if r['duplicates']:
                print(f"  {r['duplicates']} duplicates of recordings already in the library were left out")
            for rel, why in r['skipped']:
                print(f'  left out {rel}: {why}')
            if r['reasons']:
                print('  screens: ' + ', '.join(f'{k} {v}' for k, v in sorted(r['reasons'].items())))
        print(f'library: {lib}')
        return status
    if not args.map and not args.events and not args.spectrogram and not (lib / samples.INDEX).is_file():
        if args.json:                    # a caller reading JSON gets an empty library's; the person gets the setup on stderr
            print(json.dumps({} if args.labels else {'packs': {}, 'files': [], 'matched': 0, 'some_words': []}, indent=1))
            print(f'samples: no library at {lib}; sfx.py samples without --json says how to fill one', file=sys.stderr)
            return 0
        print('A sample library holds the recordings sample layers play. Fill it in either way:')
        print(f'  FSD50K ({fsd50k.PAGE}): {fsd50k.RELEASE_RECORDINGS:,} screened CC0 recordings in 190 labelled classes, from '
              "this project's release. Fetch and import (ask the person first: a 2.9 GB download, 4.3 GB once imported, about 7.2 GB until the "
              "archives are removed): sfx.py samples "
              f'--download-fsd50k DATA_FOLDER. With --source zenodo, a whole set of the dataset instead: --set eval '
              f'({fsd50k.CC0_COUNTS["eval"]:,} CC0 recordings, 6.3 GB) or --set dev ({fsd50k.CC0_COUNTS["dev"]:,}, 18.4 GB).')
        print('  Sounds the person has (their own recordings, a library they downloaded or bought, the game\'s sounds): '
              'sfx.py samples --add FOLDER_OR_ZIP [--licence own|CC0-1.0|...] [--author NAME] [--page URL]. Nothing '
              'is imported until the person names the licence: without --licence it prints the licence text that came '
              'with them, to show the person.')
        print(f'  The library is {lib} (--library FOLDER, or ${samples.DEFAULT_LIBRARY_ENV}, to use another).')
        return 0
    if args.map:
        return _samples_map(args)
    if args.events:
        return _samples_events(args)
    if args.spectrogram:
        return _samples_spectrograms(args)
    try:
        index = samples.offered(samples.load_index(lib))
    except samples.SampleError as error:
        print(f'samples: {error}')
        return 1
    if args.labels:
        held = samples.labels(index, passing=args.passing)
        if args.json:
            print(json.dumps(held, indent=1))
        else:
            print(f'{len(held)} classes (how many recordings carry each); search them with --find:')
            print(textwrap.fill(', '.join(f'{name} {count}' for name, count in held.items()), 118))
        return 0
    if args.find is not None:
        for text in (args.find, *(args.exclude or ())):
            if not samples.searchable(text):
                print(f"samples: '{text}' holds no letters A-Z or digits to search by. Names, titles, tags and labels are "
                      'matched by those, so search in English, for example --find "wood door"')
                return 1
        files = samples.find(index, args.find, passing=args.passing, exclude=args.exclude or (),
                             min_seconds=args.min_seconds, max_seconds=args.max_seconds, project=Path.cwd().name)
    else:
        files = [f for f in index['files'] if not (args.passing and f.get('screen'))]
    total = len(files)
    some = sum(1 for f in files if 'matched_words' in f)        # listed after those holding every word
    summary = not args.find and not args.json and args.limit is None
    limit = args.limit if args.limit is not None else (FIND_SHOWN if args.find and not args.json else 0)
    files = [] if summary else files[:limit] if limit else files
    if args.json:                        # files and matched hold every word, as before; those holding some of them come apart
        print(json.dumps({'packs': index['packs'], 'files': [f for f in files if 'matched_words' not in f], 'matched': total - some,
                          'some_words': [f for f in files if 'matched_words' in f]}, indent=1))
        return 0
    shown = {f['library'] for f in files} if args.find else set(index['packs'])
    for pack_id, pack in sorted(index['packs'].items()):        # a search names only the packs whose licence asks for something
        if pack_id in shown and (not args.find or licences.conditions(pack['licence'])):
            asks = ', '.join(licences.conditions(pack['licence'])) or 'no credit needed'
            print(f"{pack_id}: {pack['title']}" + (f" by {pack['author']}" if pack.get('author') else '') +
                  f" ({pack['licence']}: {asks}), {pack['files']} files")
    for f in files:
        layout = 'mono' if f['channels'] == 1 else 'stereo'
        line = f"  {f['library']}/{f['file']}  {f['seconds']:.2f} s {layout}"
        if f.get('title'):
            m = f.get('measures', {})
            line += (f"  '{f['title'][:60]}' [{', '.join(f.get('labels', [])[:4])}] peak {m.get('peak_db')} dB, "
                     f"range {m.get('range_db')} dB, {'; '.join(f['screen']) if f.get('screen') else 'passes'}")
            via = samples.matched_tags(f, args.find) if args.find else []
            if via:
                line += f" (by its tag{'s' if len(via) > 1 else ''} {', '.join(via[:3])})"
        if 'matched_words' in f:
            line += f" (holds only: {', '.join(f['matched_words'])})"
        print(line)
        if f.get('author_note'):
            print(f"      author's note (read and respect it before use): {f['author_note']}")
    if summary:
        print(f"{total} recording{'s' if total != 1 else ''} in all. Search them with --find WORDS, see the classes they are "
              'labelled with by --labels, or list them with --limit N (0 for all)')
    if args.find:
        every = total - some
        print((f"{every} recording{'s' if every != 1 else ''} match '{args.find}', and {some} more hold some of its words (marked)" if some else
               f"{total} recording{'s' if total != 1 else ''} match '{args.find}'")
              + (f' (the best {len(files)} shown; --limit 0 lists all)' if len(files) < total else ''))
    print('A layer plays one: {"type": "sample", "library": PACK, "file": NAME} (or "file": [NAME, ...], a take per '
          'variant)')
    return 0
