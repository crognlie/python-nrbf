"""Parse .NET BinaryFormatter streams (MS-NRBF spec).

Basic usage::

    import nrbf

    with open("data.dat", "rb") as f:
        data = nrbf.load(f)

    data = nrbf.loads(raw_bytes)

Both functions return a plain Python object (dict / list / str / int / float /
bool / None).  .NET collection types are unwrapped automatically:

* ``System.Collections.Generic.List<T>``         → ``list``
* ``System.Collections.Generic.Dictionary<K,V>`` → ``dict``

All other objects are returned as dicts with a ``__class__`` key that holds the
.NET type name, so callers can identify and handle them if needed.
"""

import io
import struct
from enum import IntEnum
from typing import IO, Any


__all__ = ["load", "loads", "NRBFParser", "ParseError"]


class ParseError(ValueError):
    """Raised when the input is not a valid BinaryFormatter stream."""


# ---------------------------------------------------------------------------
# Enumerations (MS-NRBF §2.1)
# ---------------------------------------------------------------------------

class _RecordType(IntEnum):
    SerializationHeader        = 0
    ClassWithId                = 1
    SystemClassWithMembers     = 2
    ClassWithMembers           = 3
    SystemClassWithMembersAndTypes = 4
    ClassWithMembersAndTypes   = 5
    BinaryObjectString         = 6
    BinaryArray                = 7
    MemberPrimitiveTyped       = 8
    MemberReference            = 9
    ObjectNull                 = 10
    MessageEnd                 = 11
    BinaryLibrary              = 12
    ObjectNullMultiple256      = 13
    ObjectNullMultiple         = 14
    ArraySinglePrimitive       = 15
    ArraySingleObject          = 16
    ArraySingleString          = 17


class _BinaryType(IntEnum):
    Primitive      = 0
    String         = 1
    Object         = 2
    SystemClass    = 3
    Class          = 4
    ObjectArray    = 5
    StringArray    = 6
    PrimitiveArray = 7


class _PrimitiveType(IntEnum):
    Boolean  = 1
    Byte     = 2
    Char     = 3
    Decimal  = 5
    Double   = 6
    Int16    = 7
    Int32    = 8
    Int64    = 9
    SByte    = 10
    Single   = 11
    TimeSpan = 12
    DateTime = 13
    UInt16   = 14
    UInt32   = 15
    UInt64   = 16
    Null     = 17
    String   = 18


class _BinaryArrayType(IntEnum):
    Single             = 0
    Jagged             = 1
    Rectangular        = 2
    SingleOffset       = 3
    JaggedOffset       = 4
    RectangularOffset  = 5


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

