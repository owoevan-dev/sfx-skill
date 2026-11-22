"""The repository as a package: the skill's format, the plugin manifests, links, languages, what files may hold, and the
limits of the plugin directories."""
import json
import re
import subprocess
import unicodedata
import unittest
from pathlib import Path

import helpers
from sfxlib import VERSION, project

try:
    import yaml
except ImportError:
    yaml = None

ROOT = helpers.REPO
SKILL = ROOT / 'skills' / 'sound-effects'
LANGUAGES = ('README.md', 'README.zh-TW.md', 'README.zh-CN.md', 'README.ja.md', 'README.ko.md')
SKIP_PARTS = {'.git', '__pycache__', '.pytest_cache', 'work', 'research'}
TEXT = ('.py', '.md', '.json', '.txt', '.yaml', '.yml', '.toml', '.cfg', '.svg', '')
PERSONAL = [re.compile(p) for p in (
    r'(?<![A-Za-z])[A-Za-z]:[\\/][A-Za-z0-9 _.\-]{2,}(?=[\\/`\'")\],;:\s]|$)', r'[\\/]Users[\\/]\w', r'[\\/]home[\\/](?!runner)\w',
    r'\bsk-[A-Za-z0-9_-]{16,}', r'\bgh[pos]_[A-Za-z0-9]{20,}', r'[\w.+-]+@[\w-]+\.[\w.]+', r'(?i)api[_-]?key\s*[:=]\s*[\'"]?\w{12,}')]


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def repository_files():
    """The repository's own files: git's list in a checkout, every file in an unpacked archive; never the scratch or
    private folders a working copy may hold."""
    try:
        listed = subprocess.run(['git', 'ls-files', '-z'], cwd=ROOT, capture_output=True, check=True).stdout.decode('utf-8')
        files = [ROOT / f for f in listed.split('\0') if f]
    except (OSError, subprocess.CalledProcessError):
        files = []
    files = files or [p for p in ROOT.rglob('*') if p.is_file()]
    return [p for p in files if p.is_file() and not SKIP_PARTS & set(p.relative_to(ROOT).parts)]


def frontmatter():
    text = (SKILL / 'SKILL.md').read_text(encoding='utf-8')
    m = re.match(r'^---\n(.*?)\n---\n', text, re.S)
    if yaml is not None:
        return text, yaml.safe_load(m.group(1))        # strict, as the agents' skill loaders are
    fields = dict(re.findall(r'^([a-z-]+): "?(.*?)"?$', m.group(1), re.M))
    fields['metadata'] = {'version': re.search(r'version: "(.*?)"', m.group(1)).group(1)}
    return text, fields


def heading_anchor(heading):
    """GitHub's anchor for a heading."""
    parts = re.split(r'(`[^`]+`)', heading.strip())
    text = ''.join(part[1:-1] if part.startswith('`') else re.sub(r'</?[A-Za-z][A-Za-z0-9-]*(\s[^<>]*)?/?>', '', part)
                   for part in parts)
    text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text).lower()
    return re.sub(r'[^\w\- ]', '', text).replace(' ', '-')


def image_size(path):
    if path.suffix == '.svg':
        w, h = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', path.read_text(encoding='utf-8')).groups()
        return float(w), float(h)
    data = path.read_bytes()[:24]
    assert data[:8] == b'\x89PNG\r\n\x1a\n', path
    return int.from_bytes(data[16:20], 'big'), int.from_bytes(data[20:24], 'big')


