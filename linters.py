'''Linters for various languages.'''
# pylint: disable=too-many-lines

from __future__ import print_function

from html.parser import HTMLParser
import bisect
import dataclasses
import importlib.util
import json
import logging
import os
import os.path
import re
import shlex
import socket
import subprocess
import sys
import tempfile
import threading
from typing import (Any, Callable, Dict, List, Mapping, NamedTuple, Optional,
                    Pattern, Text, Sequence, Tuple)

if __name__ == "__main__" and __package__ is None:
    sys.path.append(os.path.dirname(sys.path[0]))
    __package__ = "hook_tools"  # pylint: disable=redefined-builtin

from hook_tools import git_tools  # pylint: disable=E0402,C0413


def _find_pip_tool(name: Text) -> Text:
    '''Tries to find a pip tool in a few default locations.'''
    for prefix in ['/usr/bin', '/usr/local/bin']:
        toolpath = os.path.join(prefix, name)
        if os.path.exists(toolpath):
            return toolpath
    return os.path.join(os.environ['HOME'], '.local/bin', name)


def _which(program: Text) -> Text:
    '''Looks for |program| in $PATH. Similar to UNIX's `which` command.'''
    for path in ['./node_modules/.bin'] + os.environ['PATH'].split(os.pathsep):
        exe_file = os.path.abspath(os.path.join(path.strip('"'), program))
        if os.path.isfile(exe_file) and os.access(exe_file, os.X_OK):
            return exe_file
    raise Exception('`%s` not found' % program)


_TIDY_PATH = os.path.join(git_tools.HOOK_TOOLS_ROOT, 'tidy')
_PRETTIER_RE = re.compile(r'^(?:[^\s:]+\s*)?[^\s:]+: (.*) \((\d+):(\d+)\)\s*$')
_DIAGNOSTIC_RE = re.compile(r'^([^:\s]+):(\d+):(\d+): (.*)$')

Options = Mapping[str, Any]
ContentsCallback = Callable[[str], bytes]


class SingleResult(NamedTuple):
    '''The result of Linter.run_one().'''
    contents: bytes
    violations: Sequence[str] = ()


class MultipleResults(NamedTuple):
    '''The result of Linter.run_all().'''
    new_contents: Mapping[str, bytes]
    original_contents: Mapping[str, bytes]
    violations: Sequence[str] = ()


class ProblematicTerm(NamedTuple):
    '''Represents a list of problematic terms/messages associated with them.'''
    regexps: Sequence[Pattern[bytes]]
    message: str
    allowlist: Optional[Sequence[Pattern[str]]]
    denylist: Optional[Sequence[Pattern[str]]]


@dataclasses.dataclass
class Diagnostic:
    '''A diagnostic message.'''
    message: str
    filename: str
    line: Optional[str] = None
    lineno: Optional[int] = None
    col: Optional[int] = None
    col_end: Optional[int] = None
    level: str = 'error'


class LinterException(Exception):
    '''A fatal exception during linting.'''

    def __init__(
            self,
            message: Text,
            fixable: bool = True,
            diagnostics: Sequence[Diagnostic] = ()
    ) -> None:
        super().__init__(message)
        self.__message = message
        self.__fixable = fixable
        self.__diagnostics = diagnostics

    @property
    def message(self) -> Text:
        '''A message that can be presented to the user.'''
        return self.__message

    @property
    def fixable(self) -> bool:
        '''Whether this exception supports being fixed.'''
        return self.__fixable

    @property
    def diagnostics(self) -> Sequence[Diagnostic]:
        '''Any diagnostics that this LinterException could have.'''
        return self.__diagnostics


def _process_diagnostics_output(
        filename: str,
        lines: Sequence[str],
        output: str,
        toolname: Optional[str] = None) -> Sequence[Diagnostic]:
    '''Process the output of a tool in standard format.'''
    diagnostics: List[Diagnostic] = []
    for line in output.split('\n'):
        match = _DIAGNOSTIC_RE.match(line.strip())
        if not match:
            continue
        if os.path.basename(match.group(1)) != os.path.basename(filename):
            # Some diagnostics will refer to other code locations, maybe in
            # other files.
            continue
        highlighted_line = ''
        lineno = int(match.group(2))
        if len(lines) >= lineno:
            highlighted_line = lines[lineno - 1].rstrip()
        diagnostics.append(
            Diagnostic(
                (f'[{toolname}] {match.group(4)}'
                 if toolname else match.group(4)),
                filename=filename,
                lineno=lineno,
                line=highlighted_line,
                col=int(match.group(3)) or None,
            ))
    return diagnostics


