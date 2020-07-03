#!/usr/bin/python3
# -*- coding: utf-8 -*-

"""Unit tests for the Linters."""

from __future__ import print_function

import os
import sys
import unittest

if __name__ == "__main__" and __package__ is None:
    sys.path.append(os.path.dirname(sys.path[0]))
    __package__ = "hook_tools"  # pylint: disable=redefined-builtin

from hook_tools import linters  # pylint: disable=E0402,C0413


class TestLinters(unittest.TestCase):
    """Tests the linters."""

    def test_whitespace(self) -> None:
        """Tests WhitespaceLinter."""

        linter = linters.WhitespaceLinter()

        new_contents, violations = linter.run_one('test.txt',
                                                  b'Hello\r\nWorld!\n')
        self.assertEqual(new_contents, b'Hello\nWorld!\n')
        self.assertEqual(violations, ['Windows-style EOF'])

        new_contents, violations = linter.run_one('test.txt',
                                                  b'Hello\n\n\nWorld!\n')
        self.assertEqual(new_contents, b'Hello\n\nWorld!\n')
        self.assertEqual(violations, ['consecutive empty lines'])

        new_contents, violations = linter.run_one('test.txt',
                                                  b'function() {\n\n}\n')
        self.assertEqual(new_contents, b'function() {\n}\n')
        self.assertEqual(violations, ['empty lines after an opening brace'])

        new_contents, violations = linter.run_one('test.txt',
                                                  b'function() {\n//\n\n}\n')
        self.assertEqual(new_contents, b'function() {\n//\n}\n')
        self.assertEqual(violations, ['empty lines before a closing brace'])

        new_contents, violations = linter.run_one(
            'test.txt', b'function() {\r\n\n\n// \n\n}\n')
        self.assertEqual(new_contents, b'function() {\n//\n}\n')
        self.assertEqual(violations, [
            'Windows-style EOF',
            'trailing whitespace',
            'consecutive empty lines',
            'empty lines after an opening brace',
            'empty lines before a closing brace',
        ])

    def test_command(self) -> None:
        """Tests CommandLinter."""

        linter = linters.CommandLinter({
            'commands': ['python3 test/uppercase_linter.py'],
        })

        new_contents, violations = linter.run_one('test.txt',
                                                  b'Hello, World!\n')
        self.assertEqual(new_contents, b'HELLO, WORLD!\n')
        self.assertEqual(violations, ['command'])

    @unittest.skipIf(os.environ.get('TRAVIS') == 'true', 'Travis CI')
    def test_javascript(self) -> None:
        """Tests JavaScriptLinter."""

        linter = linters.JavaScriptLinter()

        new_contents, violations = linter.run_one(
            'test.js', b'  function x ( ){a;b;c;};\n')
        self.assertEqual(new_contents,
                         b'function x() {\n  a;\n  b;\n  c;\n}\n')
        self.assertEqual(violations, ['javascript'])

        new_contents, violations = linter.run_one(
            'test.js', b'#!/usr/bin/node\nreturn;\n')
        self.assertEqual(new_contents, b'#!/usr/bin/node\nreturn;\n')
        self.assertEqual(violations, ['javascript'])

    @unittest.skipIf(os.environ.get('TRAVIS') == 'true', 'Travis CI')
    def test_vue(self) -> None:
        """Tests VueLinter."""

        linter = linters.VueLinter()

        with self.assertRaisesRegex(linters.LinterException,
                                    r'Unclosed tag at line 2, column 3'):
            linter.run_one('test.vue',
                           b'<template>\n<b></span>\n</template>\n')

        new_contents, violations = linter.run_one(
            'test.vue', b'<template>\n<b></b>\n</template>\n')
        self.assertEqual(new_contents,
                         b'<template>\n  <b></b>\n</template>\n')
        self.assertEqual(violations, ['vue'])

    @unittest.skipIf(os.environ.get('TRAVIS') == 'true', 'Travis CI')
    def test_html(self) -> None:
        """Tests HTMLLinter."""

        linter = linters.HTMLLinter()

        new_contents, violations = linter.run_one(
            'test.html',
            b'<!DOCTYPE html>\n<html><head><title /></head>'
            b'<body>\n<input/></body></html>\n')
        self.assertEqual(
            new_contents,
            b'<!DOCTYPE html>\n<html>\n  <head>\n    <title></title>\n'
            b'  </head>\n  <body>\n    <input />\n  </body>\n</html>\n')
        self.assertEqual(violations, ['html'])

    @unittest.skipIf(os.environ.get('TRAVIS') == 'true', 'Travis CI')
    def test_php(self) -> None:
        """Tests PHPLinter."""

        linter = linters.PHPLinter()

        new_contents, violations = linter.run_one(
            'test.php', b'<?php\necho array("foo");')
        self.assertEqual(
            new_contents,
            b'<?php\necho [\'foo\'];\n')
        self.assertEqual(violations, ['php'])

    def test_python(self) -> None:
        """Tests PythonLinter."""

        linter = linters.PythonLinter()

        with self.assertRaisesRegex(linters.LinterException, r'.*\bE111\b.*'):
            linter.run_one(
                'test.py', b'def main():\n  pass\n')


if __name__ == '__main__':
    unittest.main()

# vim: tabstop=4 expandtab shiftwidth=4 softtabstop=4
