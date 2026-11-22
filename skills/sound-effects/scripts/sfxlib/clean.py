"""Clean: list the render folders a project no longer needs, and move them aside. Nothing here deletes a file.

A folder that goes, goes whole and unchanged into FOLDER/.sfx-trash/<time>/, from where moving it back restores it; emptying
.sfx-trash is left to the person. How a folder is recognised follows music-production's `studio.py clean` (0.8.0); what may
go is stricter:

- Nothing moves without move=True (`--trash`); by default the folders are only listed.
- At least one render is kept (`--keep`, required), and the kept renders name the project by their title: a render or
  export of another title is left alone and listed.
- A folder counts only when its own record shows that this skill made it: a regular file resolved.json whose
  `made_by` starts with "sound-effects " (a render), or manifest.json whose `schema` is "sound-effects-export/1" (an
  export), and its files.json. A folder's name proves nothing.
- A folder moves only when all of it is what this skill wrote: every file listed in files.json and unchanged (size and
  SHA-256, looked at again just before the move), and no links. A note, a changed file or a link keeps the whole folder
  where it is, and is listed.
- Nothing newer than the kept render of its project moves. A folder's age is that of its record, which later commands do
  not rewrite.
- Exports stay unless asked for (exports=True, `--exports`), because a game may be using them. Even then an export of a
  kept render stays.
- Links are never followed or moved; a folder or a keep given as a link is refused, and so is a .sfx-trash that is one.

A render or export writes its files.json last, so a folder still being written has none and is left alone.

`downloads` only lists: which of FSD50K's archives in a folder the library holds in full (by the dataset's or a release
pack's own names and sums, every recording unchanged), with their partial copies, for the person to delete if they wish."""
import hashlib
import json
import os
import time
from pathlib import Path

from .record import is_link

RECORD = {'render': 'resolved.json', 'export': 'manifest.json'}
LISTING = 'files.json'
TRASH = '.sfx-trash'
OWN_FOLDERS = ('master', 'instruments', 'spectrograms')      # a render's own, also when nothing is in them


class CleanError(Exception):
    """The clean cannot be planned (a link, a keep that is not this project's folder, ...)."""


def _regular(path):
    return path.is_file() and not is_link(path)


def _json(path):
    if not _regular(path):
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def record(folder):
    """('render' or 'export', its record) when this skill made the folder, else (None, None)."""
    folder = Path(folder)
    r = _json(folder / RECORD['render'])
    if isinstance(r, dict) and str(r.get('made_by', '')).startswith('sound-effects '):
        return 'render', r
    m = _json(folder / RECORD['export'])
    if isinstance(m, dict) and m.get('schema') == 'sound-effects-export/1':
        return 'export', m
    return None, None


def _made(folder, kind):
    """When a folder's record was written: a render or export writes it once, and later commands leave it alone."""
    return (Path(folder) / RECORD[kind]).stat().st_mtime_ns


def _sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def _unchanged(path, entry):
    try:
        return _regular(path) and path.stat().st_size == entry.get('bytes') and _sha256(path) == entry.get('sha256')
    except OSError:
        return False


def _tree(top):
    """(the files and links under a folder, its real subfolders), never going into a link or a junction."""
    files, folders, stack = [], [], [Path(top)]
    while stack:
        for p in stack.pop().iterdir():
            if p.is_dir() and not is_link(p):
                folders.append(p)
                stack.append(p)
            else:
                files.append(p)
    return sorted(files), folders


def _contents(sub):
    """(the bytes of a folder's files, what in it this skill did not write or that has changed), or (None, None) when it has
    no files.json to go by."""
    entries = _json(sub / LISTING)
    if not isinstance(entries, list):
        return None, None
    listed = {e['path']: e for e in entries
              if isinstance(e, dict) and isinstance(e.get('path'), str) and '..' not in Path(e['path']).parts}
    try:
        found, inner = _tree(sub)
        empty = [d for d in inner if not any(d.iterdir())]
    except OSError as error:
        return 0, [{'path': sub.name, 'why': f'could not be read ({error.strerror or error})'}]
    total, stays = 0, []
    for path in found:
        rel = path.relative_to(sub).as_posix()
        if rel == LISTING:
            continue
        entry = listed.get(rel)
        if is_link(path):
            stays.append({'path': rel, 'why': 'a link'})
        elif entry is None:
            stays.append({'path': rel, 'why': 'not made by this skill'})
        elif not _unchanged(path, entry):
            stays.append({'path': rel, 'why': 'changed since it was written'})
        else:
            total += entry['bytes']
    ours = set(OWN_FOLDERS) | {parent.as_posix() for rel in listed for parent in Path(rel).parents}
    stays += [{'path': d.relative_to(sub).as_posix(), 'why': 'not made by this skill'} for d in empty
              if d.relative_to(sub).as_posix() not in ours]
    return total, sorted(stays, key=lambda s: s['path'])


