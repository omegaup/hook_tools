#!/usr/bin/env python3

"""Unit tests for git_tools."""

from __future__ import print_function

import os
import sys
import unittest

if __name__ == "__main__" and __package__ is None:
    sys.path.append(os.path.dirname(sys.path[0]))
    __package__ = "hook_tools"  # pylint: disable=redefined-builtin

from hook_tools import git_tools  # pylint: disable=E0402,C0413


class TestGitTools(unittest.TestCase):
    """Tests git tools."""

    def test_get_explicit_file_list(self) -> None:
        """Tests git_tools.get_explicit_file_list()."""

        for commits, expected in [
                # Explicit separator.
                (['--', 'HEAD', 'foo'], ([], ['HEAD', 'foo'])),
                (['HEAD', '--', 'foo'], (['HEAD'], ['foo'])),
                (['HEAD', 'foo', '--'], (['HEAD', 'foo'], [])),

                # Sniffing.
                (['HEAD'], (['HEAD'], [])),
                (['foo'], ([], ['foo'])),
                (['HEAD', 'foo'], (['HEAD'], ['foo'])),
                (['HEAD', 'foo', 'HEAD'], (['HEAD'], ['foo', 'HEAD'])),
                (['HEAD', 'HEAD', 'foo'], (['HEAD', 'HEAD'], ['foo'])),
        ]:
            files = git_tools.get_explicit_file_list(commits)
            self.assertEqual((commits, files), expected)


if __name__ == '__main__':
    unittest.main()

# vim: tabstop=4 expandtab shiftwidth=4 softtabstop=4
