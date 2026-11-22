"""Run every test: the unit tests, the integration tests and the checks of the repository itself.

Usage: python tools/check_all.py [--quick]     (--quick leaves out the tests that need music-production's FFmpeg)

Child processes get TMP and TEMP from tools/local.json and write no bytecode files. Tests write their scratch output
under work/tests/ and never delete it."""
import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'tests'))
import helpers  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--quick', action='store_true', help='skip the tests that call FFmpeg or music-production')
    args = parser.parse_args()
    env = helpers.child_env()
    if args.quick:
        env['SFX_QUICK'] = '1'
    tests = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-t', 'tests', '-v'],
                           cwd=REPO, env=env)
    print(f"check_all: tests {'OK' if tests.returncode == 0 else 'FAILED'}")
    return 0 if tests.returncode == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
