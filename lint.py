#!/usr/bin/python3
# -*- coding: utf-8 -*-

"""Main entrypoint for the omegaUp linting tool."""

from __future__ import print_function

import json
import os.path
import re
import sys

import linters
import git_tools


_LINTER_MAPPING = {
    'whitespace': linters.WhitespaceLinter,
    'javascript': linters.JavaScriptLinter,
    'html': linters.HTMLLinter,
    'vue': linters.VueLinter,
    'php': linters.PHPLinter,
}


def run_linter(args, linter, files, validate_only):
    '''Runs the linter against all files.'''
    root = git_tools.root_dir()
    file_violations = set()
    for filename in files:
        contents = git_tools.file_contents(args, root, filename)
        try:
            new_contents, violations = linter.run_one(filename, contents)
        except linters.LinterException as lex:
            print('File %s%s%s lint failed:\n%s' %
                  (git_tools.COLORS.FAIL, filename,
                   git_tools.COLORS.NORMAL, lex.message.decode('utf-8')),
                  file=sys.stderr)
            file_violations.add(filename)
            continue
        if contents != new_contents:
            file_violations.add(filename)
            violations_message = ', '.join(
                '%s%s%s' %
                (git_tools.COLORS.FAIL, violation, git_tools.COLORS.NORMAL)
                for violation in violations)
            if validate_only:
                print('File %s%s%s lint failed: %s' %
                      (git_tools.COLORS.HEADER, filename,
                       git_tools.COLORS.NORMAL, violations_message),
                      file=sys.stderr)
            else:
                print('Fixing %s%s%s' %
                      (git_tools.COLORS.HEADER, filename,
                       git_tools.COLORS.NORMAL),
                      file=sys.stderr)
                with open(os.path.join(root, filename), 'wb') as outfile:
                    outfile.write(new_contents)
    return file_violations


def main():
    '''Runs the linters against the chosen files.'''

    args = git_tools.parse_arguments(tool_description='purges whitespace')
    if not args.files:
        return 0

    # If running in an automated environment, we can close stdin.
    # This will disable all prompts.
    if (args.continuous_integration or
            os.environ.get('CONTINUOUS_INTEGRATION') == 'true'):
        sys.stdin.close()

    validate_only = args.tool == 'validate'

    with open(args.config_file, 'r') as config_file:
        config = json.load(config_file)

    file_violations = set()

    for linter, options in config['lint'].items():
        if linter not in _LINTER_MAPPING:
            print('Unknown linter %s%s%s.' %
                  (git_tools.COLORS.FAIL, linter, git_tools.COLORS.NORMAL),
                  file=sys.stderr)
            sys.exit(1)

        filtered_files = args.files

        # Filter only the files in the whitelist.
        whitelist = [re.compile(r) for r in options.get('whitelist', [])]
        filtered_files = [
            filename for filename in filtered_files
            if any(r.match(filename) for r in whitelist)]

        # And not in the blacklist.
        blacklist = [re.compile(r) for r in options.get('blacklist', [])]
        filtered_files = [
            filename for filename in filtered_files
            if all(not r.match(filename) for r in blacklist)]

        file_violations |= run_linter(args, _LINTER_MAPPING[linter](options),
                                      filtered_files, validate_only)

    if file_violations:
        if validate_only:
            if git_tools.attempt_automatic_fixes(sys.argv[0], args,
                                                 file_violations):
                sys.exit(1)
            print('%sLinter validation errors.%s '
                  'Please run `%s` to fix them.' % (
                      git_tools.COLORS.FAIL, git_tools.COLORS.NORMAL,
                      git_tools.get_fix_commandline(sys.argv[0], args,
                                                    file_violations)),
                  file=sys.stderr)
        else:
            print('Files written to working directory. '
                  '%sPlease commit them before pushing.%s' % (
                      git_tools.COLORS.HEADER, git_tools.COLORS.NORMAL),
                  file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()

# vim: tabstop=4 expandtab shiftwidth=4 softtabstop=4
