#!/usr/bin/python3

'''
Utility functions used to write git hooks.
'''

import argparse
import os
import os.path
import pipes
import re
import subprocess
import sys


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


def _get_explicit_file_list(args):
    '''Returns the explicit file list from the commandline.

    Developers might want to use an explicit file list in case there is a file
    with the same name as a commit. The way git disambiguates is that arguments
    that come before -- are references, and the ones that come after are files.
    We use the same convention.
    '''
    try:
        idx = args.index('--')
        files = args[idx + 1:]
        args[idx:] = []
        return files
    except ValueError:
        return []


def _validate_args(args, files):
    '''Validates whether args is valid.

    args.commits is valid if it has one commit (diffing from that commit
    against the working tree) or two commits.
    '''
    if args.all_files:
        if args.commits != ['HEAD'] or files:
            print(
                '%s--all-files is incompatible with `commits` or `files`.%s' %
                (COLORS.FAIL, COLORS.NORMAL), file=sys.stderr)
            return False
    if len(args.commits) not in (1, 2):
        # args.commits can never be empty since its default value is ['HEAD'],
        # but the user can specify zero commits.
        print('%sCan only specify zero, one or two commits.%s' %
              (COLORS.FAIL, COLORS.NORMAL),
              file=sys.stderr)
        return False
    return True


def _get_all_files():
    '''Returns the list of all files at HEAD.'''

    for line in subprocess.check_output(['/usr/bin/git', 'ls-tree', '-r',
                                         '-z', 'HEAD'],
                                        cwd=root_dir()).split(b'\x00'):
        match = GIT_LS_TREE_PATTERN.match(line)
        if not match:
            continue
        yield match.groups()[0]


def _get_changed_files(commits):
    ''' Returns the list of files that were modified in the specified range.'''

    if len(commits) == 1:
        cmd = ['/usr/bin/git', 'diff-index', '-z', '--diff-filter=d'] + commits
    else:
        if commits[-1] == GIT_NULL_HASH:
            # If the second commit is the null hash, the branch is being
            # deleted, so no files should be considered.
            return
        cmd = ['/usr/bin/git', 'diff-tree', '-r', '-z',
               '--diff-filter=d'] + commits
    tokens = subprocess.check_output(cmd, cwd=root_dir()).split(b'\x00')
    idx = 0
    while idx < len(tokens) - 1:
        match = GIT_DIFF_TREE_PATTERN.match(tokens[idx])
        assert match, tokens[idx]
        filemode, status = match.groups()
        src = tokens[idx + 1]
        if status in ('C', 'R'):
            dest = tokens[idx + 2]
            idx += 3
        else:
            dest = None
            idx += 2
        if filemode == GIT_DIRECTORY_ENTRY_MODE:
            # Files with the 160000 mode are not actually files or
            # directories.  They just are directory entries, and they
            # typically appear in the path where submodules are inserted
            # into the tree.
            continue
        if dest:
            yield dest
        else:
            yield src


def _files_to_consider(args):
    '''Returns the list of files to consider, based on |args|' commits.'''

    # Get all files in the latter commit.
    if args.all_files:
        result = _get_all_files()
    else:
        result = _get_changed_files(args.commits)

    return sorted([str(filename, encoding='utf-8') for filename in result])


def prompt(question, default=True):
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
            return default
        if yes_str.startswith(response):
            return True
        if no_str.startswith(response):
            return False


def file_contents(args, root, filename):
    '''Returns contents of |filename| at the revision specified by |args|.'''
    if len(args.commits) == 1:
        # Zero or one commits (where the former is a shorthand for 'HEAD')
        # always diff against the current contents of the file in the
        # filesystem.
        with open(os.path.join(root, filename), 'rb') as working_dir_file:
            return working_dir_file.read()
    else:
        return subprocess.check_output(
            ['/usr/bin/git', 'show', '%s:%s' % (args.commits[-1], filename)])


def root_dir():
    '''Returns the top-level directory of the project.'''
    return subprocess.check_output(
        ['/usr/bin/git', 'rev-parse', '--show-toplevel'],
        universal_newlines=True).strip()


