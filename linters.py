'''Linters for various languages.'''

from __future__ import print_function

from abc import ABCMeta, abstractmethod
from html.parser import HTMLParser
import importlib.util
import logging
import os
import os.path
import re
import shlex
import subprocess
import tempfile
from typing import (Any, Callable, Dict, List, Mapping, Optional, Text,
                    Sequence, Tuple)

from . import git_tools  # pylint: disable=relative-beyond-top-level


def _find_pip_tool(name: Text) -> Text:
    '''Tries to find a pip tool in a few default locations.'''
    for prefix in ['/usr/bin', '/usr/local/bin']:
        toolpath = os.path.join(prefix, name)
        if os.path.exists(toolpath):
            return toolpath
    return os.path.join(os.environ['HOME'], '.local/bin', name)


def _which(program: Text) -> Text:
    '''Looks for |program| in $PATH. Similar to UNIX's `which` command.'''
    for path in os.environ['PATH'].split(os.pathsep):
        exe_file = os.path.join(path.strip('"'), program)
        if os.path.isfile(exe_file) and os.access(exe_file, os.X_OK):
            return exe_file
    raise Exception('`%s` not found' % program)


_TIDY_PATH = os.path.join(git_tools.HOOK_TOOLS_ROOT, 'tidy')


class LinterException(Exception):
    '''A fatal exception during linting.'''

    def __init__(self, message: Text, fixable: bool = True) -> None:
        super().__init__(message)
        self.__message = message
        self.__fixable = fixable

    @property
    def message(self) -> Text:
        '''A message that can be presented to the user.'''
        return self.__message

    @property
    def fixable(self) -> bool:
        '''Whether this exception supports being fixed.'''
        return self.__fixable


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
            logging.debug('lint_javascript: Running %s', args)
            subprocess.check_output(args, stderr=subprocess.STDOUT)

        with open(js_out.name, 'rb') as js_in:
            return header + js_in.read()


def _lint_typescript(filename: Text, contents: bytes) -> bytes:
    '''Runs prettier on |contents|.'''

    args = [_which('prettier'), '--single-quote', '--trailing-comma=all',
            '--no-config', '--stdin-filepath=%s' % filename]
    logging.debug('lint_typescript: Running %s', args)
    with subprocess.Popen(args, stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          cwd=git_tools.HOOK_TOOLS_ROOT) as proc:
        new_contents, stderr = proc.communicate(contents)
        retcode = proc.wait()

        if retcode == 0:
            return new_contents

        raise LinterException(stderr.decode('utf-8', errors='replace'))


def _lint_html(contents: bytes, strict: bool) -> bytes:
    '''Runs tidy on |contents|.'''

    args = [_TIDY_PATH, '-q', '-config',
            os.path.join(git_tools.HOOK_TOOLS_ROOT, 'tidy.txt')]
    logging.debug('lint_html: Running %s', args)
    with subprocess.Popen(args, stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          cwd=git_tools.HOOK_TOOLS_ROOT) as proc:
        new_contents, stderr = proc.communicate(contents)
        retcode = proc.wait()

        if retcode == 0:
            return new_contents
        if retcode == 1 and not strict:
            # |retcode| == 1 means that there were warnings.
            return new_contents

        raise LinterException(stderr.decode('utf-8', errors='replace'))


class Linter:
    '''An abstract Linter.'''
    # pylint: disable=R0903

    __metaclass__ = ABCMeta

    def __init__(self) -> None:
        pass

    @abstractmethod
    def run_one(self, filename: Text,
                contents: bytes) -> Tuple[bytes, Sequence[Text]]:
        '''Runs the linter against |contents|.'''

    @abstractmethod
    def run_all(
            self, filenames: Sequence[Text],
            contents_callback: Callable[[Text], bytes]
    ) -> Tuple[Mapping[Text, bytes], Mapping[Text, bytes], Sequence[Text]]:
        '''Runs the linter against a subset of files.'''

    @property
    def name(self) -> Text:
        '''Gets the name of the linter.'''
        return 'linter'