def _custom_command(command: Text, filename: Text,
                    original_filename: Text) -> List[Text]:
    '''A custom command.'''

    return [
        '/bin/bash', '-c',
        '%s %s %s' % (command, shlex.quote(filename),
                      shlex.quote(original_filename))
    ]


def _lint_javascript(filename: Text,
                     contents: bytes,
                     extra_commands: Optional[Sequence[Text]] = None) -> bytes:
    '''Runs prettier on |contents|.'''

    lines = contents.decode('utf-8').split('\n')
    with tempfile.NamedTemporaryFile(suffix='.js') as js_out:
        # Keep the shebang unmodified.
        header = b''
        if contents.startswith(b'#!'):
            header, contents = contents.split(b'\n', 1)
            header += b'\n'

        js_out.write(contents)
        js_out.flush()

        commands = [
            [
                _which('prettier'), '--single-quote', '--trailing-comma=all',
                '--no-config', '--write', js_out.name
            ],
        ] + [
            _custom_command(command, js_out.name, filename)
            for command in (extra_commands or [])
        ]

        for args in commands:
            try:
                logging.debug('lint_javascript: Running %s', args)
                subprocess.run(args,
                               check=True,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT,
                               universal_newlines=True)
            except subprocess.CalledProcessError as cpe:
                diagnostics: List[Diagnostic] = []
                for line in cpe.output.strip().split('\n'):
                    match = _PRETTIER_RE.match(line)
                    if not match:
                        continue
                    diagnostics.append(
                        Diagnostic(
                            f'{match.group(1)}',
                            filename=filename,
                            lineno=int(match.group(2)),
                            line=lines[int(match.group(2)) - 1].rstrip(),
                            col=int(match.group(3)) or None,
                        ))
                raise LinterException('JavaScript lint errors',
                                      fixable=False,
                                      diagnostics=diagnostics)

        with open(js_out.name, 'rb') as js_in:
            return header + js_in.read()


def _lint_html(contents: bytes, strict: bool) -> bytes:
    '''Runs tidy on |contents|.'''

    args = [
        _TIDY_PATH, '-q', '-config',
        os.path.join(git_tools.HOOK_TOOLS_ROOT, 'tidy.txt')
    ]
    logging.debug('lint_html: Running %s', args)
    result = subprocess.run(args,
                            input=contents,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            check=False,
                            cwd=git_tools.HOOK_TOOLS_ROOT)

    if result.returncode == 0:
        return result.stdout
    if result.returncode == 1 and not strict:
        # |result.returncode| == 1 means that there were warnings.
        return result.stdout

    raise LinterException(result.stderr.decode('utf-8', errors='replace'))


def _lint_prettier(contents: bytes, filename: Text) -> bytes:
    '''Runs prettier on |contents| .'''
    args = [
        _which('prettier'), '--single-quote', '--trailing-comma=all',
        '--no-config',
        '--stdin-filepath=%s' % filename
    ]
    logging.debug('lint_prettier: Running %s', args)
    result = subprocess.run(args,
                            input=contents,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            check=False,
                            cwd=git_tools.HOOK_TOOLS_ROOT)

    if result.returncode != 0:
        lines = contents.decode('utf-8').split('\n')
        diagnostics: List[Diagnostic] = []
        for line in result.stderr.decode('utf-8',
                                         errors='replace').strip().split('\n'):
            match = _PRETTIER_RE.match(line)
            if not match:
                continue
            diagnostics.append(
                Diagnostic(
                    f'{match.group(1)}',
                    filename=filename,
                    lineno=int(match.group(2)),
                    line=lines[int(match.group(2)) - 1].rstrip(),
                    col=int(match.group(3)) or None,
                ))
        raise LinterException('lint errors',
                              fixable=False,
                              diagnostics=diagnostics)

    return result.stdout


