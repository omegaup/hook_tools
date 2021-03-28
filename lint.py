#!/usr/bin/python3
# -*- coding: utf-8 -*-

"""Main entrypoint for the omegaUp linting tool."""

from __future__ import print_function

import argparse
import concurrent.futures
import enum
import json
import logging
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
    'clang-format': linters.ClangFormatLinter,
    'eslint': linters.EslintLinter,
    'html': linters.HTMLLinter,
    'javascript': linters.JavaScriptLinter,
    'json': linters.JsonLinter,
    'karel': linters.KarelLinter,
    'markdown': linters.MarkdownLinter,
    'php': linters.PHPLinter,
    'problematic-terms': linters.ProblematicTermsLinter,
    'python': linters.PythonLinter,
    'style': linters.StyleLinter,
    'typescript': linters.TypeScriptLinter,
    'vue': linters.VueLinter,
    'whitespace': linters.WhitespaceLinter,
}

_ROOT = git_tools.root_dir()


class DiagnosticsOutput(enum.Enum):
    '''Controls the format in which diagnostic messages are displayed.'''
    STDERR = 'stderr'
    GITHUB = 'github'


def _get_command_name(command_name: Optional[Text]) -> List[Text]:
    '''Returns the name of the command needed to invoke this script.'''
    if command_name is not None:
        return [command_name]
    if os.environ.get('DOCKER') == 'true':
        return [
            '/usr/bin/docker', 'run', '--rm', '-v', '"$PWD:/src"', '-v',
            '"$PWD:$PWD"', 'omegaup/hook_tools'
        ]
    return [pipes.quote(sys.argv[0])]


def _run_linter_one(
        linter: linters.Linter, filename: Text, contents: bytes,
        validate_only: bool,
        diagnostics_output: DiagnosticsOutput) -> Tuple[Optional[Text], bool]:
    '''Runs the linter against one file.'''
    try:
        new_contents, violations = linter.run_one(filename, contents)
    except linters.LinterException as lex:
        _report_linter_exception(filename, lex, diagnostics_output)
        return filename, lex.fixable

    if contents == new_contents:
        return None, False

    return _report_linter_results(filename, new_contents, validate_only,
                                  violations, True)


def _run_linter_all(
        args: argparse.Namespace, linter: linters.Linter,
        files: Sequence[Text], validate_only: bool,
        diagnostics_output: DiagnosticsOutput
) -> Sequence[Tuple[Optional[Text], bool]]:
    try:
        new_file_contents, original_contents, violations = linter.run_all(
            files,
            lambda filename: git_tools.file_contents(args, _ROOT, filename))
    except linters.LinterException as lex:
        _report_linter_exception(', '.join(files), lex, diagnostics_output)
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
        print('Fixing %s%s%s (%s)' %
              (git_tools.COLORS.HEADER, filename,
               git_tools.COLORS.NORMAL, ', '.join(violations)),
              file=sys.stderr)
        with open(os.path.join(_ROOT, filename), 'wb') as outfile:
            outfile.write(new_contents)
    return filename, fixable


def _report_linter_exception(filename: Text, lex: linters.LinterException,
                             diagnostics_output: DiagnosticsOutput) -> None:
    if diagnostics_output == DiagnosticsOutput.GITHUB:
        for diagnostic in lex.diagnostics:
            location = [f'file={diagnostic.filename}']
            if diagnostic.lineno is not None:
                location.append(f'line={diagnostic.lineno}')
            if diagnostic.col is not None:
                location.append(f'col={diagnostic.col}')
            message = diagnostic.message.replace('%', '%25').replace(
                '\r', '%0D').replace('\n', '%0A')
            print((f'::{diagnostic.level} '
                   f'{",".join(location)}::{message}\n'),
                  end='')
    message_lines: List[str] = []
    if not lex.diagnostics:
        message_lines.append(
            'File '
            f'{git_tools.COLORS.FAIL}{filename}{git_tools.COLORS.NORMAL} '
            'lint failed:')
        message_lines.append(lex.message)
    for diagnostic in lex.diagnostics:
        location = [f'{git_tools.COLORS.FAIL}{diagnostic.filename}'
                    f'{git_tools.COLORS.NORMAL}']
        if diagnostic.lineno is not None:
            location.append(str(diagnostic.lineno))
            if diagnostic.col is not None:
                location.append(str(diagnostic.col))
        message_lines.append(f'{":".join(location)}: {diagnostic.message}')
        if diagnostic.line is not None:
            message_lines.append(f'    {diagnostic.line}')
            if diagnostic.col is not None:
                prefix = re.sub(r'[^\t]', ' ',
                                diagnostic.line[:diagnostic.col - 1])
                if diagnostic.col_end is not None:
                    arrow = '^' * (diagnostic.col_end - diagnostic.col)
                else:
                    arrow = '^'
                message_lines.append(f'    {prefix}{arrow}')
    message_lines.append('')
    print('\n'.join(message_lines), end='', file=sys.stderr)