class JavaScriptLinter(Linter):
    '''Runs the Google Closure Compiler linter+prettier against |files|.'''
    # pylint: disable=R0903

    def __init__(self, options: Optional[Mapping[Text, Text]] = None) -> None:
        super().__init__()
        self.__options = options or {}

    def run_one(self, filename: Text,
                contents: bytes) -> Tuple[bytes, Sequence[Text]]:
        try:
            return (_lint_javascript(filename, contents,
                                     self.__options.get('extra_js_linters')),
                    ['javascript'])
        except subprocess.CalledProcessError as cpe:
            raise LinterException(str(b'\n'.join(cpe.output.split(b'\n')[1:]),
                                      encoding='utf-8'))

    def run_all(
            self, filenames: Sequence[Text],
            contents_callback: Callable[[Text], bytes]
    ) -> Tuple[Mapping[Text, bytes], Mapping[Text, bytes], Sequence[Text]]:
        return {}, {}, []

    @property
    def name(self) -> Text:
        return 'javascript'


class WhitespaceLinter(Linter):
    '''Removes annoying superfluous whitespace.'''
    # pylint: disable=R0903

    _VALIDATIONS = [
        ('Windows-style EOF', re.compile(br'\r\n?'), br'\n'),
        ('trailing whitespace', re.compile(br'[ \t]+\n'), br'\n'),
        ('consecutive empty lines', re.compile(br'\n\n\n+'), br'\n\n'),
        ('empty lines after an opening brace',
         re.compile(br'{\n\n+'), br'{\n'),
        ('empty lines before a closing brace',
         re.compile(br'\n+\n(\s*})'), br'\n\1'),
    ]

    def __init__(self, options: Optional[Mapping[Text, Text]] = None) -> None:
        super().__init__()
        self.__options = options or {}

    def run_one(self, filename: Text,
                contents: bytes) -> Tuple[bytes, Sequence[Text]]:
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

        return contents, violations

    def run_all(
            self, filenames: Sequence[Text],
            contents_callback: Callable[[Text], bytes]
    ) -> Tuple[Mapping[Text, bytes], Mapping[Text, bytes], Sequence[Text]]:
        return {}, {}, []

    @property
    def name(self) -> Text:
        return 'whitespace'


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

    def error(self, message: Text) -> None:
        raise LinterException(message)

    def parse(
            self, contents: Text
    ) -> Sequence[Tuple[Text, List[Tuple[str, Optional[str]]], Text, Text]]:
        '''Parses |contents| and returns the .vue-specific sections.'''

        self._stack = []
        self._tags = []
        self.feed(contents)

        lines = contents.split('\n')

        sections = []
        for tag, attrs, starttag, start, end in self._tags:
            line_range = []
            if len(lines[start[0]]) > len(starttag) + start[1]:
                line_range.append(lines[start[0]][len(starttag) + start[1]:])
            line_range += lines[start[0] + 1:end[0]]
            if end[1] > 0:
                line_range.append(lines[end[0]][:end[1]])
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
                raise LinterException(
                    ('Use of "id" attribute in .vue files is '
                     'discouraged. Found one in line %d\n') % (line),
                    fixable=False)

    def handle_endtag(self, tag: Text) -> None:
        while self._stack and self._stack[-1][0] != tag:
            self._stack.pop()
        if not self._stack or self._stack[-1][0] != tag:
            raise LinterException(
                'Unclosed tag at line %d, column %d' % self.getpos())
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

    def __init__(self, options: Optional[Mapping[Text, Text]] = None) -> None:
        super().__init__()
        self.__options = options or {}

    def run_one(self, filename: Text,
                contents: bytes) -> Tuple[bytes, Sequence[Text]]:
        parser = VueHTMLParser()
        try:
            sections = parser.parse(contents.decode('utf-8'))
        except AssertionError as assertion:
            raise LinterException(str(assertion))

        new_sections = []
        for tag, attrs, starttag, section_contents in sections:
            try:
                if tag == 'script':
                    if any(val == 'ts' for name, val in attrs
                           if name == 'lang'):
                        new_section_contents = _lint_typescript(
                            filename + '.ts', section_contents.encode('utf-8'))
                    else:
                        new_section_contents = _lint_javascript(
                            filename + '.js', section_contents.encode('utf-8'),
                            self.__options.get('extra_js_linters'))
                    new_sections.append(
                        '%s\n%s\n</%s>' %
                        (starttag, new_section_contents.decode('utf-8'), tag))
                elif tag == 'template':
                    lines = _lint_html(
                        (b'<!DOCTYPE html>\n<html>\n<head>\n'
                         b'<title></title>\n</head><body>\n'
                         b'%s\n'
                         b'</body>\n</html>') %
                        section_contents.encode('utf-8'),
                        strict=bool(self.__options.get('strict',
                                                       False))).split(b'\n')
                    new_section_contents = b'\n'.join(
                        line.rstrip() for line in lines[6:-3])
                    new_sections.append(
                        '%s\n%s\n</%s>' %
                        (starttag, new_section_contents.decode('utf-8'), tag))
                else:
                    new_sections.append('%s\n%s\n</%s>' % (
                        starttag, section_contents, tag))
            except subprocess.CalledProcessError as cpe:
                raise LinterException(
                    str(b'\n'.join(cpe.output.split(b'\n')[1:]),
                        encoding='utf-8'))

        if len(new_sections) != len(sections):
            raise LinterException('Mismatched sections: expecting %d, got %d' %
                                  (len(sections), len(new_sections)))

        return ('\n\n'.join(new_sections)).encode('utf-8') + b'\n', ['vue']

    def run_all(
            self, filenames: Sequence[Text],
            contents_callback: Callable[[Text], bytes]
    ) -> Tuple[Mapping[Text, bytes], Mapping[Text, bytes], Sequence[Text]]:
        return {}, {}, []

    @property
    def name(self) -> Text:
        return 'vue'


