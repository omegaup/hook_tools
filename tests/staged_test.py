"""Regression tests for staged linting with real git repositories."""

import ast
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from typing import List, cast

from omegaup_hook_tools import git_tools


class TestStaged(unittest.TestCase):
    """Exercise staged selection and fixes through the command line."""

    def setUp(self) -> None:
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory)
        self.root = pathlib.Path(directory)
        self.git('init', '-q')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.com')
        self.git('config', 'commit.gpgsign', 'false')
        self.git('config', 'diff.renames', 'true')
        self.write('.lint.config.json',
                   '{"lint":{"whitespace":{"allowlist":[".*\\\\.txt$"]}}}')
        for name in ['modified.txt', 'deleted.txt',
                     'renamed.txt', 'plain.txt']:
            self.write(name, 'original\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'initial files')

    def git(self, *args: str) -> bytes:
        """Run git in the temporary repository."""
        return subprocess.run(['/usr/bin/git', *args], cwd=self.root,
                              check=True, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE).stdout

    def write(self, name: str, contents: str) -> None:
        """Write a working tree file."""
        (self.root / name).write_text(contents, encoding='utf-8')

    def cli(self, *args: str) -> 'subprocess.CompletedProcess[bytes]':
        """Run the real lint entrypoint without interactive prompts."""
        env = dict(os.environ)
        env['PYTHONPATH'] = str(pathlib.Path(git_tools.__file__).parents[1])
        return subprocess.run(
            [sys.executable, '-m', 'omegaup_hook_tools',
             '--continuous-integration', '--command-name', 'lint', *args],
            cwd=self.root, env=env, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, check=False)

    def selected(self) -> List[str]:
        """Parse staged arguments in the temporary repository."""
        env = dict(os.environ)
        env['PYTHONPATH'] = str(pathlib.Path(git_tools.__file__).parents[1])
        result = subprocess.run(
            [sys.executable, '-c',
             'from omegaup_hook_tools import git_tools; '
             'print(repr(git_tools.parse_arguments().files))',
             'validate', '--staged'], cwd=self.root, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        return cast(List[str], ast.literal_eval(result.stdout.decode()))

    def test_selection(self) -> None:
        """Select staged additions, edits and rename destinations only."""
        self.write('modified.txt', 'staged\n')
        self.write('added file.txt', 'added\n')
        self.git('add', 'modified.txt', 'added file.txt')
        self.git('rm', 'deleted.txt')
        self.git('mv', 'renamed.txt', 'new name.txt')
        self.write('plain.txt', 'unstaged\n')
        self.write('untracked.txt', 'untracked\n')
        self.assertEqual(self.selected(),
                         ['added file.txt', 'modified.txt', 'new name.txt'])
        self.assertEqual(self.cli('validate', '--staged').returncode, 0)

    def test_empty_index_diff(self) -> None:
        """Unstaged edits and untracked files are ignored."""
        self.write('plain.txt', 'bad  \n')
        self.write('untracked.txt', 'bad  \n')
        self.assertEqual(self.selected(), [])
        self.assertEqual(self.cli('validate', '--staged').returncode, 0)

    def test_initial_commit(self) -> None:
        """Staged files can be checked before the first commit."""
        self.git('checkout', '--orphan', 'unborn')
        self.assertEqual(self.cli('validate', '--staged').returncode, 0)

    def test_validate_reads_index(self) -> None:
        """Validation uses staged bytes in both directions."""
        for staged, working, status in [('clean\n', 'bad  \n', 0),
                                        ('bad  \n', 'clean\n', 1)]:
            with self.subTest(staged=staged):
                self.write('modified.txt', staged)
                self.git('add', 'modified.txt')
                self.write('modified.txt', working)
                result = self.cli('validate', '--staged')
                self.assertEqual(result.returncode, status, result.stderr)
                self.assertEqual(self.git('show', ':modified.txt'),
                                 staged.encode())
                self.assertEqual((self.root / 'modified.txt').read_bytes(),
                                 working.encode())

    def test_suggested_fix_keeps_staged(self) -> None:
        """The suggested command keeps the staged selection."""
        self.write('modified.txt', 'bad  \n')
        self.git('add', 'modified.txt')
        result = self.cli('validate', '--staged')
        self.assertEqual(result.returncode, 1)
        self.assertIn(b'lint fix --staged\n', result.stderr)

    def test_fix_rejects_partial_changes_before_writing(self) -> None:
        """One partial file prevents every write, including safe files."""
        self.write('modified.txt', 'staged  \n')
        self.write('added.txt', 'added  \n')
        self.git('add', 'modified.txt', 'added.txt')
        self.write('modified.txt', 'unstaged work\n')
        before = self.git('diff', '--binary', 'HEAD')
        index = self.git('write-tree')
        result = self.cli('fix', '--staged')
        self.assertEqual(result.returncode, 2)
        self.assertIn(b'working tree differs from the index', result.stderr)
        self.assertEqual(self.git('diff', '--binary', 'HEAD'), before)
        self.assertEqual(self.git('write-tree'), index)

    def test_fix_rejects_missing_working_file(self) -> None:
        """An unstaged deletion is not recreated by the formatter."""
        self.write('modified.txt', 'staged  \n')
        self.git('add', 'modified.txt')
        (self.root / 'modified.txt').unlink()
        self.assertEqual(self.cli('fix', '--staged').returncode, 2)
        self.assertFalse((self.root / 'modified.txt').exists())

    def test_fix_only_writes_selected_files(self) -> None:
        """Safe fixes update the working tree while leaving the index alone."""
        self.write('modified.txt', 'staged  \n')
        self.git('add', 'modified.txt')
        self.write('plain.txt', 'unstaged  \n')
        index = self.git('write-tree')
        result = self.cli('fix', '--staged')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual((self.root / 'modified.txt').read_bytes(),
                         b'staged\n')
        self.assertEqual((self.root / 'plain.txt').read_bytes(),
                         b'unstaged  \n')
        self.assertEqual(self.git('write-tree'), index)

    def test_incompatible_arguments(self) -> None:
        """Reject other selection modes for both commands."""
        for tool in ['validate', 'fix']:
            for extra in [['--all-files'], ['HEAD'], ['--', 'plain.txt']]:
                with self.subTest(tool=tool, extra=extra):
                    result = self.cli(tool, '--staged', *extra)
                    self.assertEqual(result.returncode, 1)
                    self.assertIn(b'incompatible', result.stderr)

    def test_fix_rejects_symlink(self) -> None:
        """A working tree symlink must not redirect writes to another file."""
        self.write('modified.txt', 'staged  \n')
        self.git('add', 'modified.txt')
        self.write('target.txt', 'staged  \n')
        (self.root / 'modified.txt').unlink()
        (self.root / 'modified.txt').symlink_to('target.txt')
        self.assertEqual(self.cli('fix', '--staged').returncode, 2)
        self.assertEqual((self.root / 'target.txt').read_bytes(),
                         b'staged  \n')

    def test_submodule_is_not_selected(self) -> None:
        """Gitlinks are not files to lint."""
        commit = self.git('rev-parse', 'HEAD').decode().strip()
        self.git('update-index', '--add', '--cacheinfo',
                 f'160000,{commit},submodule')
        self.assertEqual(self.selected(), [])

    def test_validate_reads_removed_working_file(self) -> None:
        """Validation can read a staged file removed only from disk."""
        self.write('modified.txt', 'clean staged content\n')
        self.git('add', 'modified.txt')
        (self.root / 'modified.txt').unlink()
        self.assertEqual(self.cli('validate', '--staged').returncode, 0)
        self.assertFalse((self.root / 'modified.txt').exists())
