#!/bin/bash

set -e
set -x

DIAGNOSTICS_OUTPUT=stderr

while [[ $# -gt 0 ]]; do
  case "${1}" in
    --diagnostics-output=*)
      DIAGNOSTICS_OUTPUT="${1#*=}"
      ;;
    *)
      echo "Unrecognized option \"${1}\""
      exit 1
      ;;
  esac
  shift
done

/usr/bin/python3 linters_test.py
/usr/bin/python3 git_tools_test.py
MYPYPATH="${PWD}/.." /usr/bin/python3 \
  lint.py \
  "--diagnostics-output=${DIAGNOSTICS_OUTPUT}" \
  validate --all