class HTMLLinter(Linter):
    '''Runs HTML Tidy.'''
    # pylint: disable=R0903

    def __init__(self, options: Optional[Mapping[Text, Text]] = None) -> None:
        super().__init__()
        self.__options = options or {}

    def run_one(self, filename: Text,
                contents: bytes) -> Tuple[bytes, Sequence[Text]]:
        return (_lint_html(contents,
                           strict=bool(self.__options.get('strict', False))),
                ['html'])

    def run_all(
            self, filenames: Sequence[Text],
            contents_callback: Callable[[Text], bytes]
    ) -> Tuple[Mapping[Text, bytes], Mapping[Text, bytes], Sequence[Text]]:
        return {}, {}, []

    @property
    def name(self) -> Text:
        return 'html'


class PHPLinter(Linter):
    '''Runs the PHP Code Beautifier.'''
    # pylint: disable=R0903

    def __init__(self, options: Optional[Mapping[Text, Text]] = None) -> None:
        super().__init__()
        self.__options = options or {}
        standard = self.__options.get(
            'standard', os.path.join(git_tools.HOOK_TOOLS_ROOT,
                                     'phpcbf/Standards/OmegaUp/ruleset.xml'))
        self.__common_args = ['--encoding=utf-8',
                              '--standard=%s' % standard]

    def run_one(self, filename: Text,
                contents: bytes) -> Tuple[bytes, Sequence[Text]]:
        args = ([_which('phpcbf')] + self.__common_args
                + ['--stdin-path=%s' % filename])
        logging.debug('lint_php: Running %s', args)
        with subprocess.Popen(args, stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              cwd=git_tools.HOOK_TOOLS_ROOT) as proc:
            new_contents, stderr = proc.communicate(contents)
            retcode = proc.wait()

            if retcode != 0:
                logging.debug('lint_php: Return code %d, stderr = %s',
                              retcode, stderr)
                if not new_contents:
                    # phpcbf returns 1 if there was no change to the file. If
                    # there was an actual error, there won't be anything in
                    # stdout.
                    raise LinterException(stderr.decode('utf-8'))

            if new_contents != contents:
                # If phpcbf was able to fix anything, let's go with that
                # instead of running phpcs. Otherwise, phpcs will return
                # non-zero and the suggestions won't be used.
                return new_contents, ['php']

        # Even if phpcbf didn't find anything, phpcs might.
        args = ([_which('phpcs'), '-n', '-s', '-q'] + self.__common_args
                + ['--stdin-path=%s' % filename])
        logging.debug('lint_php: Running %s', args)
        with subprocess.Popen(args, stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              cwd=git_tools.HOOK_TOOLS_ROOT) as proc:
            stdout, _ = proc.communicate(contents)
            retcode = proc.wait()

            if retcode != 0:
                logging.debug('lint_php: Return code %d, stdout = %s',
                              retcode, stdout)
                raise LinterException(stdout.decode('utf-8').strip())

        return new_contents, ['php']

    def run_all(
            self, filenames: Sequence[Text],
            contents_callback: Callable[[Text], bytes]
    ) -> Tuple[Mapping[Text, bytes], Mapping[Text, bytes], Sequence[Text]]:
        return {}, {}, []

    @property
    def name(self) -> Text:
        return 'php'


