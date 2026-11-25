"""The commands that work on a project or a render: validate, render, audition, check, expand, export, spectrogram and
readout."""
import json
from pathlib import Path

from .. import (audition, check, export, ffmpeg, mpbridge, readout, recipes, record, render, samples, spectrogram, surge,
                variants, wav)
from .common import _load_project, _print_flags, _print_level, _ffmpeg, _print_traits


def cmd_validate(args):
    proj = _load_project(args.project)
    if proj is None:
        return 1
    files = sum(s['variants'] for s in proj['sounds'])
    n = len(proj['sounds'])
    print(f"OK: {proj['title']}: {n} sound{'s' if n != 1 else ''}, {files} files to render "
          f"(seed {proj['seed']}, level {proj['level']['mode']} under {proj['level']['ceiling_dbtp']} dBTP)")
    return 0


def cmd_render(args):
    proj = _load_project(args.project)
    if proj is None:
        return 1
    config = None
    if args.config:
        try:
            config = mpbridge.load_config(args.config)
        except mpbridge.MusicSkillError as error:
            print(f'render: {error}')
            return 1
    ok, ff = _ffmpeg(args, 'render')
    if not ok:
        return 1
    lib = args.samples
    if not lib and (samples.default_library() / samples.INDEX).is_file():
        lib = samples.default_library()
    if args.sound:
        unknown = sorted(set(args.sound) - {s['id'] for s in proj['sounds']})
        if unknown:
            print(f"render: no sound {', '.join(unknown)} in the project (it has: {', '.join(s['id'] for s in proj['sounds'])})")
            return 1
        proj = dict(proj, sounds=[s for s in proj['sounds'] if s['id'] in args.sound],
                    flows=[f for f in proj['flows'] if set(f['sounds']) <= set(args.sound)])
    try:
        report = render.render(proj, args.output, config=config, samples=lib, ffmpeg=ff, page=not args.no_page,
                               takes=Path(args.project).absolute().parent / surge.FOLDER, record_takes=args.record_takes,
                               music_skill=args.music_skill)
    except render.RenderError as error:
        print(f'render: {error}')
        return 1
    print(f"{report['title']}: {report['files']} files in {args.output} ({report['seconds']} s)")
    if 'surge_takes' in report:
        takes = report['surge_takes']
        print(f"Surge takes: {takes['recorded']} recorded, {takes['played'] - takes['recorded']} replayed; {surge.FOLDER} beside the project holds "
              f"{takes['folder_bytes'] / 1e6:.1f} MB and belongs with it")
    _print_level(args.output)
    _print_flags(json.loads((Path(args.output) / 'check.json').read_text(encoding='utf-8')))
    if args.sound and proj['level'].get('target_lufs') is None:
        print('These sounds were levelled among themselves: render the whole project for the set\'s level.')
    elif args.sound:
        print('The level is held by level.target_lufs, though a full render can still lower it if a sound left out cannot reach it.')
    if not args.no_page:
        print(f"audition page: {Path(args.output) / 'audition.html'}")
    return 0


def cmd_audition(args):
    folder = Path(args.render)
    if not (folder / 'check.json').is_file():
        print(f'audition: {folder} is not a checked render folder (no check.json)')
        return 1
    try:                                 # FFmpeg only makes the page smaller, so a missing or stale one is no failure
        ff = ffmpeg.find(args.ffmpeg, mpbridge.load_config(args.config) if args.config else None)
    except (ffmpeg.FFmpegError, mpbridge.MusicSkillError) as error:
        print(f'audition: {error}; the page holds WAV')
        ff = None
    path = audition.build(folder, ffmpeg=ff)
    render.refresh_listing(folder, ['audition.html'])
    print(f'audition page: {path}')
    return 0


def cmd_check(args):
    folder = Path(args.render)
    if not (folder / 'resolved.json').is_file():
        print(f'check: {folder} is not a render folder (no resolved.json)')
        return 1
    report = check.run(folder)
    render.refresh_listing(folder, ['check.json'])
    print(f"{len(report['files'])} files measured")
    _print_level(folder)
    if args.traits:
        _print_traits(report)
    _print_flags(report)
    return 0


