#!/usr/bin/env python3
"""Container healthcheck: pass only if the heartbeat file is fresh.

Exit 0 when the heartbeat exists and is younger than HEARTBEAT_MAX_AGE_SECONDS,
exit 1 otherwise. This is an operational liveness probe, not part of the
deterministic decision pipeline, so reading the wall clock here is appropriate.
"""

from __future__ import annotations

import os
import sys
import time

DEFAULT_HEARTBEAT = "/opt/hermes/logs/heartbeat.txt"


def main() -> int:
    path = os.environ.get("HERMES_HEARTBEAT_FILE", DEFAULT_HEARTBEAT)
    max_age = int(os.environ.get("HEARTBEAT_MAX_AGE_SECONDS", "600"))
    try:
        age = time.time() - os.path.getmtime(path)
    except OSError:
        print(f"unhealthy: no heartbeat at {path}", file=sys.stderr)
        return 1
    if age > max_age:
        print(f"unhealthy: heartbeat is {age:.0f}s old (max {max_age}s)", file=sys.stderr)
        return 1
    print(f"healthy: heartbeat {age:.0f}s old")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
