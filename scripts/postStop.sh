#!/bin/bash
# Stop the daemon cleanly when fppd stops (SIGTERM, wait, then SIGKILL).

. "$(dirname "$0")/_lib.sh"

nnl_stop_if_running

#postStop