def _lint_stylelint(contents: bytes, filename: Text) -> bytes:
    '''Runs stylelint on |contents| .'''
    args = [
        _which('stylelint'),
        '--stdin',
        '--stdin-filename=%s' % filename,
        '--fix',
    ]
    logging.debug('lint_style: Running %s', args)
    result = subprocess.run(args,
                            input=contents,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            check=False)

    if result.returncode == 0:
        # Everything is correct!
        return contents

    if result.returncode != 2:
        # stylelint returns 1 if something went catastrophically wrong, or 78
        # if there was a problem with the configuration. This means that the
        # only return code that we expect right now is 2, which is returned if
        # there were lint errors.
        raise LinterException(result.stdout.decode('utf-8'))

    if result.stdout != contents:
        # If stylelint was able to automatically fix anything, we go with
        # that.
        return result.stdout

    args = [
        _which('stylelint'),
        '--stdin',
        '--stdin-filename=%s' % filename,
        '--formatter=unix',
    ]
    logging.debug('lint_style: Running %s', args)
    result = subprocess.run(args,
                            input=contents,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            check=False)

    if result.returncode != 0:
        raise LinterException(
            'lint errors',
            fixable=False,
            diagnostics=_process_diagnostics_output(
                filename,
                lines=contents.decode('utf-8').split('\n'),
                output=result.stdout.decode('utf-8').strip()))

    return contents


class Linter:
    '''An abstract Linter.'''
    # pylint: disable=R0903

    def __init__(self) -> None:
        pass

    def run_one(self, filename: str, contents: bytes) -> SingleResult:
        '''Runs the linter against |contents|.'''
        # pylint: disable=no-self-use
        del filename  # unused
        return SingleResult(contents)

    def run_all(self, filenames: Sequence[str],
                contents_callback: ContentsCallback) -> MultipleResults:
        '''Runs the linter against a subset of files.'''
        # pylint: disable=no-self-use
        del filenames, contents_callback  # unused
        return MultipleResults(new_contents={}, original_contents={})

    @property
    def name(self) -> Text:
        '''Gets the name of the linter.'''
        return 'linter'


class JavaScriptLinter(Linter):
    '''Runs the Google Closure Compiler linter+prettier against |files|.'''

    # pylint: disable=R0903

    def __init__(self, options: Optional[Options] = None) -> None:
        super().__init__()
        self.__options = options or {}

    def run_one(self, filename: str, contents: bytes) -> SingleResult:
        try:
            return SingleResult(
                _lint_javascript(filename, contents,
                                 self.__options.get('extra_js_linters')),
                ['javascript'])
        except subprocess.CalledProcessError as cpe:
            raise LinterException(
                str(b'\n'.join(cpe.output.split(b'\n')[1:]), encoding='utf-8'))

    @property
    def name(self) -> Text:
        return 'javascript'


class TypeScriptLinter(Linter):
    '''Runs prettier against |files|.'''

    # pylint: disable=R0903

    def __init__(self, options: Optional[Options] = None) -> None:
        super().__init__()
        self.__options = options or {}

    def run_one(self, filename: str, contents: bytes) -> SingleResult:
        try:
            return SingleResult(_lint_prettier(contents, filename),
                                ['typescript'])
        except subprocess.CalledProcessError as cpe:
            raise LinterException(
                str(b'\n'.join(cpe.output.split(b'\n')[1:]), encoding='utf-8'))

    @property
    def name(self) -> Text:
        return 'typescript'


class KarelLinter(Linter):
    '''Runs prettier against |files|.'''

    # pylint: disable=R0903

    def __init__(self, options: Optional[Options] = None) -> None:
        super().__init__()
        self.__options = options or {}

    def run_one(self, filename: str, contents: bytes) -> SingleResult:
        try:
            return SingleResult(_lint_prettier(contents, filename),
                                ['karel'])
        except subprocess.CalledProcessError as cpe:
            raise LinterException(
                str(b'\n'.join(cpe.output.split(b'\n')[1:]), encoding='utf-8'))

    @property
    def name(self) -> Text:
        return 'karel'


class MarkdownLinter(Linter):
    '''Runs prettier against |files|.'''

    # pylint: disable=R0903

    def __init__(self, options: Optional[Options] = None) -> None:
        super().__init__()
        self.__options = options or {}

    def run_one(self, filename: str, contents: bytes) -> SingleResult:
        try:
            return SingleResult(_lint_prettier(contents, filename),
                                ['markdown'])
        except subprocess.CalledProcessError as cpe:
            raise LinterException(
                str(b'\n'.join(cpe.output.split(b'\n')[1:]), encoding='utf-8'))

    @property
    def name(self) -> Text:
        return 'markdown'


class JsonLinter(Linter):
    '''Runs prettier against |files|.'''

    # pylint: disable=R0903

    def __init__(self, options: Optional[Options] = None) -> None:
        super().__init__()
        self.__options = options or {}

    def run_one(self, filename: str, contents: bytes) -> SingleResult:
        try:
            return SingleResult(_lint_prettier(contents, filename),
                                ['json'])
        except subprocess.CalledProcessError as cpe:
            raise LinterException(
                str(b'\n'.join(cpe.output.split(b'\n')[1:]), encoding='utf-8'))

    @property
    def name(self) -> Text:
        return 'json'


