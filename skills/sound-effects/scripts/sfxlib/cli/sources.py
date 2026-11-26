"""The commands that list what a layer can play: recipes, banks, surge, and music's quotes."""
import json
from pathlib import Path

from .. import instruments, midifile, mpbridge, music, project, recipes, samples, surge


def cmd_recipes(args):
    items = recipes.all_recipes()
    if args.category:
        items = [r for r in items if r.category == args.category]
    if args.name:
        try:
            items = [recipes.get(args.name)]
        except KeyError as error:
            print(error.args[0])
            return 1
    if args.layers:
        if not args.name:
            print('recipes: --layers shows one recipe: add --name')
            return 1
        r = items[0]
        built = r.build(dict(r.defaults(), seconds=project.DEFAULT_LOOP_S) if r.use == 'loop' else r.defaults())
        print(json.dumps({'layers': built['layers'], 'chain': built.get('chain', {})}, indent=1))
        return 0
    if args.json:
        print(json.dumps([{'name': r.name, 'category': r.category, 'use': r.use, 'summary': r.summary, 'trim_db': r.trim_db,
                           'params': [p.__dict__ for p in r.params], 'rules': [list(x) for x in r.rules]}
                          for r in items], indent=1))
        return 0
    for r in items:
        kind = ', loop' if r.use == 'loop' else ''
        print(f'{r.name} ({r.category}{kind}, trim {r.trim_db:+.0f} dB): {r.summary}')
        for p in r.params:
            vary = f', varies {p.amount:g} {p.vary}' if p.vary and p.amount else ''
            print(f'    {p.name} = {p.default:g} {p.unit} ({p.lo:g} to {p.hi:g}{vary}){": " + p.help if p.help else ""}')
        if args.name:
            for rule, source in r.rules:
                print(f'    rule: {rule} [{source}]')
    if not items:
        print('no recipes')
    return 0


def cmd_banks(args):
    try:
        config = mpbridge.load_config(args.config)
    except mpbridge.MusicSkillError as error:
        print(error)
        return 1
    rows = instruments.list_banks(config)
    if args.bank:
        rows = [r for r in rows if r['name'] in args.bank]
    if args.find:
        needle = args.find.lower()
        rows = [dict(r, presets=[p for p in r['presets'] if needle in p[2].lower()]) if r['usable'] else r
                for r in rows]
    if args.json:
        print(json.dumps(rows, indent=1))
        return 0
    for r in rows:
        if not r['usable']:
            why = r['problem'] or f"licensed '{r['licence']}', which may put conditions on the sound"
            print(f"{r['name']}: not usable ({why})")
            continue
        if args.find and not r['presets']:
            continue
        print(f"{r['name']} ({r['licence']}): {len(r['presets'])} preset{'s' if len(r['presets']) != 1 else ''}")
        for bank, program, name in r['presets']:
            print(f'  {bank}:{program} {name}')
    print('A layer plays one: {"type": "instrument", "soundfont": NAME, "bank": B, "program": P, "notes": [...]}')
    return 0


def cmd_surge(args):
    try:
        skill = mpbridge.find_music_skill(args.music_skill or None)
    except mpbridge.MusicSkillError as error:
        print(f'surge: {error}')
        return 1
    if skill is None:
        print("surge: Surge XT comes with the music-production skill, which was not found (its setup installs it; --music-skill names its folder)")
        return 1
    try:
        presets = surge.load_catalog(skill)
    except surge.SurgeError as error:
        print(f'surge: {error}')
        return 1
    if args.id:
        entry = presets.get(args.id)
        if entry is None:
            print(f"surge: there is no patch '{args.id}' in Surge's catalog (sfx.py surge --find WORDS lists them)")
            return 1
        why = surge.refused(entry)
        if why:
            print(f"surge: patch '{args.id}' cannot be used: {why}")
            return 1
        print(json.dumps(dict(entry, id=args.id), indent=1) if args.json else '\n'.join(surge.describe(args.id, entry)))
        return 0
    if args.find is not None and not samples.searchable(args.find):
        print(f"surge: '{args.find}' holds no letters A-Z or digits to search by. Patch names, categories and designers are in English, for example --find bell")
        return 1
    found = surge.find(presets, args.find or '', args.role, args.character, project=Path.cwd().name)
    shown = found if args.limit == 0 else found[:args.limit]
    if args.json:
        print(json.dumps([dict(presets[key], id=key) for key in shown], indent=1))
        return 0
    print(f'{len(found)} patches match, {len(shown)} shown' + (' (--limit 0 lists all)' if len(shown) < len(found) else '') + ", in this project's own order:")
    for key in shown:
        print(f'  {surge.summary(key, presets[key])}')
    print('--id ID shows what its macros and mod wheel move. A layer plays one: {"type": "surge", "patch": ID, "notes": [[0, 72, 0.5, 100]]}; '
          'render with --record-takes --config CONFIG to record it')
    return 0


def cmd_music(args):
    if midifile.is_midi(args.song):
        return _music_midi(args)
    try:
        song = music.load(args.song)
        if args.track is None and args.bars is None:
            info = music.describe(song)
            print(json.dumps(info, indent=1) if args.json else '\n'.join(music.overview(info)))
            return 0
        if args.track is None or args.bars is None:
            raise music.MusicError('--track and --bars go together, such as --track lead --bars 5-6')
        quoted = music.quote(song, args.track, *music.parse_bars(args.bars))
    except music.MusicError as error:
        print(f'music: {error}')
        return 1
    print(json.dumps(quoted, indent=1) if args.json else '\n'.join(music.quote_lines(quoted)))
    return 0


def _music_midi(args):
    """sfx.py music FILE.mid: the file's tempo, meter and parts, or one part's notes in some bars."""
    try:
        song = midifile.load(args.song)
        if args.track is None and args.bars is None:
            print(json.dumps(midifile.describe(song), indent=1) if args.json else '\n'.join(midifile.overview(song)))
            return 0
        if args.track is None or args.bars is None:
            raise music.MusicError('--track and --bars go together, such as --track 2 --bars 5-6')
        quoted = midifile.quote(song, args.track, *music.parse_bars(args.bars))
    except music.MusicError as error:
        print(f'music: {error}')
        return 1
    print(json.dumps(quoted, indent=1) if args.json else '\n'.join(midifile.quote_lines(quoted)))
    return 0
