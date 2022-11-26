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

python3 -m pytest
python3 -m hook_tools \
  "--diagnostics-output=${DIAGNOSTICS_OUTPUT}" \
  validate --all
