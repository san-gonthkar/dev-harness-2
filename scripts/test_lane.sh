#!/usr/bin/env sh
# Run a Dev Harness test lane: smoke | full | nightly | coverage
#
#   smoke    - FAST lane for regular/daily runs (excludes NIGHTLY markers,
#              coverage-padding `*gaps*` files, spikes, and meta-tests).
#   full     - the ENTIRE suite. Run manually / on demand.
#   nightly  - only the NIGHTLY tier (timing/slow/e2e).
#   coverage - full suite with branch coverage.
#
# Usage: scripts/test_lane.sh [smoke|full|nightly|coverage] [extra pytest args...]
set -eu

lane="${1:-smoke}"
if [ "$#" -gt 0 ]; then
    shift
fi

cd "$(dirname "$0")/.."

case "$lane" in
    smoke)
        set -- tests -q -m "not timing and not slow and not e2e" \
            --ignore-glob="**/*gaps*.py" --ignore=tests/spikes --ignore=tests/tooling \
            --timeout=120 "$@"
        ;;
    full)
        set -- tests -q "$@"
        ;;
    nightly)
        set -- tests -q -m "timing or slow or e2e" "$@"
        ;;
    coverage)
        set -- tests -q --cov=dev_harness --cov-branch --cov-report=term-missing "$@"
        ;;
    *)
        echo "unknown lane: $lane (expected smoke|full|nightly|coverage)" >&2
        exit 2
        ;;
esac

echo "test lane: $lane"
exec python -m pytest "$@"