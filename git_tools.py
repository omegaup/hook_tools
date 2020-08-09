#!/usr/bin/python3

'''
Utility functions used to write git hooks.
'''

import argparse
import logging
import multiprocessing
import os
import os.path
import pipes
import re
import subprocess
import sys
from typing import (Any, Iterable, Iterator, List, Mapping, Optional, Sequence,
                    Text)


HOOK_TOOLS_ROOT = os.path.abspath(os.path.join(__file__, '..'))
GIT_DIFF_TREE_PATTERN = re.compile(
    br'^:\d+ (\d+) [0-9a-f]+ [0-9a-f]+ ([ACDMRTUX])\d*$')
GIT_LS_TREE_PATTERN = re.compile(br'^\d* blob [0-9a-f]+\t(.*)$')
GIT_NULL_HASH = '0000000000000000000000000000000000000000'
GIT_DIRECTORY_ENTRY_MODE = b'160000'


class COLORS:
    '''Constants for colors in bash.'''
    # pylint: disable=R0903

    HEADER = '\033[95m'
    OKGREEN = '\033[92m'
    FAIL = '\033[91m'
    NORMAL = '\033[0m'


class Argument:
    '''Class that represents a single argument for argparse.ArgumentParser.'''
    # pylint: disable=R0903

    def __init__(self, *args: Any, **kwargs: Any):
        self.args = args
        self.kwargs = kwargs

    def add_to(self, parser: argparse.ArgumentParser) -> None:
        '''Adds an additional argument to the parser.'''

        parser.add_argument(*self.args, **self.kwargs)


def get_explicit_file_list(commits: List[Text]) -> Sequence[Text]:
    '''Returns the explicit file list from the commandline.

    Developers might want to use an explicit file list in case there is a file
    with the same name as a commit. The way git disambiguates is that arguments
    that come before -- are references, and the ones that come after are files.
    We use the same convention.
    '''
    # If a -- was explicitly passed, honor it and don't try to guess what is
    # what.
    try:
        idx = commits.index('--')
        files = commits[idx + 1:]
        commits[idx:] = []
        return files
    except ValueError:
        pass

    # Otherwise let git-rev-parse let us know what are revisions and we treat
    # everything following the first non-revision as a file.
    try:
        commit_refs_output = subprocess.run(
            ['/usr/bin/git', 'rev-parse', '--revs-only'] + commits,
            text=True,
            check=True,
            stdout=subprocess.PIPE,
            cwd=root_dir()).stdout.strip()
        if not commit_refs_output:
            commit_refs: List[Text] = []
        else:
            commit_refs = commit_refs_output.split('\n')
    except subprocess.CalledProcessError:
        commit_refs = []

    files = commits[len(commit_refs):]
    del commits[len(commit_refs):]
    return files


def _validate_args(args: argparse.Namespace, files: Sequence[Text]) -> bool:
    '''Validates whether args is valid.

    args.commits is valid if it has one commit (diffing from that commit
    against the working tree) or two commits.
    '''
    if args.all_files:
        if args.commits or files:
            print(
                '%s--all-files is incompatible with `commits` or `files`.%s' %
                (COLORS.FAIL, COLORS.NORMAL), file=sys.stderr)
            return False
    if len(args.commits) not in (0, 1, 2):
        # args.commits can never be empty since its default value is ['HEAD'],
        # but the user can specify zero commits.
        print('%sCan only specify zero, one or two commits.%s' %
              (COLORS.FAIL, COLORS.NORMAL),
              file=sys.stderr)
        return False
    return True


def _get_all_files() -> Iterator[bytes]:
    '''Returns the list of all files at HEAD (and maybe in the index).'''

    for path in subprocess.run(['/usr/bin/git', 'ls-files', '-z'],
                               check=True,
                               stdout=subprocess.PIPE,
                               cwd=root_dir()).stdout.split(b'\x00'):
        if os.path.isfile(path):
            yield path


