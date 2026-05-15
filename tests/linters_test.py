#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Unit tests for the Linters."""

from __future__ import print_function

import subprocess
import unittest
from unittest import mock

from omegaup_hook_tools import linters


class TestLinters(unittest.TestCase):
    """Tests the linters."""

    def test_whitespace(self) -> None:
        """Tests WhitespaceLinter."""

        linter = linters.WhitespaceLinter()

        self.assertEqual(
            linter.run_one('test.txt', b'Hello\r\nWorld!\n'),
            linters.SingleResult(b'Hello\nWorld!\n', ['Windows-style EOF']))

        self.assertEqual(
            linter.run_one('test.txt', b'Hello\n\n\nWorld!\n'),
            linters.SingleResult(b'Hello\n\nWorld!\n',
                                 ['consecutive empty lines']))

        self.assertEqual(
            linter.run_one('test.txt', b'function() {\n\n}\n'),
            linters.SingleResult(b'function() {\n}\n',
                                 ['empty lines after an opening brace']))

        self.assertEqual(
            linter.run_one('test.txt', b'function() {\n//\n\n}\n'),
            linters.SingleResult(b'function() {\n//\n}\n',
                                 ['empty lines before a closing brace']))

        self.assertEqual(
            linter.run_one('test.txt', 'hello \u200b\n'.encode('utf-8')),
            linters.SingleResult(b'hello\n', [
                'trailing whitespace',
            ]))

        self.assertEqual(
            linter.run_one('test.txt', b'function() {\r\n\n\n// \n\n}\n'),
            linters.SingleResult(b'function() {\n//\n}\n', [
                'Windows-style EOF',
                'trailing whitespace',
                'consecutive empty lines',
                'empty lines after an opening brace',
                'empty lines before a closing brace',
            ]))

    def test_command(self) -> None:
        """Tests CommandLinter."""

        linter = linters.CommandLinter({
            'commands': ['python3 tests/data/uppercase_linter.py'],
        })

        self.assertEqual(linter.run_one('test.txt', b'Hello, World!\n'),
                         linters.SingleResult(b'HELLO, WORLD!\n', ['command']))

    def test_javascript(self) -> None:
        """Tests JavaScriptLinter."""

        linter = linters.JavaScriptLinter()

        self.assertEqual(
            linter.run_one('test.js', b'  function x ( ){a;b;c;};\n'),
            linters.SingleResult(b'function x() {\n  a;\n  b;\n  c;\n}\n',
                                 ['javascript']))

        self.assertEqual(
            linter.run_one('test.js', b'#!/usr/bin/node\nreturn;\n'),
            linters.SingleResult(b'#!/usr/bin/node\nreturn;\n',
                                 ['javascript']))

    def test_vue(self) -> None:
        """Tests VueLinter."""

        linter = linters.VueLinter()

        with self.assertRaisesRegex(linters.LinterException,
                                    r'Found Vue errors') as lex:
            linter.run_one('test.vue',
                           b'<template>\n<b></span>\n</template>\n')
        self.assertEqual(lex.exception.diagnostics, [
            linters.Diagnostic(message='Unclosed tag',
                               filename='test.vue',
                               line='<b></span>',
                               lineno=2,
                               col=4),
            linters.Diagnostic('Unclosed tag',
                               filename='test.vue',
                               line='</template>',
                               lineno=3,
                               col=1),
        ])

        self.assertEqual(
            linter.run_one('test.vue', b'<template>\n<b></b>\n</template>\n'),
            linters.SingleResult(b'<template>\n  <b></b>\n</template>\n',
                                 ['vue']))

    def test_html(self) -> None:
        """Tests HTMLLinter."""

        linter = linters.HTMLLinter()

        self.assertEqual(
            linter.run_one(
                'test.html', b'<!DOCTYPE html>\n<html><head><title /></head>'
                b'<body>\n<input/></body></html>\n'),
            linters.SingleResult(
                b'<!DOCTYPE html>\n<html>\n  <head>\n    <title></title>\n'
                b'  </head>\n  <body>\n    <input />\n  </body>\n</html>\n',
                ['html']))

    def test_php(self) -> None:
        """Tests PHPLinter."""

        linter = linters.PHPLinter()

        self.assertEqual(
            linter.run_one('test.php', b'<?php\necho array("foo");'),
            linters.SingleResult(b'<?php\necho [\'foo\'];\n', ['php']))

    def test_python(self) -> None:
        """Tests PythonLinter."""

        linter = linters.PythonLinter()

        with self.assertRaisesRegex(linters.LinterException,
                                    'Python lint errors') as lex:
            linter.run_one('test.py', b'def main():\n  pass\n')
        self.assertEqual(lex.exception.diagnostics, [
            linters.Diagnostic(
                message=('[pylint] C0114(missing-module-docstring) '
                         'Missing module docstring'),
                filename='test.py',
                line='def main():',
                lineno=1,
            ),
            linters.Diagnostic(
                message=('[pylint] C0116(missing-function-docstring) '
                         'Missing function or method docstring'),
                filename='test.py',
                line='def main():',
                lineno=1,
            ),
            linters.Diagnostic(
                message=('[pycodestyle] E111 indentation is not a '
                         'multiple of 4'),
                filename='test.py',
                line='  pass',
                lineno=2,
                col=3,
            ),
            linters.Diagnostic(
                message=('[pylint] W0311(bad-indentation) '
                         'Bad indentation. Found 2 spaces, expected 4'),
                filename='test.py',
                line='  pass',
                lineno=2,
            ),
        ])

    @mock.patch('omegaup_hook_tools.linters._which', return_value='sqlfluff')
    @mock.patch('omegaup_hook_tools.linters.subprocess.run')
    def test_sql_fix(self, mock_run: mock.Mock,
                     mock_which: mock.Mock) -> None:
        """Tests SqlFluffLinter automatic fixes."""
        del mock_which

        expected = b'SELECT 1;\n'

        def _side_effect(args, **kwargs):  # type: ignore[no-untyped-def]
            del kwargs
            if args[1] == 'fix':
                with open(args[-1], 'wb') as sql_file:
                    sql_file.write(expected)
                return subprocess.CompletedProcess(args=args,
                                                   returncode=0,
                                                   stdout='')
            return subprocess.CompletedProcess(args=args, returncode=0,
                                               stdout='[]')

        mock_run.side_effect = _side_effect

        linter = linters.SqlFluffLinter({'dialect': 'mysql'})
        self.assertEqual(
            linter.run_one('test.sql', b'SELECT  1;\n'),
            linters.SingleResult(expected, ['sql']))

    @mock.patch('omegaup_hook_tools.linters._which', return_value='sqlfluff')
    @mock.patch('omegaup_hook_tools.linters.subprocess.run')
    def test_sql_lint_errors(self, mock_run: mock.Mock,
                             mock_which: mock.Mock) -> None:
        """Tests SqlFluffLinter diagnostics on unfixable lint errors."""
        del mock_which

        def _side_effect(args, **kwargs):  # type: ignore[no-untyped-def]
            del kwargs
            if args[1] == 'fix':
                return subprocess.CompletedProcess(args=args,
                                                   returncode=0,
                                                   stdout='')
            output = (
                '[{"filepath":"/tmp/test.sql","violations":['
                '{"code":"LT01","description":"Unexpected spacing",'
                '"line_no":1,"line_pos":9}]}]')
            return subprocess.CompletedProcess(args=args,
                                               returncode=1,
                                               stdout=output)

        mock_run.side_effect = _side_effect

        linter = linters.SqlFluffLinter({'dialect': 'mysql'})
        with self.assertRaisesRegex(linters.LinterException,
                        r'sqlfluff lint errors') as lex:
            linter.run_one('test.sql', b'SELECT  1;\n')
        self.assertTrue(lex.exception.diagnostics)
        self.assertIn('Unexpected spacing',
                  lex.exception.diagnostics[0].message)


if __name__ == '__main__':
    unittest.main()

# vim: tabstop=4 expandtab shiftwidth=4 softtabstop=4
