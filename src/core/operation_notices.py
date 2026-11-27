"""Keep application notices distinct from subprocess diagnostics."""
import json
import sys

PREFIX = "SDPLUS-NOTICE "


def warn(message):
    print(PREFIX + json.dumps(message, ensure_ascii=False), file=sys.stderr)


def operation_notices(stderr):
    notices = []
    for line in stderr.splitlines():
        if line.startswith(PREFIX):
            try:
                message = json.loads(line[len(PREFIX):])
            except ValueError:
                continue
            if isinstance(message, str):
                notices.append(message)
    return "\n".join(notices)
