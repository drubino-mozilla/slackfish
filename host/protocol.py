"""Native messaging wire protocol for Firefox extensions.

Messages are JSON encoded, prefixed with a 4-byte little-endian length.
"""

import json
import struct
import sys

# On Windows, stdin/stdout must be binary to avoid CRLF mangling.
if sys.platform == "win32":
    import os
    import msvcrt

    msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
    msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)


def read_message():
    """Read a single native messaging message from stdin. Returns None on EOF."""
    raw_length = sys.stdin.buffer.read(4)
    if len(raw_length) < 4:
        return None
    length = struct.unpack("<I", raw_length)[0]
    raw_message = sys.stdin.buffer.read(length)
    if len(raw_message) < length:
        return None
    return json.loads(raw_message.decode("utf-8"))


def write_message(msg):
    """Write a single native messaging message to stdout."""
    encoded = json.dumps(msg).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("<I", len(encoded)))
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()