def _trash_bytes(folder):
    """How much .sfx-trash holds, so that the person can see when to empty it."""
    trash = Path(folder) / TRASH
    if is_link(trash) or not trash.is_dir():
        return 0
    try:
        return sum(p.lstat().st_size for p in _tree(trash)[0] if p.parent != trash)         # not its two marker files
    except OSError:
        return 0


def _refuse_a_bad_trash(folder):
    trash = Path(folder) / TRASH
    if is_link(trash) or (trash.exists() and not trash.is_dir()):
        raise CleanError(f'{trash} is a link or a file: folders are not moved into it')


def plan(folder, keep, exports=False):
    """What a clean of `folder` with the kept folders `keep` would move, and what stays and why: {'folder', 'kept', 'move':
    [{'folder', 'kind', 'bytes'}], 'left': [{'folder', 'why'; 'stays': [{'path', 'why'}] when what it holds keeps it}],
    'trash_bytes'}."""
    folder = Path(folder)
    if is_link(folder) or not folder.is_dir():
        raise CleanError(f'{folder} is not a folder (or is a link, which clean does not follow)')
    _refuse_a_bad_trash(folder)
    if not keep:
        raise CleanError('name at least one folder to keep (--keep), such as the render you chose')
    kept, kept_renders, newest = [], set(), {}                    # newest: the latest kept render of each project title
    for k in keep:
        k = Path(k)
        if not k.is_absolute() and (folder / k).exists():     # the name of a folder in `folder`, wherever this is run from
            k = folder / k
        if is_link(k):
            raise CleanError(f'{k} is a link; clean does not follow links')
        if not k.is_dir() or k.resolve().parent != folder.resolve():
            raise CleanError(f'{k} is not a folder directly inside {folder}')
        kind, rec = record(k)
        if kind != 'render':
            raise CleanError(f'{k} is not a render of this skill: name the render you chose (exports stay unless you add '
                             '--exports)')
        kept.append(k.resolve())
        kept_renders.add(k.resolve().name.casefold())
        title = str(rec.get('title'))
        newest[title] = max(newest.get(title, 0), _made(k, 'render'))
    out = {'folder': str(folder), 'kept': [str(k) for k in kept], 'move': [], 'left': [], 'trash_bytes': _trash_bytes(folder)}
    for sub in sorted(folder.iterdir()):
        if sub.name == TRASH or is_link(sub) or not sub.is_dir() or sub.resolve() in kept:
            continue
        kind, rec = record(sub)
        if kind is None:
            continue
        title = str(rec.get('title'))
        if title not in newest:
            why = f"another project ({rec.get('title')!r})"
        elif kind == 'export' and str(rec.get('render')).casefold() in kept_renders:
            why = f"an export of the kept render {rec.get('render')}"
        elif kind == 'export' and not exports:
            why = 'an export: exports stay unless you add --exports'
        elif _made(sub, kind) >= newest[title]:
            why = 'newer than the kept version'
        else:
            why = None
        if why:
            out['left'].append({'folder': sub.name, 'why': why})
            continue
        size, stays = _contents(sub)
        if size is None:
            out['left'].append({'folder': sub.name, 'why': 'no files.json (made before it, or still being written)'})
        elif stays:
            out['left'].append({'folder': sub.name, 'why': 'it holds what this skill did not write, or has changed',
                                'stays': stays})
        else:
            out['move'].append({'folder': sub.name, 'kind': kind, 'bytes': size})
    return out


def _new_place(folder):
    """A new folder in the project's .sfx-trash, which, when first made, is told to be passed by: Godot by a .gdignore, git by a
    .gitignore of everything (Unity skips a folder whose name starts with a dot)."""
    _refuse_a_bad_trash(folder)
    trash = Path(folder) / TRASH
    try:
        trash.mkdir(exist_ok=True)
        for name, text in (('.gdignore', ''), ('.gitignore', '*\n')):
            try:
                with open(trash / name, 'x', encoding='utf-8', newline='\n') as f:   # 'x': never through a file already there
                    f.write(text)
            except FileExistsError:
                pass
        stamp = time.strftime('%Y%m%d-%H%M%S')
        for n in range(1, 10000):
            place = trash / (stamp if n == 1 else f'{stamp}-{n}')
            try:
                place.mkdir()
                return place
            except FileExistsError:
                continue
    except OSError as error:
        raise CleanError(f'{trash} could not be made ready, so nothing was moved: {error.strerror or error}') from None
    raise CleanError(f'no free name in {trash}')