class WhitespaceLinter(Linter):
    '''Removes annoying superfluous whitespace.'''
    # pylint: disable=R0903

    _VALIDATIONS = [
        ('Windows-style EOF', re.compile(br'\r\n?'), br'\n'),
        ('trailing whitespace', re.compile(br'[ \t]+\n'), br'\n'),
        ('consecutive empty lines', re.compile(br'\n\n\n+'), br'\n\n'),
        ('empty lines after an opening brace', re.compile(br'{\n\n+'),
         br'{\n'),
        ('empty lines before a closing brace', re.compile(br'\n+\n(\s*})'),
         br'\n\1'),
    ]

    def __init__(self, options: Optional[Options] = None) -> None:
        super().__init__()
        self.__options = options or {}

    def run_one(self, filename: str, contents: bytes) -> SingleResult:
        '''Runs all validations against |files|.

        A validation consists of performing regex substitution against the
        contents of each file in |files|.  Validation fails if the resulting
        content is not identical to the original.  The contents of the files
        will be presented as a single string, allowing for multi-line matches.
        '''
        violations = []

        # Run all validations sequentially, so all violations can be fixed
        # together.
        for error_string, search, replace in WhitespaceLinter._VALIDATIONS:
            replaced = search.sub(replace, contents)
            if replaced != contents:
                violations.append(error_string)
                contents = replaced

        return SingleResult(contents, violations)

    @property
    def name(self) -> Text:
        return 'whitespace'


class ProblematicTermsLinter(Linter):
    '''Warns if there are problematic terms in the codebase.'''
    # pylint: disable=R0903

    def __init__(self, options: Optional[Options] = None) -> None:
        super().__init__()
        self.__terms: List[ProblematicTerm] = []
        for term in (options or {}).get('terms', []):
            if not isinstance(term, dict):
                raise ValueError(f'{term} is not a dictionary')
            if 'regexps' not in term:
                raise ValueError(f'{term} does not contain a key "regexps"')
            if not isinstance(term['regexps'], list):
                raise ValueError(f'{term} is not a list of string')
            if not all(isinstance(regexp, str) for regexp in term['regexps']):
                raise ValueError(f'{term} is not a list of string')
            allowlist: Optional[Sequence[Pattern[str]]] = None
            denylist: Optional[Sequence[Pattern[str]]] = None
            if 'allowlist' in term:
                allowlist = [
                    re.compile(pattern) for pattern in term['allowlist']
                ]
            if 'denylist' in term:
                denylist = [
                    re.compile(pattern) for pattern in term['denylist']
                ]
            self.__terms.append(
                ProblematicTerm(
                    regexps=[
                        re.compile(regexp.encode('utf-8'), re.MULTILINE)
                        for regexp in term['regexps']
                    ],
                    message=term.get('message', 'is a problematic term'),
                    allowlist=allowlist,
                    denylist=denylist,
                ))

    def run_one(self, filename: str, contents: bytes) -> SingleResult:
        '''Tries to identify problematic term in the provided file.'''
        diagnostics = []
        lines = contents.split(b'\n')
        start_indices: List[int] = [0]

        for line in lines:
            start_indices.append(start_indices[-1] + len(line) + 1)

        for term in self.__terms:
            if term.denylist is not None and any(
                    r.match(filename) for r in term.denylist):
                continue
            if term.allowlist is not None and not any(
                    r.match(filename) for r in term.allowlist):
                continue
            for regexp in term.regexps:
                for match in regexp.finditer(contents):
                    found_term = match.group()
                    lineno = bisect.bisect_left(start_indices, match.start())
                    col = match.start() - start_indices[lineno] + 1
                    diagnostics.append(
                        Diagnostic(
                            f'{found_term.decode("utf-8")}: {term.message}',
                            filename=filename,
                            lineno=lineno + 1,
                            col=col,
                            col_end=col + len(found_term)))

        if diagnostics:
            raise LinterException('Found problematic terms',
                                  diagnostics=diagnostics,
                                  fixable=False)

        return SingleResult(contents, [])

    @property
    def name(self) -> Text:
        return 'problematic_terms'