class NRBFParser:
    """Low-level parser for a single .NET BinaryFormatter byte stream.

    Prefer the module-level :func:`load` / :func:`loads` helpers unless you
    need access to the raw object table or library map.

    Attributes:
        objects:   dict mapping object ID → resolved value (populated after
                   :meth:`parse` returns).
        libraries: dict mapping library ID → assembly name string.
    """

    def __init__(self, data: bytes) -> None:
        self._f = io.BytesIO(data)
        self.objects: dict  = {}   # objectId → value
        self.libraries: dict = {}  # libraryId → assembly name
        self._class_defs: dict = {}  # objectId → (name, member_names, btypes, additional)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def parse(self) -> Any:
        """Parse the stream and return the root object."""
        first_byte = self._u8()
        if first_byte != _RecordType.SerializationHeader:
            raise ParseError(
                f"Stream does not start with a SerializationHeaderRecord "
                f"(got byte 0x{first_byte:02x})"
            )
        root_id   = self._i32()
        self._i32()  # headerId
        self._i32()  # majorVersion
        self._i32()  # minorVersion

        while True:
            pos      = self._f.tell()
            rec_byte = self._f.read(1)
            if not rec_byte:
                break
            rec = _RecordType(rec_byte[0])

            if rec == _RecordType.MessageEnd:
                break
            elif rec == _RecordType.BinaryLibrary:
                self._read_binary_library()
            elif rec == _RecordType.ClassWithMembersAndTypes:
                self._read_class_with_members_and_types()
            elif rec == _RecordType.SystemClassWithMembersAndTypes:
                self._read_system_class_with_members_and_types()
            elif rec == _RecordType.ClassWithId:
                self._read_class_with_id()
            elif rec == _RecordType.BinaryObjectString:
                self._read_binary_object_string_body()
            elif rec == _RecordType.ArraySinglePrimitive:
                self._read_array_single_primitive()
            elif rec == _RecordType.ArraySingleObject:
                self._read_array_single_object()
            elif rec == _RecordType.ArraySingleString:
                self._read_array_single_string()
            elif rec == _RecordType.BinaryArray:
                self._read_binary_array()
            else:
                raise ParseError(
                    f"Unexpected top-level record {rec!r} ({rec.value}) at offset {pos}"
                )

        return self._resolve(self.objects.get(root_id))

    # ------------------------------------------------------------------
    # Low-level readers
    # ------------------------------------------------------------------

    def _u8(self)  -> int:   return self._f.read(1)[0]
    def _i8(self)  -> int:   return struct.unpack('<b', self._f.read(1))[0]
    def _u16(self) -> int:   return struct.unpack('<H', self._f.read(2))[0]
    def _i16(self) -> int:   return struct.unpack('<h', self._f.read(2))[0]
    def _i32(self) -> int:   return struct.unpack('<i', self._f.read(4))[0]
    def _u32(self) -> int:   return struct.unpack('<I', self._f.read(4))[0]
    def _i64(self) -> int:   return struct.unpack('<q', self._f.read(8))[0]
    def _u64(self) -> int:   return struct.unpack('<Q', self._f.read(8))[0]
    def _f32(self) -> float: return struct.unpack('<f', self._f.read(4))[0]
    def _f64(self) -> float: return struct.unpack('<d', self._f.read(8))[0]

    def _lps(self) -> str:
        """Read a .NET length-prefixed string (7-bit encoded length + UTF-8)."""
        length = shift = 0
        while True:
            b = self._f.read(1)[0]
            length |= (b & 0x7F) << shift
            if not (b & 0x80):
                break
            shift += 7
        return self._f.read(length).decode('utf-8')

    def _primitive(self, ptype: int) -> Any:
        pt = _PrimitiveType(ptype)
        if pt == _PrimitiveType.Boolean:  return bool(self._u8())
        if pt == _PrimitiveType.Byte:     return self._u8()
        if pt == _PrimitiveType.Char:     return self._read_char()
        if pt == _PrimitiveType.Decimal:  return self._lps()
        if pt == _PrimitiveType.Double:   return self._f64()
        if pt == _PrimitiveType.Int16:    return self._i16()
        if pt == _PrimitiveType.Int32:    return self._i32()
        if pt == _PrimitiveType.Int64:    return self._i64()
        if pt == _PrimitiveType.SByte:    return self._i8()
        if pt == _PrimitiveType.Single:   return self._f32()
        if pt == _PrimitiveType.TimeSpan: return self._i64()
        if pt == _PrimitiveType.DateTime: return self._u64()
        if pt == _PrimitiveType.UInt16:   return self._u16()
        if pt == _PrimitiveType.UInt32:   return self._u32()
        if pt == _PrimitiveType.UInt64:   return self._u64()
        raise ParseError(f"Unhandled primitive type {ptype}")

    def _read_char(self) -> str:
        """Read a UTF-8-encoded Char (BinaryFormatter stores Char as UTF-8)."""
        b = self._u8()
        if b < 0x80:
            return chr(b)
        if b < 0xE0:
            b2 = self._u8()
            return chr(((b & 0x1F) << 6) | (b2 & 0x3F))
        if b < 0xF0:
            b2, b3 = self._u8(), self._u8()
            return chr(((b & 0x0F) << 12) | ((b2 & 0x3F) << 6) | (b3 & 0x3F))
        b2, b3, b4 = self._u8(), self._u8(), self._u8()
        return chr(((b & 0x07) << 18) | ((b2 & 0x3F) << 12) | ((b3 & 0x3F) << 6) | (b4 & 0x3F))

    # ------------------------------------------------------------------
    # Record readers
    # ------------------------------------------------------------------

    def _read_binary_library(self) -> None:
        lib_id = self._i32()
        self.libraries[lib_id] = self._lps()

    def _read_class_info(self) -> tuple:
        obj_id       = self._i32()
        name         = self._lps()
        count        = self._i32()
        member_names = [self._lps() for _ in range(count)]
        return obj_id, name, member_names

    def _read_member_type_info(self, count: int) -> tuple:
        binary_types = [_BinaryType(self._u8()) for _ in range(count)]
        additional   = []
        for bt in binary_types:
            if bt in (_BinaryType.Primitive, _BinaryType.PrimitiveArray):
                additional.append(self._u8())
            elif bt == _BinaryType.SystemClass:
                additional.append(self._lps())
            elif bt == _BinaryType.Class:
                class_name = self._lps()
                lib_id     = self._i32()
                additional.append((class_name, lib_id))
            else:
                additional.append(None)
        return binary_types, additional

    def _read_class_with_members_and_types(self) -> int:
        obj_id, name, member_names = self._read_class_info()
        binary_types, additional   = self._read_member_type_info(len(member_names))
        self._i32()  # libraryId
        self._class_defs[obj_id] = (name, member_names, binary_types, additional)
        values = self._read_member_values(binary_types, additional)
        obj = dict(zip(member_names, values))
        obj['__class__'] = name
        self.objects[obj_id] = obj
        return obj_id

    def _read_system_class_with_members_and_types(self) -> int:
        obj_id, name, member_names = self._read_class_info()
        binary_types, additional   = self._read_member_type_info(len(member_names))
        self._class_defs[obj_id] = (name, member_names, binary_types, additional)
        values = self._read_member_values(binary_types, additional)
        obj = dict(zip(member_names, values))
        obj['__class__'] = name
        self.objects[obj_id] = obj
        return obj_id

    def _read_class_with_id(self) -> int:
        obj_id      = self._i32()
        metadata_id = self._i32()
        name, member_names, binary_types, additional = self._class_defs[metadata_id]
        values = self._read_member_values(binary_types, additional)
        obj = dict(zip(member_names, values))
        obj['__class__'] = name
        self.objects[obj_id] = obj
        return obj_id

    def _read_member_values(self, binary_types: list, additional: list) -> list:
        return [self._read_value(bt, ai)
                for bt, ai in zip(binary_types, additional)]

    def _read_value(self, bt: _BinaryType, additional: Any) -> Any:
        if bt == _BinaryType.Primitive:
            return self._primitive(additional)
        # All other types are encoded as the next inline record.
        return self._read_inline_value()

    def _read_inline_value(self) -> Any:
        pos      = self._f.tell()
        rec_byte = self._f.read(1)
        if not rec_byte:
            return None
        rec = _RecordType(rec_byte[0])

        if rec == _RecordType.BinaryObjectString:
            return self._read_binary_object_string_body()
        if rec == _RecordType.MemberReference:
            return {'__ref__': self._i32()}
        if rec == _RecordType.ObjectNull:
            return None
        if rec in (_RecordType.ObjectNullMultiple256, _RecordType.ObjectNullMultiple):
            # Per MS-NRBF §2.5.4–5, these records are only valid inside arrays.
            # _read_array_elements handles them directly; reaching here means the
            # stream is malformed.
            raise ParseError(
                f"{rec.name} at offset {pos} is only valid inside an array, "
                f"not as a single member value"
            )
        if rec == _RecordType.ClassWithMembersAndTypes:
            return {'__ref__': self._read_class_with_members_and_types()}
        if rec == _RecordType.SystemClassWithMembersAndTypes:
            return {'__ref__': self._read_system_class_with_members_and_types()}
        if rec == _RecordType.ClassWithId:
            return {'__ref__': self._read_class_with_id()}
        if rec == _RecordType.ArraySinglePrimitive:
            return self._read_array_single_primitive()
        if rec == _RecordType.ArraySingleObject:
            return self._read_array_single_object()
        if rec == _RecordType.ArraySingleString:
            return self._read_array_single_string()
        if rec == _RecordType.BinaryArray:
            return self._read_binary_array()
        if rec == _RecordType.MemberPrimitiveTyped:
            return self._primitive(self._u8())
        if rec == _RecordType.BinaryLibrary:
            # A BinaryLibrary record may appear inline before the value it precedes.
            self._read_binary_library()
            return self._read_inline_value()
        raise ParseError(
            f"Unexpected record type {rec!r} ({rec.value}) at offset {pos}"
        )

    def _read_binary_object_string_body(self) -> str:
        obj_id = self._i32()
        s = self._lps()
        self.objects[obj_id] = s
        return s

    def _read_array_single_primitive(self) -> list:
        obj_id = self._i32()
        length = self._i32()
        ptype  = self._u8()
        arr    = [self._primitive(ptype) for _ in range(length)]
        self.objects[obj_id] = arr
        return arr

    def _read_array_single_string(self) -> list:
        obj_id = self._i32()
        length = self._i32()
        # Delegate to _read_array_elements so run-length null records
        # (ObjectNullMultiple256 / ObjectNullMultiple) are handled correctly.
        arr = self._read_array_elements(length)
        self.objects[obj_id] = arr
        return arr

    def _read_array_single_object(self) -> list:
        obj_id = self._i32()
        length = self._i32()
        arr    = self._read_array_elements(length)
        self.objects[obj_id] = arr
        return arr

    def _read_array_elements(self, length: int) -> list:
        arr = []
        i   = 0
        while i < length:
            pos      = self._f.tell()
            rec_byte = self._f.read(1)
            if not rec_byte:
                break
            rec = _RecordType(rec_byte[0])
            if rec == _RecordType.ObjectNull:
                arr.append(None); i += 1
            elif rec == _RecordType.ObjectNullMultiple256:
                count = self._u8()
                arr.extend([None] * count); i += count
            elif rec == _RecordType.ObjectNullMultiple:
                count = self._i32()
                arr.extend([None] * count); i += count
            else:
                self._f.seek(pos)
                arr.append(self._read_inline_value()); i += 1
        return arr

    def _read_binary_array(self) -> list:
        obj_id     = self._i32()
        array_type = _BinaryArrayType(self._u8())
        rank       = self._i32()
        lengths    = [self._i32() for _ in range(rank)]
        if array_type in (_BinaryArrayType.SingleOffset,
                          _BinaryArrayType.JaggedOffset,
                          _BinaryArrayType.RectangularOffset):
            for _ in range(rank):
                self._i32()  # lower bounds
        bt = _BinaryType(self._u8())
        additional = None
        if bt in (_BinaryType.Primitive, _BinaryType.PrimitiveArray):
            additional = self._u8()
        elif bt == _BinaryType.SystemClass:
            additional = self._lps()
        elif bt == _BinaryType.Class:
            additional = (self._lps(), self._i32())

        total = 1
        for dim in lengths:
            total *= dim

        if bt == _BinaryType.Primitive and additional is not None:
            arr = [self._primitive(additional) for _ in range(total)]
        else:
            arr = self._read_array_elements(total)

        self.objects[obj_id] = arr
        return arr

    # ------------------------------------------------------------------
    # Reference resolution and collection unwrapping
    # ------------------------------------------------------------------

    def _resolve(self, value: Any) -> Any:
        if isinstance(value, dict) and tuple(value) == ('__ref__',):
            ref_id = value['__ref__']
            if ref_id not in self.objects:
                raise ParseError(f"Unresolved object reference: ID {ref_id}")
            return self._resolve(self.objects[ref_id])
        if isinstance(value, dict):
            resolved = {k: self._resolve(v) for k, v in value.items()}
            return _unwrap_collection(resolved)
        if isinstance(value, list):
            return [self._resolve(v) for v in value]
        return value