def move_planned(plan_, rename=os.rename):
    """Carry out a plan: each folder, looked at once more, goes whole into a new folder of .sfx-trash. One that has changed,
    or that cannot be moved (a file in it is in use), stays where it is and is listed. Adds 'moved' and 'trash'."""
    base = Path(plan_['folder'])
    plan_['moved'], plan_['trash'] = [], None
    place = None
    for item in plan_['move']:
        sub = base / item['folder']
        size, stays = (None, None)
        if not is_link(sub) and sub.is_dir() and record(sub)[0] == item['kind']:
            size, stays = _contents(sub)
        if size is None or stays:
            plan_['left'].append({'folder': item['folder'], 'why': 'changed since it was looked at', 'stays': stays or []})
            continue
        place = place or _new_place(base)
        try:
            rename(sub, place / item['folder'])
        except OSError as error:
            plan_['left'].append({'folder': item['folder'], 'why': f'could not be moved ({error.strerror or error})'})
            continue
        plan_['moved'].append(item)
    if plan_['moved']:
        plan_['trash'] = str(place)
    plan_['trash_bytes'] = _trash_bytes(base)
    return plan_


def clean(folder, keep, move=False, exports=False, rename=os.rename):
    """Plan the clean and, with move=True, carry it out; returns the plan with what was moved ('moved') and where ('trash').
    `rename` does the moving."""
    p = plan(folder, keep, exports)
    if move:
        return move_planned(p, rename)
    p['moved'], p['trash'] = [], None
    return p


def downloads(folder, library):
    """What of a download folder a library already holds in full: {'folder', 'held': [{'name', 'bytes'}], 'left': [{'name',
    'why'}]}. An archive counts as held only when every recording it gave the library is there, unchanged by its SHA-256;
    its partial copies are listed with it. This only reads: what happens to the files is the person's to decide."""
    import re
    import zipfile

    from . import fsd50k, samples
    folder, library = Path(folder), Path(library)
    if is_link(folder) or not folder.is_dir():
        raise CleanError(f'{folder} is not a folder (or is a link, which clean does not follow)')
    try:
        held = samples.load_index(library)
    except samples.SampleError as error:
        raise CleanError(str(error)) from None
    rows = {r['path']: r for r in held['files']}
    out = {'folder': str(folder), 'held': [], 'left': []}

    def intact(path_in_library):
        row = rows.get(path_in_library)
        target = library / path_in_library
        return row is not None and _regular(target) and _sha256(target) == row['sha256']

    def judge(path, why):
        if why:
            out['left'].append({'name': path.name, 'why': why})
        else:
            out['held'].append({'name': path.name, 'bytes': path.stat().st_size})

    pack_parts = {}
    if (folder / fsd50k.PACK_INDEX).is_file():
        try:
            pack_parts = {p['name']: p for p in fsd50k.read_pack_index(folder)['parts']}
        except fsd50k.FSD50KError as error:
            out['left'].append({'name': fsd50k.PACK_INDEX, 'why': str(error)})

    def holds(path):
        with zipfile.ZipFile(path) as z:
            return all(fsd50k.PACK_MEMBER_RE.fullmatch(n) and intact(n) for n in z.namelist())

    whole_set = {}

    def set_is_held(which):
        if which not in whole_set:
            pack_id = f'fsd50k-{which}'
            pack = held['packs'].get(pack_id)
            mine = [p for p, r in rows.items() if r['library'] == pack_id]
            whole_set[which] = bool(mine) and pack is not None and pack.get('status') != fsd50k.PACK_STATUS \
                and pack.get('files') == len(mine) and all(intact(p) for p in mine)
        return whole_set[which]

    files = sorted(p for p in folder.iterdir() if _regular(p))
    for path in files:
        name = path.name
        if name in pack_parts:
            if fsd50k.sha256_of(path) != pack_parts[name]['sha256']:
                judge(path, 'not the file its index names (damaged?)')
            elif not holds(path):
                judge(path, 'the library does not hold all of its recordings, unchanged')
            else:
                judge(path, None)
        elif name in fsd50k.KNOWN_MD5 and name not in (fsd50k.META_ZIP, fsd50k.TRUTH_ZIP):
            which = 'eval' if 'eval' in name else 'dev'
            if not set_is_held(which):
                judge(path, f"the library does not hold FSD50K's {which} set from these files, unchanged")
            elif fsd50k.md5_of(path) != fsd50k.KNOWN_MD5[name]:
                judge(path, "not the dataset's file (damaged?)")
            else:
                judge(path, None)
    whole = {item['name'] for item in out['held']}
    for path in files:                                     # the partial copies of what is held, by their exact names
        m = re.fullmatch(r'(.+)\.(?:part|bad-[0-9]+)', path.name)
        if m and m.group(1) in whole:
            judge(path, None)
    return out
