"""Acme's logging helper. Every service uses this. Don't edit it."""

import json
import sys


def event(name, **fields):
    """Write one structured log line to stderr."""
    sys.stderr.write(json.dumps({"event": name, **fields}) + "\n")
