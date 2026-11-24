"""The clean command."""
import json

from .. import clean
from .common import _library


def _clean_downloads(args):
    if args.trash or args.exports:
        print('clean: --downloads only lists: this skill does not move or delete downloaded archives')
        return 1
    try:
        result = clean.downloads(args.folder, _library(args, must_exist=False))
    except clean.CleanError as error:
        print(f'clean: {error}')
        return 1
    if args.json:
        print(json.dumps(result, indent=1))
        return 0
    for item in result['held']:
        print(f"fully imported: {item['name']} ({item['bytes'] / 1e6:.1f} MB)")
    for item in result['left']:
        print(f"left alone: {item['name']} ({item['why']})")
    if result['held']:
        print('The library holds every CC0 recording in the files marked fully imported (it does not take the others). This '
              'skill leaves them where they are: if you will not import them again, you can delete them yourself.')
    else:
        print('no download here is fully imported')
    return 0


def cmd_clean(args):
    if args.downloads:
        return _clean_downloads(args)
    try:
        result = clean.clean(args.folder, args.keep or [], move=args.trash, exports=args.exports)
    except clean.CleanError as error:
        print(f'clean: {error}')
        return 1
    if args.json:
        print(json.dumps(result, indent=1))
        return 0
    shown, verb = (result['moved'], 'moved') if args.trash else (result['move'], 'would move')
    for item in shown:
        print(f"{verb} to {clean.TRASH}: {item['folder']} ({item['kind']}, {item['bytes'] / 1e6:.1f} MB)")
    for item in result['left']:
        print(f"left alone: {item['folder']} ({item['why']})")
        for s in item.get('stays', []):
            print(f"    {s['path']}: {s['why']}")
    if not shown:
        print('nothing to move')
    elif args.trash:
        print(f"They are in {result['trash']}: to restore one, move it back. This skill never deletes them; when you are "
              f'sure, delete {clean.TRASH} yourself.')
    else:
        print('nothing was moved; run again with --trash to move what is listed (nothing is deleted)')
    if result['trash_bytes']:
        print(f"{clean.TRASH} holds {result['trash_bytes'] / 1e6:.1f} MB")
    return 0
