'''Linters for various languages.'''

from __future__ import print_function

from abc import ABCMeta, abstractmethod
from html.parser import HTMLParser
import importlib.util
import logging
import os
import os.path
import re
import subprocess
import sys
import tempfile

from . import git_tools  # pylint: disable=relative-beyond-top-level


def _find_pip_tool(name):
    '''Tries to find a pip tool in a few default locations.'''
    for prefix in ['/usr/bin', '/usr/local/bin']:
        toolpath = os.path.join(prefix, name)
        if os.path.exists(toolpath):
            return toolpath
    return os.path.join(os.environ['HOME'], '.local/bin', name)


def _which(program):
    '''Looks for |program| in $PATH. Similar to UNIX's `which` command.'''
    for path in os.environ['PATH'].split(os.pathsep):
        exe_file = os.path.join(path.strip('"'), program)
        if os.path.isfile(exe_file) and os.access(exe_file, os.X_OK):
            return exe_file
    raise Exception('`%s` not found' % program)


_CLANG_FORMAT_PATH = '/usr/bin/clang-format-3.7'
# pylint: disable=fixme
# TODO(lhchavez): Use closure compiler instead since closure-linter does not
# support ES6 correctly.
_FIXJSSTYLE_PATH = _find_pip_tool('fixjsstyle')
_TIDY_PATH = os.path.join(git_tools.HOOK_TOOLS_ROOT, 'tidy')

_JAVASCRIPT_TOOLCHAIN_VERIFIED = False


class LinterException(Exception):
    '''A fatal exception during linting.'''

    def __init__(self, message, fixable=True):
        super().__init__(message)
        self.__message = message
        self.__fixable = fixable

    @property
    def message(self):
        '''A message that can be presented to the user.'''
        return self.__message

    @property
    def fixable(self):
        '''Whether this exception supports being fixed.'''
        return self.__fixable


def _custom_command(command, filename, original_filename):
    '''A custom command.'''

    try:
        from shlex import quote
    except ImportError:
        from pipes import quote

    return ['/bin/bash', '-c', '%s %s %s' %
            (command, quote(filename), quote(original_filename))]


def _lint_javascript(filename, contents, extra_commands=None):
    '''Runs clang-format and the Google Closure Compiler on |contents|.'''

    global _JAVASCRIPT_TOOLCHAIN_VERIFIED  # pylint: disable=global-statement

    if not _JAVASCRIPT_TOOLCHAIN_VERIFIED and not git_tools.verify_toolchain({
            _CLANG_FORMAT_PATH: 'sudo apt-get install clang-format-3.7',
            _FIXJSSTYLE_PATH: (
                'pip install --user '
                'https://github.com/google/closure-linter/zipball/master'),
    }):
        sys.exit(1)

    _JAVASCRIPT_TOOLCHAIN_VERIFIED = True

    with tempfile.NamedTemporaryFile(suffix='.js') as js_out:
        # Keep the shebang unmodified.
        header = b''
        if contents.startswith(b'#!'):
            header, contents = contents.split(b'\n', 1)
            header += b'\n'

        js_out.write(contents)
        js_out.flush()

        commands = [
            [_FIXJSSTYLE_PATH, '--strict', js_out.name],
            [_CLANG_FORMAT_PATH, '-style=Google',
             '-assume-filename=%s' % filename, '-i', js_out.name],
        ] + [
            _custom_command(command, js_out.name, filename)
            for command in (extra_commands or [])
        ]

        for args in commands:
            logging.debug('lint_javascript: Running %s', args)
            subprocess.check_output(args, stderr=subprocess.STDOUT)

        with open(js_out.name, 'rb') as js_in:
            return header + js_in.read()


def _lint_typescript(filename, contents):
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

        raise LinterException(stderr)


def _lint_html(contents, strict):
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

        raise LinterException(stderr)


class Linter:
    '''An abstract Linter.'''
    # pylint: disable=R0903

    __metaclass__ = ABCMeta

    def __init__(self):
        pass

    @abstractmethod
    def run_one(self, filename, contents):
        '''Runs the linter against |contents|.'''

    @abstractmethod
    def run_all(self, file_contents, contents_callback):
        '''Runs the linter against a subset of files.'''

    @property
    def name(self):
        '''Gets the name of the linter.'''
        return 'linter'


class JavaScriptLinter(Linter):
    '''Runs the Google Closure Compiler linter+prettier against |files|.'''
    # pylint: disable=R0903

    def __init__(self, options=None):
        super().__init__()
        self.__options = options or {}

    def run_one(self, filename, contents):
        try:
            return (_lint_javascript(filename, contents,
                                     self.__options.get('extra_js_linters')),
                    ['javascript'])
        except subprocess.CalledProcessError as cpe:
            raise LinterException(str(b'\n'.join(cpe.output.split(b'\n')[1:]),
                                      encoding='utf-8'))

    def run_all(self, file_contents, contents_callback):
        return [], [], []

    @property
    def name(self):
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

    def __init__(self, options=None):
        super().__init__()
        self.__options = options or {}

    def run_one(self, filename, contents):
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

    def run_all(self, file_contents, contents_callback):
        return [], [], []

    @property
    def name(self):
        return 'whitespace'


