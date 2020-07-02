#!/bin/bash

set -e
set -x

/usr/bin/python3 linters_test.py
/usr/bin/python3 git_tools_test.py
MYPYPATH="${PWD}/.." /usr/bin/python3 lint.py validate --all
