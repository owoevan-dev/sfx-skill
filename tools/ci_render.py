"""Render the small fixed project CI looks at: its masters are compared across systems (ci_compare.py) and its audition
page is opened in real browsers (tests/browser).

Usage: python tools/ci_render.py OUT_FOLDER"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills' / 'sound-effects' / 'scripts'))
from sfxlib import ffmpeg, project, render  # noqa: E402

PROJECT = {
    'schema': 'sound-effects/1', 'title': 'CI 音效 café', 'seed': 7, 'variants': 2,
    'flows': [{'name': 'menu', 'sounds': ['confirm', 'coin']}],
    'sounds': [
        {'id': 'confirm', 'category': 'ui', 'recipe': 'ui.confirm'},
        {'id': 'coin', 'category': 'retro', 'recipe': 'retro.coin'},
        {'id': 'knock', 'category': 'impact', 'recipe': 'impact.wood_knock'},
        {'id': 'zap', 'category': 'scifi', 'recipe': 'scifi.zap'},
        {'id': 'sparkle', 'category': 'magic', 'recipe': 'magic.sparkle'},
        {'id': 'breeze', 'category': 'ambience', 'recipe': 'ambience.breeze', 'loop': {'seconds': 4}, 'trim_db': -9},
    ],
}


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    report = render.render(project.validate(PROJECT), sys.argv[1], ffmpeg=ffmpeg.find())      # with FFmpeg the page holds FLAC
    print(f"{report['files']} files in {sys.argv[1]}, {report['flags']} flags")
    return 1 if report['flags'] else 0


if __name__ == '__main__':
    sys.exit(main())
