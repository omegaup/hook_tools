#!/usr/bin/python3
# -*- coding: utf-8 -*-

"""Main entrypoint for the omegaUp linting tool."""

from __future__ import print_function

import argparse
import json
import logging
import multiprocessing
import os.path
import pipes
import re
import sys
from typing import (Any, Callable, Dict, Iterator, List, Mapping, Optional,
                    Sequence, Set, Text, Tuple)

if __name__ == "__main__" and __package__ is None:
    sys.path.append(os.path.dirname(sys.path[0]))
    __package__ = "hook_tools"  # pylint: disable=redefined-builtin

from hook_tools import linters  # pylint: disable=E0402,C0413
from hook_tools import git_tools  # pylint: disable=E0402,C0413

LinterFactory = Callable[..., linters.Linter]

_LINTER_MAPPING: Dict[Text, LinterFactory] = {
    'whitespace': linters.WhitespaceLinter,
    'javascript': linters.JavaScriptLinter,
    'typescript': linters.TypeScriptLinter,
    'html': linters.HTMLLinter,
    'vue': linters.VueLinter,
    'php': linters.PHPLinter,
    'python': linters.PythonLinter,
}

_ROOT = git_tools.root_dir()


def _get_command_name() -> List[Text]:
    '''Returns the name of the command needed to invoke this script.'''
    if os.environ.get('DOCKER') == 'true':
        return [
            '/usr/bin/docker', 'run', '--rm', '-v', '"$PWD:/src"', '-v',
            '"$PWD:$PWD"', 'omegaup/hook_tools'
        ]
    return [pipes.quote(sys.argv[0])]


def _run_linter_one(linter: linters.Linter, filename: Text, contents: bytes,
                    validate_only: bool) -> Tuple[Optional[Text], bool]:
    '''Runs the linter against one file.'''
    try:
        new_contents, violations = linter.run_one(filename, contents)
    except linters.LinterException as lex:
        print('File %s%s%s lint failed:\n%s' %
              (git_tools.COLORS.FAIL, filename,
               git_tools.COLORS.NORMAL, lex.message),
              file=sys.stderr)
        return filename, lex.fixable

    if contents == new_contents:
        return None, False

    return _report_linter_results(filename, new_contents, validate_only,
                                  violations, True)


def _run_linter_all(args: argparse.Namespace, linter: linters.Linter,
                    files: Sequence[Text], validate_only: bool
                    ) -> Sequence[Tuple[Optional[Text], bool]]:
    try:
        new_file_contents, original_contents, violations = linter.run_all(
            files, lambda filename: git_tools.file_contents(args, _ROOT,
                                                            filename))
    except linters.LinterException as lex:
        print('Files %s%s%s lint failed:\n%s' %
              (git_tools.COLORS.FAIL, ', '.join(files),
               git_tools.COLORS.NORMAL, lex.message),
              file=sys.stderr)
        return [(filename, lex.fixable) for filename in files]

    result: List[Tuple[Optional[Text], bool]] = []
    for filename in new_file_contents:
        if original_contents[filename] == new_file_contents[filename]:
            result.append((None, False))
        else:
            result.append(_report_linter_results(filename,
                                                 new_file_contents[filename],
                                                 validate_only, violations,
                                                 True))
    return result


def _report_linter_results(filename: Text, new_contents: bytes, validate: bool,
                           violations: Sequence[Text],
                           fixable: bool) -> Tuple[Text, bool]:
    violations_message = ', '.join(
        '%s%s%s' %
        (git_tools.COLORS.FAIL, violation, git_tools.COLORS.NORMAL)
        for violation in violations)
    if validate:
        print('File %s%s%s lint failed: %s' %
              (git_tools.COLORS.HEADER, filename,
               git_tools.COLORS.NORMAL, violations_message),
              file=sys.stderr)
    else:
        print('Fixing %s%s%s' %
              (git_tools.COLORS.HEADER, filename,
               git_tools.COLORS.NORMAL),
              file=sys.stderr)
        with open(os.path.join(_ROOT, filename), 'wb') as outfile:
            outfile.write(new_contents)
    return filename, fixable