def _get_changed_files(commits: List[Text]) -> Iterator[bytes]:
    ''' Returns the list of files that were modified in the specified range.'''

    if not commits:
        cmd = ['/usr/bin/git', 'diff-index', '-z', '--diff-filter=d', 'HEAD']
    elif len(commits) == 1:
        cmd = ['/usr/bin/git', 'diff-index', '-z', '--diff-filter=d'] + commits
    else:
        if commits[-1] == GIT_NULL_HASH:
            # If the second commit is the null hash, the branch is being
            # deleted, so no files should be considered.
            return
        cmd = ['/usr/bin/git', 'diff-tree', '-r', '-z',
               '--diff-filter=d'] + commits
    tokens = subprocess.run(cmd,
                            check=True,
                            stdout=subprocess.PIPE,
                            cwd=root_dir()).stdout.split(b'\x00')
    idx = 0
    while idx < len(tokens) - 1:
        match = GIT_DIFF_TREE_PATTERN.match(tokens[idx])
        assert match, tokens[idx]
        filemode, status = match.groups()
        if filemode == GIT_DIRECTORY_ENTRY_MODE:
            # Files with the 160000 mode are not actually files or
            # directories.  They just are directory entries, and they
            # typically appear in the path where submodules are inserted
            # into the tree.
            idx += 2
            continue
        src = tokens[idx + 1]
        if status in (b'C', b'R'):
            dest = tokens[idx + 2]
            idx += 3
            yield dest
        else:
            idx += 2
            yield src


def _files_to_consider(args: argparse.Namespace) -> List[Text]:
    '''Returns the list of files to consider, based on |args|' commits.'''

    # Get all files in the latter commit.
    if args.all_files:
        result = _get_all_files()
    else:
        result = _get_changed_files(args.commits)

    return sorted([str(filename, encoding='utf-8') for filename in result])


def prompt(question: Text, default: bool = True) -> bool:
    '''Asks the user a yes/no question.'''
    if sys.stdin.closed or not sys.stdin.isatty():
        return default

    while True:
        yes_str = 'yes'
        no_str = 'no'
        yes_label = yes_str
        no_label = no_str
        if default:
            yes_label = yes_label.upper()
        else:
            no_label = no_label.upper()

        try:
            response = input('%s (%s/%s): ' % (question, yes_label, no_label))
        except EOFError:
            return default

        response = response.strip().lower()
        if not response:
            break
        if yes_str.startswith(response):
            return True
        if no_str.startswith(response):
            return False

    return default


def file_contents(args: argparse.Namespace, root: Text,
                  filename: Text) -> bytes:
    '''Returns contents of |filename| at the revision specified by |args|.'''
    if len(args.commits) in (0, 1):
        # Zero or one commits (where the former is a shorthand for 'HEAD')
        # always diff against the current contents of the file in the
        # filesystem.
        with open(os.path.join(root, filename), 'rb') as working_dir_file:
            return working_dir_file.read()
    else:
        return subprocess.run(
            ['/usr/bin/git', 'show',
             '%s:%s' % (args.commits[-1], filename)],
            check=True,
            stdout=subprocess.PIPE,
            cwd=root).stdout


def root_dir() -> Text:
    '''Returns the top-level directory of the project.'''
    return subprocess.run(['/usr/bin/git', 'rev-parse', '--show-toplevel'],
                          text=True,
                          check=True,
                          stdout=subprocess.PIPE).stdout.strip()


def parse_arguments(
        tool_description: Optional[Text] = None,
        extra_arguments: Sequence[Argument] = ()) -> argparse.Namespace:
    '''Parses the commandline arguments.'''
    parser = argparse.ArgumentParser(description=tool_description)
    parser.add_argument(
        '--verbose', '-v', action='store_true',
        help='Prints verbose information')
    parser.add_argument(
        '--config-file',
        default=os.path.join(root_dir(), '.lint.config.json'),
        help='Prints verbose information')
    parser.add_argument(
        '--continuous-integration', action='store_true',
        help=('Assumes this is an unsupervised environment. '
              'Disables all prompts.'))
    parser.add_argument(
        '--jobs', '-j', type=int, help='Number of parallel jobs',
        default=multiprocessing.cpu_count())
    for argument in extra_arguments:
        argument.add_to(parser)
    subparsers = parser.add_subparsers(dest='tool')
    subparsers.required = True

    validate_parser = subparsers.add_parser(
        'validate', help='Only validates, does not make changes')
    validate_parser.add_argument(
        '--all-files', action='store_true',
        help='Considers all files. Incompatible with `commits` and `files`')
    validate_parser.add_argument(
        'commits',
        metavar='[commit [commit ...]] [--] [file [file ...]]',
        nargs=argparse.REMAINDER,
        default=[],
        type=str,
        help=('commit: Only include files changed between commits\n'
              'file:   If specified, only consider these files'))

    fix_parser = subparsers.add_parser(
        'fix',
        help=('Fixes all violations and leaves '
              'the results in the working tree.'))
    fix_parser.add_argument(
        '--all-files', action='store_true',
        help=('Considers all files. '
              'Incompatible with `commits` and `files`'))
    fix_parser.add_argument(
        'commits',
        metavar='[commit [commit ...]] [--] [file [file ...]]',
        nargs=argparse.REMAINDER,
        default=[],
        type=str,
        help=('commit: Only include files changed between commits\n'
              'file:   If specified, only consider these files'))

    args = parser.parse_args()
    files = get_explicit_file_list(args.commits)
    if not _validate_args(args, files):
        sys.exit(1)
    if files:
        args.files = files
    else:
        args.files = list(_files_to_consider(args))

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)
    logging.debug('Files to consider: %s', ' '.join(args.files))
    return args