def parse_arguments(tool_description=None):
    '''Parses the commandline arguments.'''
    parser = argparse.ArgumentParser(description=tool_description)
    parser.add_argument(
        '--verbose', action='store_true', help='Prints verbose information')
    parser.add_argument(
        '--config-file',
        default=os.path.join(root_dir(), '.lint.config.json'),
        help='Prints verbose information')
    parser.add_argument(
        '--continuous-integration', action='store_true',
        help=('Assumes this is an unsupervised environment. '
              'Disables all prompts.'))
    subparsers = parser.add_subparsers(dest='tool')
    subparsers.required = True

    validate_parser = subparsers.add_parser(
        'validate', help='Only validates, does not make changes')
    validate_parser.add_argument(
        '--all-files', action='store_true',
        help='Considers all files. Incompatible with `commits` and `files`')
    validate_parser.add_argument(
        'commits', metavar='commit', nargs='*', default=['HEAD'], type=str,
        help='Only include files changed between commits')
    validate_parser.add_argument('ignored', metavar='--', nargs='?')
    validate_parser.add_argument(
        'ignored', metavar='file', nargs='*',
        help='If specified, only consider these files')

    fix_parser = subparsers.add_parser(
        'fix',
        help=('Fixes all violations and leaves '
              'the results in the working tree.'))
    fix_parser.add_argument(
        '--all-files', action='store_true',
        help=('Considers all files. '
              'Incompatible with `commits` and `files`'))
    fix_parser.add_argument(
        'commits', metavar='commit', nargs='*', default=['HEAD'], type=str,
        help='Only include files changed between commits')
    fix_parser.add_argument('ignored', metavar='--', nargs='?')
    fix_parser.add_argument(
        'ignored', metavar='file', nargs='*',
        help='If specified, only consider these files')

    files = _get_explicit_file_list(sys.argv)
    args = parser.parse_args()
    if not _validate_args(args, files):
        sys.exit(1)
    if files:
        args.files = files
    else:
        args.files = list(_files_to_consider(args))
    if args.verbose:
        print('Files to consider: %s' % ' '.join(args.files),
              file=sys.stderr)
    return args


def _get_fix_args(progname, args, files=None):
    '''Gets the command arguments to run to fix violations.'''
    params = [progname, 'fix']
    params.extend(args.commits)
    if not files and args.files:
        files = args.files
    if files:
        params.append('--')
        params.extend(sorted(files))
    return params


def get_fix_commandline(progname, args, files=None):
    '''Gets the commandline the developer must run to fix violations.'''
    return ' '.join(pipes.quote(p)
                    for p in _get_fix_args(progname, args, files))


def verify_toolchain(binaries):
    '''Verifies that the developer has all necessary tools installed.'''
    success = True
    for path, install_cmd in binaries.items():
        if not os.path.isfile(path):
            print('%s%s not found.%s ' 'Please run `%s` to install.' %
                  (COLORS.FAIL, path, COLORS.NORMAL, install_cmd),
                  file=sys.stderr)
            success = False
    return success


def _is_single_commit_pushed(args):
    '''Returns whether a single commit is being pushed.'''
    if len(args.commits) != 2:
        return False
    return args.commits[0] == subprocess.check_output(
        ['/usr/bin/git', 'rev-parse', '%s^' % args.commits[1]],
        universal_newlines=True).strip()


def attempt_automatic_fixes(scriptname, args, files=None):
    '''Attempts to automatically fix any fixable errors.'''
    if sys.stdin.closed or not sys.stdin.isatty():
        # There is no one to ask.
        return False
    if not prompt('Want to automatically fix errors?'):
        # User decided not to go with the fixes.
        return False
    # This should always "fail" because it's designed to block `git push`.
    # We cannot use check_call() for that reason.
    subprocess.call(_get_fix_args(scriptname, args, files))
    if not subprocess.check_output(['/usr/bin/git',
                                    'status', '--porcelain']).strip():
        # The fix failed?
        return False
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
        subprocess.check_call(commit_params)
        print('%sPrevious commit reused, ready to upload.%s' %
              (COLORS.OKGREEN, COLORS.NORMAL), file=sys.stderr)
    else:
        commit_params = ['/usr/bin/git', 'commit',
                         '-m', 'Fixed %s lints' % scriptname]
        if files:
            commit_params.append('--')
            commit_params.extend(files)
        else:
            commit_params.append('--all')
        subprocess.check_call(commit_params)
        print('%sCommitted fixes, ready to upload.%s' %
              (COLORS.OKGREEN, COLORS.NORMAL), file=sys.stderr)
    return True


# vim: tabstop=4 expandtab shiftwidth=4 softtabstop=4