class PythonLinter(Linter):
    '''Runs pycodestyle, pylint, and Mypy.'''
    # pylint: disable=R0903

    def __init__(self, options: Optional[Mapping[Text, Text]] = None) -> None:
        super().__init__()
        self.__options = options or {}

    def run_one(self, filename: Text,
                contents: bytes) -> Tuple[bytes, Sequence[Text]]:
        with tempfile.TemporaryDirectory(prefix='python_linter_') as tmpdir:
            tmp_path = os.path.join(tmpdir, os.path.basename(filename))
            with open(tmp_path, 'wb') as pyfile:
                pyfile.write(contents)

            python3 = _which('python3')

            args = [python3, '-m', 'pycodestyle', tmp_path]
            for configname in ('pycodestyle_config', 'pep8_config'):
                if configname not in self.__options:
                    continue
                args.append('--config=%s' % self.__options[configname])
                break
            try:
                logging.debug('lint_python: Running %s', args)
                subprocess.check_output(args, stderr=subprocess.STDOUT)
            except subprocess.CalledProcessError as cpe:
                raise LinterException(
                    str(cpe.output, encoding='utf-8').replace(tmp_path,
                                                              filename),
                    fixable=False)

            # We need to disable import-error since the file won't be checked
            # in the repository, but in a temporary directory.
            args = [python3, '-m', 'pylint', '--output-format=parseable',
                    '--reports=no', '--disable=import-error', tmp_path]
            if 'pylint_config' in self.__options:
                args.append('--rcfile=%s' % self.__options['pylint_config'])
            try:
                logging.debug('lint_python: Running %s', args)
                subprocess.check_output(args, stderr=subprocess.STDOUT)
            except subprocess.CalledProcessError as cpe:
                raise LinterException(
                    str(cpe.output, encoding='utf-8').replace(tmp_path,
                                                              filename),
                    fixable=False)

            if self.__options.get('mypy', False):
                args = [
                    _which('mypy'), '--strict', '--no-incremental', filename
                ]
                try:
                    logging.debug('lint_python: Running %s', args)
                    subprocess.check_output(args, stderr=subprocess.STDOUT)
                except subprocess.CalledProcessError as cpe:
                    raise LinterException(
                        str(cpe.output, encoding='utf-8'), fixable=False)

            return contents, []

    def run_all(
            self, filenames: Sequence[Text],
            contents_callback: Callable[[Text], bytes]
    ) -> Tuple[Mapping[Text, bytes], Mapping[Text, bytes], Sequence[Text]]:
        return {}, {}, []

    @property
    def name(self) -> Text:
        return 'python'


class CustomLinter(Linter):
    '''A lazily, dynamically-loaded linter.'''

    def __init__(self, custom_linter: Mapping[Text, Text],
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
        custom_linter_module = importlib.util.module_from_spec(
            custom_linter_module_spec)
        custom_linter_module_spec.loader.exec_module(  # type: ignore
            custom_linter_module)
        self.__instance = getattr(custom_linter_module,
                                  self.__config['class_name'])(self.__options)
        return self.__instance

    def __call__(self, options: Optional[Mapping[Text, Text]] = None
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

    def run_one(self, filename: Text,
                contents: bytes) -> Tuple[bytes, Sequence[Text]]:
        return self._instance.run_one(filename, contents)

    def run_all(
            self, filenames: Sequence[Text],
            contents_callback: Callable[[Text], bytes]
    ) -> Tuple[Mapping[Text, bytes], Mapping[Text, bytes], Sequence[Text]]:
        return self._instance.run_all(filenames, contents_callback)

    @property
    def name(self) -> Text:
        return self._instance.name


class CommandLinter(Linter):
    '''Runs a custom command as linter.'''
    # pylint: disable=R0903

    def __init__(self, options: Optional[Mapping[Text, Any]] = None) -> None:
        super().__init__()
        self.__options = options or {}

    def run_one(self, filename: Text,
                contents: bytes) -> Tuple[bytes, Sequence[Text]]:
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
                    subprocess.check_output(args, stderr=subprocess.STDOUT)
                except subprocess.CalledProcessError as cpe:
                    raise LinterException(str(cpe.output, encoding='utf-8'),
                                          fixable=False)

            with open(tmp.name, 'rb') as tmp_in:
                return tmp_in.read(), ['command']

    def run_all(
            self, filenames: Sequence[Text],
            contents_callback: Callable[[Text], bytes]
    ) -> Tuple[Mapping[Text, bytes], Mapping[Text, bytes], Sequence[Text]]:
        return {}, {}, []

    @property
    def name(self) -> Text:
        return 'command (%s)' % (self.__options.get('commands', []),)


# vim: tabstop=4 expandtab shiftwidth=4 softtabstop=4
