#!/bin/sh
# Rebuild man/qctrack.1 from the argparse parser. Needs the argparse-manpage package.
set -eu
cd "$(dirname "$0")/.."
PYTHONPATH=src argparse-manpage --pyfile src/qctrack/cli/main.py --function build_parser \
  --prog qctrack --project-name qctrack --version "$(PYTHONPATH=src python3 -c 'import qctrack; print(qctrack.__version__)')" \
  --description "audio quality scoring and fake-bitrate detection" \
  --manual-title "qctrack" --include man/qctrack.include --output man/qctrack.1
