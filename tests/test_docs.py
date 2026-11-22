"""The skill's documents agree with its tools: examples validate, links resolve, every command named exists."""
import json
import re
import unittest

import helpers
from sfxlib import project

SKILL = helpers.REPO / 'skills' / 'sound-effects'


class Docs(unittest.TestCase):
    def test_the_project_format_example_validates(self):
        text = (SKILL / 'references' / 'project-format.md').read_text(encoding='utf-8')
        block = re.search(r'```json\n(.*?)\n```', text, re.S).group(1)
        project.validate(json.loads(block))

    def test_every_name_the_validator_knows_is_documented(self):
        text = (SKILL / 'references' / 'project-format.md').read_text(encoding='utf-8')
        names = (set(project.SOURCE_TYPES) | project.SOURCE_FIELDS | project.LAYER_FIELDS | project.SOUND_FIELDS
                 | project.CHAIN_FIELDS | project.TOP_FIELDS | set(project.USES))
        missing = [n for n in sorted(names) if f'`{n}`' not in text and f'"{n}"' not in text]
        self.assertEqual(missing, [])

    def test_the_frontmatter_fits_the_skill_format(self):
        """The Agent Skills format allows a description of at most 1024 characters and a compatibility note of at
        most 500."""
        text = (SKILL / 'SKILL.md').read_text(encoding='utf-8')
        description = re.search(r'^description: "(.*)"$', text, re.M).group(1)
        compatibility = re.search(r'^compatibility: "(.*)"$', text, re.M).group(1)
        self.assertLessEqual(len(description), 1024)
        self.assertLessEqual(len(compatibility), 500)

    def test_links_in_the_skill_resolve(self):
        for doc in [SKILL / 'SKILL.md', *(SKILL / 'references').glob('*.md')]:
            for target in re.findall(r'\]\(([^)#]+\.md)\)', doc.read_text(encoding='utf-8')):
                self.assertTrue((doc.parent / target).is_file(), f'{doc.name} -> {target}')

    def test_every_command_in_skill_md_exists(self):
        import sfx
        parser = sfx.build_parser()
        choices = next(a for a in parser._actions if a.dest == 'command').choices
        table = (SKILL / 'SKILL.md').read_text(encoding='utf-8').split('## Commands', 1)[1]
        for name in re.findall(r'^\| `([a-z]+)', table, re.M):
            self.assertIn(name, choices, name)
        for name in choices:
            self.assertIn(f'`{name}', table, f'{name} is not in SKILL.md')


if __name__ == '__main__':
    unittest.main()