def _unwrap_collection(obj: dict) -> Any:
    """Convert System.Collections types to native Python equivalents."""
    # .NET enums are serialized as classes with a single `value__` int member.
    if set(obj.keys()) == {'value__', '__class__'} and isinstance(obj.get('value__'), int):
        return obj['value__']
    cls = obj.get('__class__', '')
    if cls.startswith('System.Collections.Generic.List`1'):
        items = obj.get('_items') or []
        size  = obj.get('_size', len(items))
        return items[:size]
    if cls.startswith('System.Collections.Generic.Dictionary`2'):
        pairs = obj.get('KeyValuePairs') or []
        return {p['key']: p['value'] for p in pairs
                if isinstance(p, dict) and 'key' in p}
    if cls.startswith('System.Collections.Generic.KeyValuePair`2'):
        return {'key': obj.get('key'), 'value': obj.get('value')}
    # Drop internal comparer/bookkeeping objects with no meaningful fields.
    if cls.startswith('System.Collections.Generic.'):
        useful = {k: v for k, v in obj.items()
                  if k != '__class__' and not k.startswith('_')}
        if not useful:
            return None
    return obj


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def loads(data: bytes) -> Any:
    """Parse a .NET BinaryFormatter byte string and return the root object.

    Args:
        data: Raw bytes of a BinaryFormatter stream (not gzip-compressed).

    Returns:
        The deserialized root object as a plain Python value (dict, list,
        str, int, float, bool, or None).

    Raises:
        ParseError: If the stream is not a valid BinaryFormatter stream.
    """
    return NRBFParser(data).parse()


def load(fp: IO[bytes]) -> Any:
    """Parse a .NET BinaryFormatter stream from a file-like object.

    Args:
        fp: A binary-mode file-like object (must support ``.read()``).

    Returns:
        The deserialized root object as a plain Python value.

    Raises:
        ParseError: If the stream is not a valid BinaryFormatter stream.
    """
    return loads(fp.read())
