"""A run that says it needs a tool (SFX_TEST_REQUIRE, as CI sets it) fails without it, so that the tests using the tool
cannot pass by being skipped."""
import shutil
import unittest

import helpers


class RequiredTools(unittest.TestCase):
    def test_every_required_tool_is_there(self):
        found = {'ffmpeg': helpers.ffmpeg_or_none, 'godot': helpers.godot_or_none, 'node': lambda: shutil.which('node')}
        for name in sorted(helpers.REQUIRED):
            self.assertIn(name, found, f'unknown tool {name}')
            self.assertTrue(found[name](), f'{name} is required for this run and was not found')


if __name__ == '__main__':
    unittest.main()
