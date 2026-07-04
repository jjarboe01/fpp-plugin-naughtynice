#!/bin/bash
# Ensure no previous daemon instance is still running before postStart.sh
# launches a new one (e.g. after a crash left a stale PID file).

. "$(dirname "$0")/_lib.sh"

nnl_stop_if_running

#preStart
