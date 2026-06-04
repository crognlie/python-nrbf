# python-nrbf

[![PyPI version](https://img.shields.io/pypi/v/nrbf.svg)](https://pypi.org/project/nrbf/)
[![Python versions](https://img.shields.io/pypi/pyversions/nrbf.svg)](https://pypi.org/project/nrbf/)
[![License](https://img.shields.io/pypi/l/nrbf.svg)](LICENSE)

A pure-Python parser for .NET BinaryFormatter streams ([MS-NRBF spec](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-nrbf/)).  No dependencies beyond the standard library.

## Installation

```sh
pip install nrbf
```

## Library usage

```python
import nrbf

# From a file
with open("data.dat", "rb") as f:
    data = nrbf.load(f)

# From bytes
data = nrbf.loads(raw_bytes)
```

Both functions return plain Python objects — `dict`, `list`, `str`, `int`, `float`, `bool`, or `None`.  The `.NET` type name is preserved in a `__class__` key on dicts so you can identify types if needed.

`System.Collections.Generic.List<T>` and `Dictionary<K,V>` are automatically unwrapped to Python `list` and `dict`.

## Command-line usage

`nrbfdump` is installed automatically with the package.  It reads a BinaryFormatter file (or stdin) and prints it.  It automatically decompresses gzip input.

```
usage: nrbfdump [FILE] [--format FORMAT]

positional arguments:
  FILE                  input file (default: stdin)

options:
  -h, --help            show this help message and exit
  --format, -f          json (default), pprint, yaml
```

Examples:

```sh
# Decode a file to JSON
nrbfdump data.dat

# Pipe a gzip-compressed file
cat data.dat.gz | nrbfdump

# Pretty-print for quick inspection
nrbfdump data.dat --format pprint

# YAML output (requires: pip install pyyaml)
nrbfdump data.dat --format yaml
```

## What's decoded

All MS-NRBF record types are supported:

- All 16 primitive types (`bool`, `int`, `float`, `double`, `DateTime`, `TimeSpan`, `Char`, etc.)
- Strings, object references, nulls
- Single-dimensional and multi-dimensional arrays (primitive, object, string)
- Classes with full member type info (`ClassWithMembersAndTypes`, `SystemClassWithMembersAndTypes`, `ClassWithId`)
- `System.Collections.Generic.List<T>` → `list`
- `System.Collections.Generic.Dictionary<K,V>` → `dict`

## License

MIT — see [LICENSE](LICENSE).