class VueHTMLParser(HTMLParser):
    '''A parser that can understand .vue template files.'''
    # pylint: disable=R0903

    def __init__(self):
        super(VueHTMLParser, self).__init__()
        self._stack = []
        self._tags = []
        self._id_linter_enabled = True

    def error(self, message):
        raise LinterException(message)

    def parse(self, contents):
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

    def handle_starttag(self, tag, attrs):
        line, col = self.getpos()
        self._stack.append((tag, attrs, self.get_starttag_text(), (line - 1,
                                                                   col)))
        if not self._id_linter_enabled:
            return
        for name, _ in attrs:
            if name == 'id':
                raise LinterException(
                    ('Use of "id" attribute in .vue files is '
                     'discouraged. Found one in line %d\n') % (line),
                    fixable=False)

    def handle_endtag(self, tag):
        while self._stack and self._stack[-1][0] != tag:
            self._stack.pop()
        if not self._stack or self._stack[-1][0] != tag:
            raise LinterException(
                'Unclosed tag at line %d, column %d' % self.getpos())
        _, attrs, starttag, begin = self._stack.pop()
        if not self._stack:
            line, col = self.getpos()
            self._tags.append((tag, attrs, starttag, begin, (line - 1, col)))

    def handle_comment(self, data):
        if data.find('id-lint ') < 0:
            return
        self._id_linter_enabled = data.strip().split()[1] == 'on'


class VueLinter(Linter):
    '''A linter for .vue files.'''
    # pylint: disable=R0903

    def __init__(self, options=None):
        super().__init__()
        self.__options = options or {}

    def run_one(self, filename, contents):
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
                        strict=self.__options.get('strict',
                                                  False)).split(b'\n')
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

    def run_all(self, file_contents, contents_callback):
        return [], [], []

    @property
    def name(self):
        return 'vue'


class HTMLLinter(Linter):
    '''Runs HTML Tidy.'''
    # pylint: disable=R0903

    def __init__(self, options=None):
        super().__init__()
        self.__options = options or {}

    def run_one(self, filename, contents):
        return (_lint_html(contents,
                           strict=self.__options.get('strict', False)),
                ['html'])

    def run_all(self, file_contents, contents_callback):
        return [], [], []

    @property
    def name(self):
        return 'html'


class PHPLinter(Linter):
    '''Runs the PHP Code Beautifier.'''
    # pylint: disable=R0903

    def __init__(self, options=None):
        super().__init__()
        self.__options = options or {}
        standard = self.__options.get(
            'standard', os.path.join(git_tools.HOOK_TOOLS_ROOT,
                                     'phpcbf/Standards/OmegaUp/ruleset.xml'))
        self.__common_args = ['--encoding=utf-8',
                              '--standard=%s' % standard]

    def run_one(self, filename, contents):
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

            if retcode != 0 and not new_contents:
                # phpcbf returns 1 if there was no change to the file. If there
                # was an actual error, there won't be anything in stdout.
                raise LinterException(stderr.decode('utf-8'))

        # Even if phpcbf didn't find anything, phpcs might.
        args = ([_which('phpcs'), '-n'] + self.__common_args
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

    def run_all(self, file_contents, contents_callback):
        return [], [], []

    @property
    def name(self):
        return 'php'


class PythonLinter(Linter):
    '''Runs pycodestyle and pylint.'''
    # pylint: disable=R0903

    def __init__(self, options=None):
        super().__init__()
        self.__options = options or {}

    def run_one(self, filename, contents):
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

            return contents, []

    def run_all(self, file_contents, contents_callback):
        return [], [], []

    @property
    def name(self):
        return 'python'


class CustomLinter(Linter):
    '''A lazily, dynamically-loaded linter.'''

    def __init__(self, custom_linter, config_file_path):
        super().__init__()
        self.__module_path = os.path.join(
            os.path.dirname(config_file_path), custom_linter['path'])
        self.__config = custom_linter
        self.__instance = None
        self.__options = {}

    def _ensure_loaded(self):
        if self.__instance:
            return
        custom_linter_module_spec = importlib.util.spec_from_file_location(
            self.__module_path.rstrip('.py').replace('/', '_'),
            self.__module_path)
        custom_linter_module = importlib.util.module_from_spec(
            custom_linter_module_spec)
        custom_linter_module_spec.loader.exec_module(custom_linter_module)
        self.__instance = getattr(custom_linter_module,
                                  self.__config['class_name'])(self.__options)

    def __call__(self, options=None):
        # Instead of the constructor being stored in the map of available
        # linters, a live instance of this class is stored. Later, this
        # function is called in lieu of the constructor.
        if options:
            self.__options = options
        return self

    def __getstate__(self):
        # This is needed to prevent self.__instance from being pickled, which
        # is something that is not supported.
        return {
            '_CustomLinter__config': self.__config,
            '_CustomLinter__module_path': self.__module_path,
            '_CustomLinter__options': self.__options,
            '_CustomLinter__instance': None,
        }

    def run_one(self, filename, contents):
        self._ensure_loaded()
        return self.__instance.run_one(filename, contents)

    def run_all(self, file_contents, contents_callback):
        self._ensure_loaded()
        return self.__instance.run_all(file_contents, contents_callback)

    @property
    def name(self):
        self._ensure_loaded()
        return self.__instance.name


class CommandLinter(Linter):
    '''Runs a custom command as linter.'''
    # pylint: disable=R0903

    def __init__(self, options=None):
        super().__init__()
        self.__options = options or {}

    def run_one(self, filename, contents):
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

    def run_all(self, file_contents, contents_callback):
        return [], [], []

    @property
    def name(self):
        return 'command (%s)' % (self.__options.get('commands', []),)


# vim: tabstop=4 expandtab shiftwidth=4 softtabstop=4
