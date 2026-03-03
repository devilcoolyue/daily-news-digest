#!/usr/bin/env bash
set -euo pipefail

PYTHONPATH=src python3 -m daily_infographic.cli run --domain ai "$@"