def _get_fix_args(prog_args: List[Text],
                  args: argparse.Namespace,
                  files: Optional[Iterable[Text]] = None) -> Sequence[Text]:
    '''Gets the command arguments to run to fix violations.'''
    params = prog_args + ['fix']
    params.extend(args.commits)
    if not files and args.files:
        files = args.files
    if files:
        params.append('--')
        params.extend(sorted(files))
    return params


def get_fix_commandline(prog_args: List[Text],
                        args: argparse.Namespace,
                        files: Optional[Iterable[Text]] = None) -> Text:
    '''Gets the commandline the developer must run to fix violations.'''
    full_args = (
        prog_args + [pipes.quote(p) for p in _get_fix_args([], args, files)])
    return ' '.join(full_args)


def verify_toolchain(binaries: Mapping[Text, Text]) -> bool:
    '''Verifies that the developer has all necessary tools installed.'''
    success = True
    for path, install_cmd in binaries.items():
        if not os.path.isfile(path):
            print('%s%s not found.%s ' 'Please run `%s` to install.' %
                  (COLORS.FAIL, path, COLORS.NORMAL, install_cmd),
                  file=sys.stderr)
            success = False
    return success


def _is_single_commit_pushed(args: argparse.Namespace) -> bool:
    '''Returns whether a single commit is being pushed.'''
    if len(args.commits) != 2:
        return False
    return str(args.commits[0]) == subprocess.run(
        [
            '/usr/bin/git',
            'rev-parse',
            '%s^' % args.commits[1],
        ],
        text=True,
        check=True,
        stdout=subprocess.PIPE).stdout.strip()


def attempt_automatic_fixes(scriptname: Text,
                            args: argparse.Namespace,
                            files: Optional[Iterable[Text]] = None,
                            pre_upload: bool = False) -> bool:
    '''Attempts to automatically fix any fixable errors.'''
    if sys.stdin.closed or not sys.stdin.isatty():
        # There is no one to ask.
        return False
    if not prompt('Want to automatically fix errors?'):
        # User decided not to go with the fixes.
        return False
    # This should always "fail" because it's designed to block `git push`.  We
    # cannot use check_call() for that reason. We also always use the
    # in-container version of the invocation.
    subprocess.call(_get_fix_args([scriptname], args, files))
    if not subprocess.run(['/usr/bin/git', 'status', '--porcelain'],
                          check=True,
                          stdout=subprocess.PIPE).stdout.strip():
        # The fix failed?
        return False
    if pre_upload:
        continue_message = 'please run the `git push` command again.'
    else:
        continue_message = 'ready to upload.'
    if not prompt('Want to also commit the fixes?'):
        # Fixes succeeded, even if they are not committed yet.
        print('Files written to working directory. '
              '%sPlease commit them before pushing.%s' %
              (COLORS.HEADER, COLORS.NORMAL), file=sys.stderr)
        return True
    if _is_single_commit_pushed(args):
        # We can amend the previous commit!
        commit_params = ['/usr/bin/git', 'commit', '--amend', '--no-edit']
        if files:
            commit_params.append('--')
            commit_params.extend(files)
        else:
            commit_params.append('--all')
        subprocess.run(commit_params, check=True)
        print('%sPrevious commit reused, %s%s' %
              (COLORS.OKGREEN, continue_message, COLORS.NORMAL),
              file=sys.stderr)
    else:
        commit_params = ['/usr/bin/git', 'commit',
                         '-m', 'Fixed %s lints' % scriptname]
        if files:
            commit_params.append('--')
            commit_params.extend(files)
        else:
            commit_params.append('--all')
        subprocess.run(commit_params, check=True)
        print('%sCommitted fixes, %s%s' %
              (COLORS.OKGREEN, continue_message, COLORS.NORMAL),
              file=sys.stderr)
    return True


# vim: tabstop=4 expandtab shiftwidth=4 softtabstop=4