class VueHTMLParser(HTMLParser):
    '''A parser that can understand .vue template files.'''

    # pylint: disable=R0903

    def __init__(self) -> None:
        super(VueHTMLParser, self).__init__()
        self._stack: List[Tuple[Text, List[Tuple[str, Optional[str]]], Text,
                                Tuple[int, int]]] = []
        self._tags: List[Tuple[Text, List[Tuple[str, Optional[str]]], str,
                               Tuple[int, int], Tuple[int, int]]] = []
        self._id_linter_enabled = True
        self.diagnostics: List[Diagnostic] = []
        self._lines: List[str] = []
        self._filename: str = ''

    def error(self, message: Text) -> None:
        raise LinterException(message)

    def parse(
            self, contents: Text, filename: str
    ) -> Sequence[Tuple[Text, List[Tuple[str, Optional[str]]], Text, Text]]:
        '''Parses |contents| and returns the .vue-specific sections.'''

        self._stack = []
        self._tags = []
        self._filename = filename
        self._lines = contents.split('\n')
        self.feed(contents)

        sections = []
        for tag, attrs, starttag, start, end in self._tags:
            line_range = []
            if len(self._lines[start[0]]) > len(starttag) + start[1]:
                line_range.append(self._lines[start[0]][len(starttag)
                                                        + start[1]:])
            line_range += self._lines[start[0] + 1:end[0]]
            if end[1] > 0:
                line_range.append(self._lines[end[0]][:end[1]])
            sections.append((tag, attrs, starttag, '\n'.join(line_range)))
        return sections

    def handle_starttag(self, tag: Text,
                        attrs: List[Tuple[Text, Optional[Text]]]) -> None:
        line, col = self.getpos()
        self._stack.append((tag, attrs, str(self.get_starttag_text()),
                            (line - 1, col)))
        if not self._id_linter_enabled:
            return
        for name, _ in attrs:
            if name == 'id':
                self.diagnostics.append(
                    Diagnostic(
                        'Use of "id" attribute in .vue files is discouraged.',
                        filename=self._filename,
                        line=self._lines[line - 1],
                        lineno=line,
                        col=col + 1))

    def handle_endtag(self, tag: Text) -> None:
        while self._stack and self._stack[-1][0] != tag:
            self._stack.pop()
        if not self._stack or self._stack[-1][0] != tag:
            line, col = self.getpos()
            self.diagnostics.append(
                Diagnostic('Unclosed tag',
                           filename=self._filename,
                           line=self._lines[line - 1],
                           lineno=line,
                           col=col + 1))
            return
        _, attrs, starttag, begin = self._stack.pop()
        if not self._stack:
            line, col = self.getpos()
            self._tags.append((tag, attrs, starttag, begin, (line - 1, col)))

    def handle_comment(self, data: Text) -> None:
        if data.find('id-lint ') < 0:
            return
        self._id_linter_enabled = data.strip().split()[1] == 'on'


class VueLinter(Linter):
    '''A linter for .vue files.'''

    # pylint: disable=R0903

    def __init__(self, options: Optional[Options] = None) -> None:
        super().__init__()
        self.__options = options or {}

    def run_one(self, filename: str, contents: bytes) -> SingleResult:
        parser = VueHTMLParser()
        try:
            sections = parser.parse(contents.decode('utf-8'),
                                    filename=filename)
        except AssertionError as assertion:
            raise LinterException(str(assertion),
                                  diagnostics=parser.diagnostics,
                                  fixable=False)
        if parser.diagnostics:
            raise LinterException('Found Vue errors',
                                  diagnostics=parser.diagnostics,
                                  fixable=False)

        new_sections = []
        for tag, _, starttag, section_contents in sorted(
                sections,
                key=lambda x: ['template', 'script', 'style'].index(x[0])):
            try:
                new_sections.append('%s\n%s\n</%s>' %
                                    (starttag, section_contents.rstrip(), tag))
            except subprocess.CalledProcessError as cpe:
                raise LinterException(
                    str(b'\n'.join(cpe.output.split(b'\n')[1:]),
                        encoding='utf-8'),
                    fixable=False)

        if len(new_sections) != len(sections):
            raise LinterException('Mismatched sections: expecting %d, got %d' %
                                  (len(sections), len(new_sections)),
                                  fixable=False)

        contents = '\n\n'.join(new_sections).encode('utf-8') + b'\n'
        if self.__options.get('stylelint', False):
            contents = _lint_stylelint(contents, filename)

        # Finally, run prettier on the whole thing.
        return SingleResult(_lint_prettier(contents, filename), ['vue'])

    @property
    def name(self) -> Text:
        return 'vue'