def cmd_expand(args):
    raw_path = Path(args.project)
    proj = _load_project(raw_path)
    if proj is None:
        return 1
    out = Path(args.output)
    if out.exists():
        print(f'expand: {out} exists; expand writes a new file')
        return 1
    raw = json.loads(raw_path.read_text(encoding='utf-8'))
    done = []
    for i, sound in enumerate(proj['sounds']):
        if 'recipe' not in sound or (args.sound and sound['id'] not in args.sound):
            continue
        spec, _, _ = variants.spec_for(sound, proj['seed'], 0)
        recipe = recipes.get(sound['recipe'])
        cents = max([sound['vary'].get(p.name, 0.0) for p in recipe.params if p.vary == 'cents'] or [0.0])
        pct = max([sound['vary'].get(p.name, 0.0) for p in recipe.params if p.vary == 'pct'] or [0.0])
        new = {k: v for k, v in raw['sounds'][i].items() if k not in ('recipe', 'params', 'vary')}
        if sound.get('crackle'):
            new['crackle'] = True                  # what the recipe says of its sound, which the check goes by
        if sound.get('loop'):
            new['use'] = 'loop'
            new['loop'] = dict(sound['loop'])
            pct = 0.0                              # a loop keeps its length
        new['layers'] = spec['layers']
        if spec.get('chain'):
            new['chain'] = spec['chain']
        new['vary'] = {k: v for k, v in (('pitch_cents', cents), ('time_pct', pct)) if v}
        new['trim_db'] = sound['trim_db']
        new['notes'] = (sound.get('notes', '') + f' (expanded from {recipe.name})').strip()
        raw['sounds'][i] = new
        done.append(sound['id'])
    out.write_text(json.dumps(raw, indent=1, ensure_ascii=False) + '\n', encoding='utf-8', newline='\n')
    print(f"{out}: {len(done)} sound(s) expanded ({', '.join(done) or 'none'}); variation now by pitch_cents and "
          'time_pct only (timbre and level draws of the recipe have no layer equivalent)')
    return 0


def cmd_export(args):
    formats = [f.strip() for f in args.formats.split(',') if f.strip()] if args.formats else None
    ok, ff = _ffmpeg(args, 'export')
    if not ok:
        return 1
    try:
        manifest = export.export(args.render, args.output, args.target, formats=formats, ffmpeg=ff,
                                 res_path=args.res_path)
    except export.ExportError as error:
        print(f'export: {error}')
        return 1
    print(f"{manifest['title']}: {manifest['files']} files for {manifest['target']} in {args.output} "
          f"({', '.join(manifest['formats'])})")
    flags = manifest['flags']
    print(f'{len(flags)} flag(s)' + (':' if flags else ''))
    for f in flags:
        where = Path(f['file']).name if f.get('file') else (f.get('sound') or 'export')
        print(f"  [{f['kind']}] {where}: {f['message']}")
    print(f"manifest: {Path(args.output) / 'manifest.json'}")
    return 0


def cmd_spectrogram(args):
    target = Path(args.target)
    jobs = []
    if target.is_dir():
        resolved_path = target / 'resolved.json'
        if not resolved_path.is_file():
            print(f'spectrogram: {target} is not a render folder (no resolved.json)')
            return 1
        resolved = record.load(target)
        out = Path(args.output) if args.output else target / 'spectrograms'
        ending = '.spectrogram.png' if args.output else '.png'     # only in its own folder does a picture take the plain name
        for snd in resolved['sounds']:
            if args.sound and snd['id'] not in args.sound:
                continue
            chosen = snd['variants'] if args.all_variants else snd['variants'][:1]
            for v in chosen:
                jobs.append((v['file'], out / (Path(v['file']).stem + ending), snd.get('peak_at')))
    elif target.suffix.lower() == '.wav' and target.is_file():
        out = Path(args.output) if args.output else target.parent
        jobs.append((target, out / (target.stem + '.spectrogram.png'), None))   # never over a picture of that name
    else:
        print(f'spectrogram: {target} is neither a render folder nor a WAV file')
        return 1
    if not jobs:
        print('spectrogram: no sound matched')
        return 1
    own_folder = target.is_dir() and not args.output
    for _, png, _ in jobs:
        if record.is_link(png):
            print(f'spectrogram: {png} is a link, and a picture is not written through one')
            return 1
        if own_folder and png.exists() and not render.as_listed(target, png.relative_to(target).as_posix()):
            print(f'spectrogram: {png} is not the picture this skill drew (changed or added since): move it away to draw it '
                  'again')
            return 1
    for src, png, peak_at in jobs:
        png.parent.mkdir(parents=True, exist_ok=True)
        if target.is_dir():
            data, rate = record.master(target, src), record.RATE
        else:
            data, info = wav.read(src)
            rate = info['rate']
        scale = spectrogram.write(data, png, rate)
        wanted = f", peak_at {peak_at:.3f} s" if peak_at is not None else ''
        print(f"{png}: {scale['seconds']} s, {scale['column_ms']} ms a column, loudest at {scale['peak_s']} s{wanted}")
    if own_folder:                                          # its own folder's pictures are part of the render
        render.refresh_listing(target, [png.relative_to(target).as_posix() for _, png, _ in jobs])
    print('Top: the level (the loudest millisecond marked white). Below: frequency on a log scale, 20 Hz at the '
          'bottom to 20 kHz at the top, faint lines at 100 Hz, 1 kHz and 10 kHz; time to the right, a faint line every '
          '0.1 s and a brighter one every second; level from black (80 dB under the loudest) to white-yellow.')
    return 0


