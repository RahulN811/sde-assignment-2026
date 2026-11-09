#!/usr/bin/env bash
# Contract: ./run.sh <SIM_URL> runs the controller until the scenario finishes.
set -euo pipefail
SIM_URL="${1:?usage: ./run.sh <SIM_URL>}"
echo "Starting assignment controller against $SIM_URL" >&2
exec python controller.py --url "$SIM_URL"