class StyleLinter(Linter):
    '''A linter for .css/.scss files.'''

    # pylint: disable=R0903

    def __init__(self, options: Optional[Options] = None) -> None:
        super().__init__()
        self.__options = options or {}

    def run_one(self, filename: str, contents: bytes) -> SingleResult:
        contents = _lint_stylelint(contents, filename)

        # Finally, run prettier on the whole thing.
        return SingleResult(_lint_prettier(contents, filename), ['style'])

    @property
    def name(self) -> Text:
        return 'style'


class HTMLLinter(Linter):
    '''Runs HTML Tidy + prettier.'''

    # pylint: disable=R0903

    def __init__(self, options: Optional[Options] = None) -> None:
        super().__init__()
        self.__options = options or {}

    def run_one(self, filename: str, contents: bytes) -> SingleResult:
        contents = _lint_html(
            contents, strict=bool(self.__options.get('strict', False)))

        # Finally, run prettier on the whole thing.
        return SingleResult(_lint_prettier(contents, filename), ['html'])

    @property
    def name(self) -> Text:
        return 'html'


class PHPLinter(Linter):
    '''Runs the PHP Code Beautifier.'''

    # pylint: disable=R0903

    def __init__(self, options: Optional[Options] = None) -> None:
        super().__init__()
        self.__options = options or {}
        standard = self.__options.get(
            'standard',
            os.path.join(git_tools.HOOK_TOOLS_ROOT,
                         'phpcbf/Standards/OmegaUp/ruleset.xml'))
        self.__common_args = ['--encoding=utf-8', '--standard=%s' % standard]

    def run_one(self, filename: str, contents: bytes) -> SingleResult:
        args = ([_which('phpcbf')] + self.__common_args
                + ['--stdin-path=%s' % filename])
        logging.debug('lint_php: Running %s', shlex.join(args))
        result = subprocess.run(args,
                                input=contents,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                check=False)

        if result.returncode != 0:
            logging.debug('lint_php: Return code %d, stderr = %s',
                          result.returncode, result.stderr)
            if not result.stdout:
                # phpcbf returns 1 if there was no change to the file. If there
                # was an actual error, there won't be anything in stdout.
                raise LinterException(result.stderr.decode('utf-8'))

        new_contents = result.stdout

        if new_contents != contents:
            # If phpcbf was able to fix anything, let's go with that instead of
            # running phpcs. Otherwise, phpcs will return non-zero and the
            # suggestions won't be used.
            return SingleResult(new_contents, ['php'])

        # Even if phpcbf didn't find anything, phpcs might.
        args = ([_which('phpcs'), '-s', '-q'] + self.__common_args
                + ['--stdin-path=%s' % filename, '--report=emacs'])
        logging.debug('lint_php: Running %s', shlex.join(args))
        result = subprocess.run(args,
                                input=contents,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                check=False)

        if result.returncode != 0:
            logging.debug('lint_php: Return code %d, stdout = %s, stderr = %s',
                          result.returncode, result.stdout, result.stderr)
            raise LinterException(
                'phpcs lint errors',
                fixable=False,
                diagnostics=_process_diagnostics_output(
                    filename,
                    lines=contents.decode('utf-8').split('\n'),
                    output=result.stdout.decode('utf-8').strip()))

        return SingleResult(new_contents, ['php'])

    @property
    def name(self) -> Text:
        return 'php'


