"""Decode .NET BinaryFormatter files to JSON (or other formats).

Reads from a file path or stdin.  Automatically decompresses gzip input.

Usage:
    decode.py [FILE] [--format FORMAT]

    FILE        Path to input file.  Omit to read from stdin.
    --format    Output format: json (default), pprint, yaml
"""

import argparse
import gzip
import json
import math
import pprint
import sys

import nrbf

GZIP_MAGIC = b'\x1f\x8b'


def _decompress_if_needed(data: bytes) -> bytes:
    if data[:2] == GZIP_MAGIC:
        return gzip.decompress(data)
    return data


def _json_default(o):
    if isinstance(o, float) and not math.isfinite(o):  # NaN, Inf, -Inf
        return None
    return str(o)


def _output_json(data):
    print(json.dumps(data, indent=2, default=_json_default))


def _output_pprint(data):
    pprint.pprint(data, sort_dicts=False)


def _output_yaml(data):
    try:
        import yaml
    except ImportError:
        print("error: 'yaml' format requires PyYAML: pip install pyyaml", file=sys.stderr)
        sys.exit(1)
    print(yaml.dump(data, allow_unicode=True, sort_keys=False), end='')


FORMATS = {
    'json':   _output_json,
    'pprint': _output_pprint,
    'yaml':   _output_yaml,
}


def main():
    parser = argparse.ArgumentParser(
        description='Decode a .NET BinaryFormatter file to a chosen output format.'
    )
    parser.add_argument(
        'file', nargs='?',
        help='Input file (default: stdin)'
    )
    parser.add_argument(
        '--format', '-f', choices=FORMATS, default='json',
        help='Output format (default: json)'
    )
    args = parser.parse_args()

    if args.file:
        with open(args.file, 'rb') as f:
            raw = f.read()
    else:
        raw = sys.stdin.buffer.read()

    raw  = _decompress_if_needed(raw)
    data = nrbf.loads(raw)
    FORMATS[args.format](data)


if __name__ == '__main__':
    main()