def _report_error(message: Text,
                  diagnostics_output: DiagnosticsOutput) -> None:
    '''Display an error message to the user.'''
    if diagnostics_output == DiagnosticsOutput.GITHUB:
        message = message.replace('%',
                                  '%25').replace('\r',
                                                 '%0D').replace('\n', '%0A')
        print(f'::error ::{message}\n', end='')
    else:
        print('%s%s%s' %
              (git_tools.COLORS.FAIL, message, git_tools.COLORS.NORMAL),
              file=sys.stderr)


def _run_linter(
        args: argparse.Namespace, linter: linters.Linter,
        filenames: Sequence[Text], validate_only: bool,
        diagnostics_output: DiagnosticsOutput) -> Tuple[Set[Text], bool]:
    '''Runs the linter against all files.'''
    logging.debug('%s: Files to consider: %s', linter.name,
                  ' '.join(filenames))
    logging.debug('%s: Running with %d threads', linter.name, args.jobs)
    files = dict((filename, git_tools.file_contents(args, _ROOT, filename))
                 for filename in filenames)
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=args.jobs) as executor:
        futures = []
        for filename, contents in files.items():
            futures.append(
                executor.submit(_run_linter_one, linter, filename, contents,
                                validate_only, diagnostics_output))
        results = [
            f.result() for f in concurrent.futures.as_completed(futures)
        ]
    results.extend(
        _run_linter_all(args, linter, filenames, validate_only,
                        diagnostics_output))
    return (set(violation for violation, _ in results
                if violation is not None),
            any(fixable for _, fixable in results))


def _get_enabled_linters(
        config: Mapping[Text, Any], config_file_path: Text,
        linter_allowlist: Text
) -> Iterator[Tuple[LinterFactory, Mapping[Text, Any]]]:
    '''Loads any custom linters.'''
    available_linters = dict(_LINTER_MAPPING)

    for custom_linter in config.get('custom_linters', []):
        available_linters[custom_linter['name']] = linters.CustomLinter(
            custom_linter, config_file_path)

    final_linter_allowlist = set(available_linters.keys())

    if linter_allowlist:
        args_linters = set(linter_allowlist.split(','))
        unknown_linters = args_linters - final_linter_allowlist
        if unknown_linters:
            print('Unknown linters %s%s%s.' %
                  (git_tools.COLORS.FAIL, ', '.join(unknown_linters),
                   git_tools.COLORS.NORMAL),
                  file=sys.stderr)
            sys.exit(1)
        final_linter_allowlist = args_linters

    unknown_linters = set(config['lint']) - set(available_linters)
    if unknown_linters:
        print(
            'Unknown linters %s%s%s.' %
            (git_tools.COLORS.FAIL, ', '.join(unknown_linters),
             git_tools.COLORS.NORMAL),
            file=sys.stderr)
        sys.exit(1)

    for linter_name, options in config['lint'].items():
        if linter_name not in final_linter_allowlist:
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
                '--command-name',
                default=None,
                type=str,
                help='Override the command name to execute this'),
            git_tools.Argument(
                '--linters', help='Comma-separated subset of linters to run'),
            git_tools.Argument(
                '--diagnostics-output',
                default=DiagnosticsOutput.STDERR,
                type=DiagnosticsOutput,
                choices=DiagnosticsOutput.__members__.values(),
                help='How to display diagnostics provided by the linters.'),
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

        # Filter only the files in the allowlist.
        allowlist = [re.compile(r) for r in options.get('allowlist', [])]
        filtered_files = [
            filename for filename in filtered_files
            if any(r.match(filename) for r in allowlist)]

        # And not in the denylist.
        denylist = [re.compile(r) for r in options.get('denylist', [])]
        filtered_files = [
            filename for filename in filtered_files
            if all(not r.match(filename) for r in denylist)]
        local_violations, local_fixable = _run_linter(
            args, linter(options), filtered_files,
            validate_only, args.diagnostics_output)
        file_violations |= local_violations
        fixable |= local_fixable

    if file_violations:
        if not fixable:
            _report_error('Errors cannot be automatically fixed.',
                          args.diagnostics_output)
        elif validate_only:
            if git_tools.attempt_automatic_fixes(sys.argv[0],
                                                 args,
                                                 file_violations,
                                                 pre_upload=args.pre_upload):
                sys.exit(1)
            _report_error(('Linter validation errors. '
                           'Please run\n\n    %s\n\nto fix them.') %
                          git_tools.get_fix_commandline(
                              _get_command_name(args.command_name), args,
                              file_violations), args.diagnostics_output)
        else:
            print('Files written to working directory. '
                  '%sPlease commit them before pushing.%s' % (
                      git_tools.COLORS.HEADER, git_tools.COLORS.NORMAL),
                  file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()

# vim: tabstop=4 expandtab shiftwidth=4 softtabstop=4