class SkillFormat(unittest.TestCase):
    def test_frontmatter_follows_the_agent_skills_format(self):
        text, front = frontmatter()
        self.assertEqual(front['name'], SKILL.name)
        self.assertRegex(front['name'], r'^[a-z0-9]+(-[a-z0-9]+)*$')
        self.assertLessEqual(len(front['name']), 64)
        self.assertTrue(0 < len(front['description']) <= 1024)
        self.assertNotRegex(front['description'], '[<>]')
        self.assertLessEqual(len(front.get('compatibility', '')), 500)
        self.assertTrue(all(isinstance(v, str) for v in front['metadata'].values()))
        self.assertLess(len(text.splitlines()), 500)

    def test_references_are_one_level_deep_and_long_ones_have_contents(self):
        for doc in (SKILL / 'references').glob('*.md'):
            text = doc.read_text(encoding='utf-8')
            self.assertEqual(re.findall(r'\]\((?!https?:)(?:\.\./|[^)#]*/)[^)]*\.md', text), [], doc.name)
            if len(text.splitlines()) > 300:
                self.assertIn('## Contents', text, doc.name)

    def test_the_skill_folder_carries_its_licence(self):
        self.assertEqual((SKILL / 'LICENSE').read_bytes(), (ROOT / 'LICENSE').read_bytes())

    def test_examples_validate(self):
        examples = sorted((SKILL / 'examples').glob('*.json'))
        self.assertTrue(examples)
        for path in examples:
            project.validate(load(path))

    def test_numpy_is_pinned_by_hash(self):
        lines = [l.strip() for l in (SKILL / 'requirements.txt').read_text(encoding='utf-8').splitlines()
                 if l.strip() and not l.lstrip().startswith('#')]
        entries, current = [], []
        for line in lines:
            current.append(line.rstrip('\\').strip())
            if not line.endswith('\\'):
                entries.append(' '.join(current))
                current = []
        self.assertEqual((current, len(entries)), ([], 2))
        for entry in entries:
            self.assertRegex(entry, r'^numpy==\d+\.\d+\.\d+; python_version [<>]=? "3\.\d+" ')
            self.assertGreaterEqual(len(re.findall(r'--hash=sha256:[0-9a-f]{64}', entry)), 20)


class Manifests(unittest.TestCase):
    def test_every_manifest_gives_one_version(self):
        _, front = frontmatter()
        claude, codex = load(ROOT / '.claude-plugin' / 'plugin.json'), load(ROOT / '.codex-plugin' / 'plugin.json')
        self.assertEqual({front['metadata']['version'], claude['version'], codex['version'], VERSION}, {VERSION})
        for key in ('name', 'version', 'description', 'author', 'homepage', 'repository', 'license', 'keywords'):
            self.assertEqual(codex[key], claude[key], key)

    def test_the_manifests_point_at_what_exists(self):
        claude, codex = load(ROOT / '.claude-plugin' / 'plugin.json'), load(ROOT / '.codex-plugin' / 'plugin.json')
        for path in claude['skills']:
            self.assertTrue((ROOT / path / 'SKILL.md').is_file(), path)
        ui = codex['interface']
        self.assertLessEqual(len(ui['displayName']), 30)
        self.assertLessEqual(len(ui['shortDescription']), 30)
        self.assertLessEqual(len(ui['longDescription']), 4000)
        self.assertTrue(len(ui['defaultPrompt']) <= 3 and all(len(p) <= 128 for p in ui['defaultPrompt']))
        self.assertRegex(ui['brandColor'], r'^#[0-9A-Fa-f]{6}$')
        markets = load(ROOT / '.claude-plugin' / 'marketplace.json'), load(ROOT / '.agents' / 'plugins' / 'marketplace.json')
        for market in markets:
            self.assertEqual((market['name'], [p['name'] for p in market['plugins']]), (claude['name'], [claude['name']]))

    def test_icons_are_square_and_large_enough(self):
        claude, codex = load(ROOT / '.claude-plugin' / 'plugin.json'), load(ROOT / '.codex-plugin' / 'plugin.json')
        for rel in (claude['icon'], codex['interface']['composerIcon'], codex['interface']['logo']):
            w, h = image_size(ROOT / rel)
            self.assertTrue(w == h >= 48, (rel, w, h))
        w, _ = image_size(ROOT / claude['icon'])
        self.assertTrue(512 <= w <= 2048)
        self.assertLess((ROOT / claude['icon']).stat().st_size, 2_000_000)

    @unittest.skipIf(yaml is None, 'needs PyYAML')
    def test_the_skill_carries_its_own_icons(self):
        ui = yaml.safe_load((SKILL / 'agents' / 'openai.yaml').read_text(encoding='utf-8'))['interface']
        for key, brand in (('icon_small', 'icon-small.svg'), ('icon_large', 'icon-512.png')):
            self.assertEqual((SKILL / ui[key]).read_bytes(), (ROOT / 'assets' / 'brand' / brand).read_bytes(), key)