class PythonLinter(Linter):
    '''Runs pycodestyle, pylint, and Mypy.'''

    # pylint: disable=R0903

    def __init__(self, options: Optional[Options] = None) -> None:
        super().__init__()
        self.__options = options or {}

    def run_one(self, filename: str, contents: bytes) -> SingleResult:
        diagnostics: List[Diagnostic] = []
        lines = contents.decode('utf-8').split('\n')
        with tempfile.TemporaryDirectory(prefix='python_linter_') as tmpdir:
            tmp_path = os.path.join(tmpdir, os.path.basename(filename))
            with open(tmp_path, 'wb') as pyfile:
                pyfile.write(contents)

            python3 = _which('python3')

            args = [
                python3, '-m', 'pycodestyle',
                '--format=%(path)s:%(row)d:%(col)d: %(code)s %(text)s',
                tmp_path
            ]
            for configname in ('pycodestyle_config', 'pep8_config'):
                if configname not in self.__options:
                    continue
                args.append('--config=%s' % self.__options[configname])
                break
            try:
                logging.debug('lint_python: Running %s', args)
                subprocess.run(args,
                               check=True,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT,
                               universal_newlines=True)
            except subprocess.CalledProcessError as cpe:
                diagnostics.extend(
                    _process_diagnostics_output(filename,
                                                lines,
                                                cpe.output.strip(),
                                                toolname='pycodestyle'))

            # We need to disable import-error since the file won't be checked
            # in the repository, but in a temporary directory.
            args = [
                python3, '-m', 'pylint', '--output-format=text',
                ('--msg-template={abspath}:{line}:{column}: '
                 '{msg_id}({symbol}) {msg}'), '--reports=no',
                '--disable=import-error', tmp_path
            ]
            if 'pylint_config' in self.__options:
                args.append('--rcfile=%s' % self.__options['pylint_config'])
            try:
                logging.debug('lint_python: Running %s', args)
                subprocess.run(args,
                               check=True,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT,
                               universal_newlines=True)
            except subprocess.CalledProcessError as cpe:
                diagnostics.extend(
                    _process_diagnostics_output(filename,
                                                lines,
                                                cpe.output.strip(),
                                                toolname='pylint'))

            if self.__options.get('mypy', False):
                args = [
                    _which('mypy'),
                    '--show-column-numbers',
                    '--strict',
                    '--no-incremental',
                    '--follow-imports=silent',
                    filename,
                ]
                try:
                    logging.debug('lint_python: Running %s', args)
                    subprocess.run(args,
                                   check=True,
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT,
                                   universal_newlines=True)
                except subprocess.CalledProcessError as cpe:
                    diagnostics.extend(
                        _process_diagnostics_output(filename,
                                                    lines,
                                                    cpe.output.strip(),
                                                    toolname='mypy'))

        if diagnostics:
            diagnostics.sort(key=lambda d: d.lineno or 0)
            raise LinterException('Python lint errors',
                                  fixable=False,
                                  diagnostics=diagnostics)
        return SingleResult(contents, [])

    @property
    def name(self) -> Text:
        return 'python'


class ClangFormatLinter(Linter):
    '''Runs clang-format.'''

    # pylint: disable=R0903

    def __init__(self, options: Optional[Options] = None) -> None:
        super().__init__()
        self.__options = options or {}

    def run_one(self, filename: str, contents: bytes) -> SingleResult:
        with tempfile.TemporaryDirectory(
                prefix='clang_format_linter_') as tmpdir:
            tmp_path = os.path.join(tmpdir, os.path.basename(filename))
            with open(tmp_path, 'wb') as outf:
                outf.write(contents)

            args = [
                _which('clang-format'),
                '--style=file',
                '--fallback-style=Google',
                f'--assume-filename={filename}',
                tmp_path,
            ]
            logging.debug('lint_clang_format: Running %s', args)
            result = subprocess.run(args,
                                    check=False,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE)
            if result.returncode != 0:
                raise LinterException('Found clang-format errors',
                                      diagnostics=[
                                          Diagnostic(
                                              result.stderr.decode('utf-8'),
                                              filename=filename),
                                      ],
                                      fixable=False)
            return SingleResult(result.stdout, ['clang-format'])

    @property
    def name(self) -> Text:
        return 'clang-format'


class EslintLinter(Linter):
    '''Runs eslint.'''

    # pylint: disable=R0903

    def __init__(self, options: Optional[Options] = None) -> None:
        super().__init__()
        self.__options = options or {}
        self.__lock = threading.Lock()
        self.__eslint_state: Optional[Tuple[int, str]] = None

    def _get_port_and_token(self) -> Tuple[int, str]:
        if self.__eslint_state is None:
            with self.__lock:
                if self.__eslint_state is None:
                    args = [
                        _which('npx'),
                        'eslint_d',
                        'start',
                    ]
                    logging.debug('lint_eslint: Running %s', args)
                    subprocess.check_call(args,
                                          env={
                                              **os.environ, 'HOME': '/tmp'
                                          })
                    with open('/tmp/.eslint_d') as portfile:
                        port_str, token = portfile.read().strip().split()
                    self.__eslint_state = (int(port_str), token)
        return self.__eslint_state

    def run_one(self, filename: str, contents: bytes) -> SingleResult:
        port, token = self._get_port_and_token()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.connect(('localhost', port))
            args = [
                token,
                os.getcwd(),
                '--fix-dry-run',
                '--stdin',
                '--stdin-filename',
                filename,
                '--format=json',
                '--cache',
            ]
            logging.debug('lint_eslint: Running %s', args)
            sock.sendall(shlex.join(args).encode('utf-8') + b'\n')
            sock.sendall(contents)
            sock.shutdown(socket.SHUT_WR)

            stdout_chunks: List[bytes] = []
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                stdout_chunks.append(chunk)
            stdout = (b''.join(stdout_chunks)).decode('utf-8')
            try:
                reports = json.loads(stdout)
            except json.decoder.JSONDecodeError as jde:
                # If there's extra data, let's just ignore it and try once
                # more.
                if jde.msg != 'Extra data':
                    raise
                reports = json.loads(stdout[:jde.pos])
            assert len(reports) == 1, reports
            report = reports[0]

            if len(report['messages']) > 0:
                lines = contents.decode('utf-8').split('\n')
                diagnostics = [
                    Diagnostic(
                        message=(f'[eslint] {message["message"]} '
                                 f'[{message["ruleId"]}]'),
                        filename=filename,
                        line=lines[message['line'] - 1],
                        lineno=message['line'],
                        col=message['column'],
                        col_end=(message['endColumn']
                                 if 'endColumn' in message else None),
                    ) for message in report['messages']
                ]
                raise LinterException('Eslint lint errors',
                                      fixable=False,
                                      diagnostics=diagnostics)
            if 'output' in report:
                # There were fixable errors.
                return SingleResult(report['output'].encode('utf-8'),
                                    ['eslint'])

            return SingleResult(contents, [])

    @property
    def name(self) -> Text:
        return 'eslint'