def _run_linter(args: argparse.Namespace, linter: linters.Linter,
                filenames: Sequence[Text],
                validate_only: bool) -> Tuple[Set[Text], bool]:
    '''Runs the linter against all files.'''
    logging.debug('%s: Files to consider: %s',
                  linter.name, ' '.join(filenames))
    logging.debug('%s: Running with %d threads', linter.name, args.jobs)
    files = dict((filename, git_tools.file_contents(args, _ROOT, filename))
                 for filename in filenames)
    results = multiprocessing.Pool(
        args.jobs).starmap(_run_linter_one, [(linter, filename, contents,
                                              validate_only)
                                             for filename,
                                             contents in files.items()])
    results.extend(_run_linter_all(args, linter, filenames, validate_only))
    return (set(violation for violation, _ in results
                if violation is not None),
            any(fixable for _, fixable in results))


def _get_enabled_linters(
        config: Mapping[Text, Any], config_file_path: Text,
        linter_whitelist: Text
) -> Iterator[Tuple[LinterFactory, Mapping[Text, Any]]]:
    '''Loads any custom linters.'''
    available_linters = dict(_LINTER_MAPPING)

    for custom_linter in config.get('custom_linters', []):
        available_linters[custom_linter['name']] = linters.CustomLinter(
            custom_linter, config_file_path)

    final_linter_whitelist = set(available_linters.keys())

    if linter_whitelist:
        args_linters = set(linter_whitelist.split(','))
        unknown_linters = args_linters - final_linter_whitelist
        if unknown_linters:
            print('Unknown linters %s%s%s.' %
                  (git_tools.COLORS.FAIL, ', '.join(unknown_linters),
                   git_tools.COLORS.NORMAL),
                  file=sys.stderr)
            sys.exit(1)
        final_linter_whitelist = args_linters

    unknown_linters = set(config['lint']) - set(available_linters)
    if unknown_linters:
        print(
            'Unknown linters %s%s%s.' %
            (git_tools.COLORS.FAIL, ', '.join(unknown_linters),
             git_tools.COLORS.NORMAL),
            file=sys.stderr)
        sys.exit(1)

    for linter_name, options in config['lint'].items():
        if linter_name not in final_linter_whitelist:
            continue
        yield available_linters[linter_name], options


def main() -> None:
    '''Runs the linters against the chosen files.'''

    args = git_tools.parse_arguments(
        tool_description='lints a project',
        extra_arguments=[
            git_tools.Argument(
                '--pre-upload',
                action='store_true',
                help='Mark this as being run from within a pre-upload hook'),
            git_tools.Argument(
                '--linters', help='Comma-separated subset of linters to run'),
        ])
    if not args.files:
        return

    # If running in an automated environment, we can close stdin.
    # This will disable all prompts.
    if (args.continuous_integration
            or os.environ.get('CONTINUOUS_INTEGRATION') == 'true'):
        sys.stdin.close()

    validate_only = args.tool == 'validate'

    with open(args.config_file, 'r') as config_file:
        config = json.load(config_file)

    file_violations: Set[Text] = set()
    fixable = False

    for linter, options in _get_enabled_linters(config, args.config_file,
                                                args.linters):
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
        local_violations, local_fixable = _run_linter(
            args, linter(options), filtered_files,
            validate_only)
        file_violations |= local_violations
        fixable |= local_fixable

    if file_violations:
        if not fixable:
            print('%sErrors cannot be automatically fixed.%s' %
                  (git_tools.COLORS.FAIL, git_tools.COLORS.NORMAL),
                  file=sys.stderr)
        elif validate_only:
            if git_tools.attempt_automatic_fixes(sys.argv[0], args,
                                                 file_violations,
                                                 pre_upload=args.pre_upload):
                sys.exit(1)
            print('%sLinter validation errors.%s '
                  'Please run `%s` to fix them.' % (
                      git_tools.COLORS.FAIL, git_tools.COLORS.NORMAL,
                      git_tools.get_fix_commandline(_get_command_name(), args,
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