class Documents(unittest.TestCase):
    def markdown(self):
        return [*ROOT.glob('*.md'), *SKILL.rglob('*.md')]

    def test_links_resolve(self):
        bad = []
        for md in self.markdown():
            text = md.read_text(encoding='utf-8')
            for target in re.findall(r'\]\(([^)#\s]+)', text) + re.findall(r'(?:src|srcset|href)="([^"#]+)"', text):
                if not re.match(r'[a-z]+:', target) and not (md.parent / target).exists():
                    bad.append(f'{md.relative_to(ROOT)} -> {target}')
        self.assertEqual(bad, [])

    def test_section_links_resolve(self):
        def anchors(path):
            seen, found, code = {}, set(), False
            for line in path.read_text(encoding='utf-8').splitlines():
                code = code ^ line.startswith('```')
                if not code and re.match(r'#{1,6} ', line):
                    a = heading_anchor(line.lstrip('#'))
                    found.add(a if a not in seen else f'{a}-{seen[a]}')
                    seen[a] = seen.get(a, 0) + 1
            return found
        bad = []
        for md in self.markdown():
            for target, anchor in re.findall(r'\]\(([^)#\s]*)#([^)\s]+)\)', md.read_text(encoding='utf-8')):
                dest = md.parent / target if target else md
                if not re.match(r'[a-z]+:', target) and dest.suffix == '.md' and anchor not in anchors(dest):
                    bad.append(f'{md.relative_to(ROOT)} -> {target}#{anchor}')
        self.assertEqual(bad, [])

    def test_every_readme_offers_every_language(self):
        self.assertEqual(sorted(p.name for p in ROOT.glob('README*.md')), sorted(LANGUAGES))
        for name in LANGUAGES:
            text = (ROOT / name).read_text(encoding='utf-8')
            for other in LANGUAGES:
                if other != name:
                    self.assertIn(other, text, name)

    def test_every_readme_gives_the_install_commands(self):
        for name in LANGUAGES:
            text = (ROOT / name).read_text(encoding='utf-8')
            self.assertIn('/plugin marketplace add owoevan-dev/sfx-skill', text, name)
            self.assertIn('codex plugin marketplace add owoevan-dev/sfx-skill', text, name)
            self.assertIn('codex plugin add sfx-skill@sfx-skill', text, name)
            self.assertIn('/plugin install sfx-skill@sfx-skill', text, name)


class Files(unittest.TestCase):
    def test_no_personal_data(self):
        hits = []
        for p in repository_files():
            if p.suffix not in TEXT or p.resolve() == Path(__file__).resolve():
                continue
            for i, line in enumerate(p.read_text(encoding='utf-8', errors='replace').splitlines(), 1):
                if any(b.search(line) for b in PERSONAL):
                    hits.append(f'{p.relative_to(ROOT)}:{i}: {line.strip()[:100]}')
        self.assertEqual(hits, [])

    def test_no_hidden_characters(self):
        """A character that does not show can make a viewer reorder the text around it (Trojan Source, CVE-2021-42574)."""
        hits = []
        for p in repository_files():
            if p.suffix not in TEXT:
                continue
            for i, line in enumerate(p.read_text(encoding='utf-8', errors='replace').split('\n'), 1):
                bad = sorted({f'U+{ord(c):04X}' for c in line.removesuffix('\r')
                              if unicodedata.category(c) in ('Cc', 'Cf', 'Zl', 'Zp', 'Co', 'Cs') and c != '\t'})
                if bad:
                    hits.append(f'{p.relative_to(ROOT)}:{i}: {", ".join(bad)}')
        self.assertEqual(hits, [])

    def test_files_fit_the_plugin_directory(self):
        """Every file under 5 MiB, and nothing binary but images."""
        for p in repository_files():
            self.assertLess(p.stat().st_size, 5 * 2 ** 20, p.name)
            if p.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.gif', '.webp'):
                self.assertNotIn(b'\0', p.read_bytes(), p.name)
        self.assertEqual([p.name for p in repository_files() if p.suffix.lower() in ('.mp3', '.wav', '.flac', '.ogg')], [])


if __name__ == '__main__':
    unittest.main()
