"""Common serialization for plans, manifests and trace records."""

import dataclasses
import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from enum import Enum
from pathlib import Path


def json_value(value):
    if isinstance(value, Enum):
        return json_value(value.value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: json_value(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("JSON keys must be strings")
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_value(item) for item in value]
    return value


def canonical(value) -> bytes:
    return json.dumps(json_value(value), sort_keys=True, ensure_ascii=False,
                      allow_nan=False, separators=(",", ":")).encode("utf-8")


def digest(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def atomic_write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, suffix=".partial")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def write_json(path: Path, value):
    atomic_write(path, canonical(value) + b"\n")


class CellIOError(ValueError):
    def __init__(self, code, message, *, path=None, exit_code=2):
        self.code = code
        self.path = path
        self.exit_code = exit_code
        super().__init__(message)
