"""A run that says it needs a tool (SFX_TEST_REQUIRE, as CI sets it) fails without it, so that the tests using the tool
cannot pass by being skipped."""
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

import helpers


class RequiredTools(unittest.TestCase):
    def test_every_required_tool_is_there(self):
        found = {'ffmpeg': helpers.ffmpeg_or_none, 'godot': helpers.godot_or_none, 'node': lambda: shutil.which('node')}
        for name in sorted(helpers.REQUIRED):
            self.assertIn(name, found, f'unknown tool {name}')
            self.assertTrue(found[name](), f'{name} is required for this run and was not found')


class TheProjectCIRenders(unittest.TestCase):
    def test_it_raises_no_flag(self):
        """CI fails on any flag in this project, and only CI runs it, so a new flag could otherwise first fail there."""
        tool = Path(__file__).resolve().parents[1] / 'tools' / 'ci_render.py'
        run = subprocess.run([sys.executable, str(tool), str(helpers.scratch('ci_render') / 'render')], capture_output=True,
                             text=True, encoding='utf-8', errors='replace')
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


if __name__ == '__main__':
    unittest.main()