def _zoom_options(args):
    """(zoom?, hz range or None, error or None) from the readout's arguments."""
    zoom = args.from_s is not None or args.to_s is not None
    if zoom and (args.from_s is None or args.to_s is None):
        return zoom, None, 'zoom in with both --from and --to'
    if not zoom and (args.slice_ms is not None or args.hz or args.channel or args.page != 1 or args.bands != 'octave'):
        return zoom, None, '--slice-ms, --bands, --hz, --channel and --page go with --from and --to'
    hz = None
    if args.hz:
        try:
            low, high = (float(v) for v in args.hz.split('-', 1))
        except ValueError:
            return zoom, None, f'--hz takes LOW-HIGH in Hz, such as 500-4000, not {args.hz!r}'
        hz = (low, high)
    if args.page < 1:
        return zoom, None, '--page counts from 1'
    return zoom, hz, None


def cmd_readout(args):
    target = Path(args.target)
    zoom, hz, problem = _zoom_options(args)
    if problem:
        print(f'readout: {problem}')
        return 1
    if args.variant is not None and (args.all_variants or args.variant < 1):
        print('readout: --variant takes one variant, numbered from 1, instead of --all-variants')
        return 1
    jobs = []
    if target.is_dir():
        if not (target / 'resolved.json').is_file():
            print(f'readout: {target} is not a render folder (no resolved.json)')
            return 1
        resolved = record.load(target)
        for snd in resolved['sounds']:
            if args.sound and snd['id'] not in args.sound:
                continue
            if args.variant is not None:
                if args.variant > len(snd['variants']):
                    continue
                chosen = [(args.variant, snd['variants'][args.variant - 1])]
            else:
                chosen = list(enumerate(snd['variants'] if args.all_variants else snd['variants'][:1], start=1))
            for n, v in chosen:
                name = f"{snd['id']}, variant {n}" if args.all_variants or args.variant is not None else snd['id']
                jobs.append((name, v['file'], snd.get('use') == 'loop', snd.get('peak_at')))
    elif target.suffix.lower() == '.wav' and target.is_file():
        jobs.append((target.name, target, None, None))
    else:
        print(f'readout: {target} is neither a render folder nor a WAV file')
        return 1
    if not jobs:
        print('readout: no sound matched')
        return 1
    found = []
    for name, src, loop, peak_at in jobs:
        if target.is_dir():
            try:
                data, rate = record.master(target, src), record.RATE
            except ValueError as error:                 # a master changed into a layout the reader does not take
                print(f'readout: {src}: {error}')
                return 1
        else:
            try:
                data, info = wav.read(src)
            except ValueError as error:                 # a layout or encoding the reader does not take, or a damaged file
                print(f'readout: {src}: {error}')
                return 1
            rate = info['rate']
            whole = [len(data)] if len(data) else []
            loop = args.loop or any(start == 0 and end in whole for start, end in info.get('loops', []))
        analysis = readout.analyse(data, rate, loop=bool(loop), peak_at=peak_at)
        if zoom:
            try:
                result = readout.view_zoom(analysis, args.from_s, args.to_s, bands=args.bands, hz=hz, channel=args.channel,
                                           slice_ms=args.slice_ms)
            except ValueError as error:
                print(f'readout: {name}: {error}')
                return 1
        else:
            result = readout.view_overview(analysis)
        if args.json:
            found.append({'sound': name, **result})
        elif zoom:
            pages = readout.text_zoom(result, name)
            if args.page > len(pages):
                print(f'readout: {name} fills {len(pages)} page{"s" if len(pages) > 1 else ""} at these settings')
                return 1
            print('\n'.join(pages[args.page - 1]))
            print()
        else:
            print('\n'.join(readout.text(result, name)))
            print()
    if args.json:
        print(json.dumps(found, indent=1, allow_nan=False))
    elif zoom:
        print('Cells: each band\'s power over each slice; a narrow low band reads coarser than the slices, shown by ^. '
              'Events and tonal lines are every one in the window.')
    else:
        print('Cells: each octave band\'s power over each slice, in dB under the loudest cell. Tonal lines are steady '
              'pitches; events are sudden rises. The spectrogram command draws the same sound as a picture.')
        print('Look closer: --from S --to S zooms in on a window (finer slices, every line and event in it; --bands third, '
              '--hz LOW-HIGH, --channel left|right; --variant N reads another variant).')
    return 0