class CustomLinter(Linter):
    '''A lazily, dynamically-loaded linter.'''

    def __init__(self, custom_linter: Options,
                 config_file_path: Text) -> None:
        super().__init__()
        self.__module_path = os.path.join(
            os.path.dirname(config_file_path), custom_linter['path'])
        self.__config = custom_linter
        self.__instance: Optional[Linter] = None
        self.__options: Dict[Text, Text] = {}

    @property
    def _instance(self) -> Linter:
        if self.__instance is not None:
            return self.__instance
        custom_linter_module_spec = importlib.util.spec_from_file_location(
            self.__module_path.rstrip('.py').replace('/', '_'),
            self.__module_path)
        if custom_linter_module_spec is None:
            raise RuntimeError("Cannot find module")
        custom_linter_module = importlib.util.module_from_spec(
            custom_linter_module_spec)
        custom_linter_module_spec.loader.exec_module(  # type: ignore
            custom_linter_module)
        self.__instance = getattr(custom_linter_module,
                                  self.__config['class_name'])(self.__options)
        return self.__instance

    def __call__(self, options: Optional[Options] = None
                 ) -> 'CustomLinter':
        # Instead of the constructor being stored in the map of available
        # linters, a live instance of this class is stored. Later, this
        # function is called in lieu of the constructor.
        if options:
            self.__options = dict(options)
        return self

    def __getstate__(self) -> Mapping[Text, Any]:
        # This is needed to prevent self.__instance from being pickled, which
        # is something that is not supported.
        return {
            '_CustomLinter__config': self.__config,
            '_CustomLinter__module_path': self.__module_path,
            '_CustomLinter__options': self.__options,
            '_CustomLinter__instance': None,
        }

    def run_one(self, filename: str, contents: bytes) -> SingleResult:
        return self._instance.run_one(filename, contents)

    def run_all(self, filenames: Sequence[Text],
                contents_callback: Callable[[Text], bytes]) -> MultipleResults:
        return self._instance.run_all(filenames, contents_callback)

    @property
    def name(self) -> Text:
        return self._instance.name


class CommandLinter(Linter):
    '''Runs a custom command as linter.'''

    # pylint: disable=R0903

    def __init__(self, options: Optional[Options] = None) -> None:
        super().__init__()
        self.__options = options or {}

    def run_one(self, filename: str, contents: bytes) -> SingleResult:
        extension = os.path.splitext(filename)[1]
        with tempfile.NamedTemporaryFile(suffix=extension) as tmp:
            tmp.write(contents)
            tmp.flush()

            commands = [
                _custom_command(command, tmp.name, filename)
                for command in self.__options.get('commands', [])
            ]

            for args in commands:
                try:
                    logging.debug('lint_command: Running %s', args)
                    subprocess.run(args,
                                   check=True,
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT,
                                   universal_newlines=True)
                except subprocess.CalledProcessError as cpe:
                    raise LinterException(cpe.output, fixable=False)

            with open(tmp.name, 'rb') as tmp_in:
                return SingleResult(tmp_in.read(), ['command'])

    @property
    def name(self) -> Text:
        return 'command (%s)' % (self.__options.get('commands', []), )


# vim: tabstop=4 expandtab shiftwidth=4 softtabstop=4
