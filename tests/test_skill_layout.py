import ast
import json
import re
import unittest
from urllib.parse import unquote, urlsplit

import yaml

from support import ROOT, SCRIPTS, SKILL
from config import env_names, read_env_file


class SkillLayoutTests(unittest.TestCase):
    def test_frontmatter_and_single_skill(self):
        text = (SKILL / 'SKILL.md').read_text(encoding='utf-8')
        self.assertTrue(text.startswith('---\n'))
        frontmatter = yaml.safe_load(text.split('---', 2)[1])
        self.assertEqual(frontmatter['name'], SKILL.name)
        self.assertRegex(frontmatter['name'], r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
        self.assertLessEqual(len(frontmatter['name']), 64)
        for key, maximum in (('description', 1024), ('compatibility', 500)):
            self.assertIsInstance(frontmatter[key], str)
            self.assertGreater(len(frontmatter[key]), 0)
            self.assertLessEqual(len(frontmatter[key]), maximum)
        self.assertEqual(frontmatter['license'], 'MIT')
        self.assertLess(len(text.splitlines()), 500)
        self.assertEqual(list((ROOT / 'skills').rglob('SKILL.md')), [SKILL / 'SKILL.md'])
        self.assertFalse((ROOT / '.agents' / 'skills' / 'camofox-client').exists())

    def test_local_markdown_links_exist_and_skill_is_self_contained(self):
        documents = [*ROOT.glob('*.md'), *(ROOT / 'docs').rglob('*.md'), *SKILL.rglob('*.md')]
        for document in documents:
            text = document.read_text(encoding='utf-8')
            for target in re.findall(r'\[[^\]]*\]\(([^)]+)\)', text):
                parsed = urlsplit(target)
                if parsed.scheme or target.startswith('#'):
                    continue
                path = (document.parent / unquote(parsed.path)).resolve()
                with self.subTest(document=document.relative_to(ROOT), target=target):
                    self.assertTrue(path.exists(), f'Missing link: {target}')
                    if SKILL in document.parents:
                        self.assertTrue(path == SKILL or SKILL in path.parents, 'Skill links outside installation')

    def test_license_instructions_and_safe_env_example(self):
        self.assertEqual((ROOT / 'LICENSE').read_bytes(), (SKILL / 'LICENSE').read_bytes())
        self.assertEqual((ROOT / 'CLAUDE.md').read_text(encoding='utf-8').strip(), '@AGENTS.md')
        values = read_env_file(ROOT / '.env.example')
        self.assertEqual(set(values), env_names())
        self.assertEqual(values['CAMOFOX_USER_ID'], '')
        self.assertEqual(values['CAMOFOX_ACCESS_KEY'], '')
        self.assertEqual(values['CAMOFOX_API_KEY'], '')
        ignores = (ROOT / '.gitignore').read_text(encoding='utf-8').splitlines()
        self.assertNotIn('.agents/', ignores)
        self.assertNotIn('.claude/', ignores)
        self.assertIn('.env', ignores)

    def test_module_dependencies_are_one_way(self):
        allowed = {
            'actions': set(), 'config': set(), 'errors': set(), 'deadline': set(), 'transport': {'deadline', 'errors'},
            'client': {'actions', 'errors', 'transport'}, 'observe': {'actions', 'errors'},
            'doctor': {'errors'},
            'main': {'actions', 'client', 'config', 'doctor', 'errors', 'observe', 'transport'},
        }
        modules = {file.stem for file in SCRIPTS.glob('*.py')}
        self.assertEqual(modules, set(allowed))
        for file in SCRIPTS.glob('*.py'):
            tree = ast.parse(file.read_text(encoding='utf-8'))
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name.split('.')[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module.split('.')[0])
            with self.subTest(module=file.stem):
                self.assertLessEqual(imported & modules, allowed[file.stem])
                if file.stem in {'client', 'observe', 'doctor', 'transport', 'actions'}:
                    self.assertFalse(imported & {'os', 'pathlib', 'argparse', 'main'})

    def test_release_config_and_workflows_parse(self):
        config = json.loads((ROOT / 'release-please-config.json').read_text(encoding='utf-8'))
        package = config['packages']['.']
        self.assertEqual(package['release-type'], 'simple')
        self.assertEqual(package['version-file'], 'version.txt')
        self.assertFalse(package['include-component-in-tag'])
        manifest = json.loads((ROOT / '.release-please-manifest.json').read_text(encoding='utf-8'))
        version = (ROOT / 'version.txt').read_text(encoding='utf-8').strip()
        self.assertRegex(version, r'^\d+\.\d+\.\d+$')
        self.assertEqual(manifest.get('.', package['initial-version']), version)
        # BaseLoader preserves the YAML 1.2 workflow key "on" rather than treating it as a boolean.
        ci = yaml.load((ROOT / '.github/workflows/ci.yml').read_text(encoding='utf-8'), Loader=yaml.BaseLoader)
        release = yaml.load((ROOT / '.github/workflows/release.yml').read_text(encoding='utf-8'), Loader=yaml.BaseLoader)
        self.assertIn('workflow_call', ci['on'])
        self.assertEqual(release['on']['push']['branches'], ['main'])
        self.assertEqual(release['jobs']['check']['uses'], './.github/workflows/ci.yml')
        self.assertEqual(release['jobs']['release']['needs'], 'check')
