"""LwM2M client helpers and descriptor mapping utilities."""

from __future__ import annotations

import base64
import binascii
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dawnpy.descriptor.client import (
    find_descriptor_path,
    load_client_descriptor,
)
from dawnpy.descriptor.support.utils import resolve_reference
from dawnpy.headerdefs import HeaderDefsError, load_header_enum_value_ids

from dawnpy_lwm2m.server import Lwm2mRegistration, Lwm2mTestServer

if TYPE_CHECKING:
    from dawnpy.descriptor.client import ClientDescriptor, ClientProto


@dataclass(frozen=True)
class Lwm2mResourceBinding:
    """Represents how one descriptor IO maps to an LwM2M resource path."""

    io_id: str
    dtype: str
    rw: bool
    object_id: int
    instance_id: int
    resource_id: int
    access: str

    @property
    def path(self) -> str:
        """Return the LwM2M resource path."""
        return f"/{self.object_id}/{self.instance_id}/{self.resource_id}"


TypedValue = bool | int | float | str | bytes


class Lwm2mDescriptorInfo:
    """Descriptor-backed LwM2M object/resource mapping."""

    def __init__(
        self,
        descriptor: "ClientDescriptor",
        *,
        proto_type: str = "wakaama",
    ) -> None:
        """Initialize mapping from descriptor Wakaama config."""
        self.descriptor = descriptor
        self.proto = self._find_proto(descriptor, proto_type)
        self.endpoint = str(self.proto.config.get("endpoint", ""))
        self.server_host = str(self.proto.config.get("server_host", ""))
        self.server_port = int(self.proto.config.get("server_port", 5683))
        self.local_port = int(self.proto.config.get("local_port", 56830))
        self.bindings = self._build_bindings()
        self.binding_map = {
            binding.io_id: binding for binding in self.bindings
        }
        self.path_map = {binding.path: binding for binding in self.bindings}

    @classmethod
    def from_path(cls, descriptor_path: str) -> "Lwm2mDescriptorInfo":
        """Load descriptor mapping from a descriptor.yaml path."""
        descriptor = load_client_descriptor(
            find_descriptor_path(descriptor_path)
        )
        return cls(descriptor)

    def get_binding(self, io_or_path: str) -> Lwm2mResourceBinding | None:
        """Return binding by IO ID or absolute LwM2M resource path."""
        if io_or_path.startswith("/"):
            return self.path_map.get(io_or_path)
        return self.binding_map.get(io_or_path)

    @staticmethod
    def _find_proto(
        descriptor: "ClientDescriptor",
        proto_type: str,
    ) -> "ClientProto":
        proto = descriptor.get_protocol(proto_type)
        if proto is None:
            raise ValueError(f"descriptor has no {proto_type} protocol")
        return proto

    def _build_bindings(self) -> list[Lwm2mResourceBinding]:
        objects = self.proto.config.get("objects", [])
        if isinstance(objects, list):
            return self._entry_bindings(objects, None)
        if not isinstance(objects, dict):
            raise ValueError("wakaama config.objects must be a list")

        bindings: list[Lwm2mResourceBinding] = []
        bindings.extend(self._group_bindings(objects, "standard", True))
        bindings.extend(self._group_bindings(objects, "custom", False))
        return bindings

    def _group_bindings(
        self,
        objects: dict[str, Any],
        group: str,
        standard: bool,
    ) -> list[Lwm2mResourceBinding]:
        entries = objects.get(group, []) or []
        if not isinstance(entries, list):
            raise ValueError(f"wakaama config.objects.{group} must be a list")

        return self._entry_bindings(entries, standard)

    def _entry_bindings(
        self,
        entries: list[Any],
        standard: bool | None,
    ) -> list[Lwm2mResourceBinding]:
        bindings: list[Lwm2mResourceBinding] = []
        object_values = _wakaama_object_values()
        resource_values = _wakaama_resource_values()

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            entry_standard = (
                _entry_uses_standard_names(entry)
                if standard is None
                else standard
            )
            object_id = _object_id(entry, entry_standard, object_values)
            instance_id = int(entry.get("instance", 0))
            resources = entry.get("resources", [])
            if not isinstance(resources, list):
                continue

            for resource in resources:
                if not isinstance(resource, dict):
                    continue
                io_id = resolve_reference(resource.get("io"))
                if io_id is None:
                    continue
                io = self.descriptor.get_io(io_id)
                if io is None:
                    raise ValueError(f"unknown IO in wakaama binding: {io_id}")
                bindings.append(
                    Lwm2mResourceBinding(
                        io_id=io_id,
                        dtype=io.dtype,
                        rw=io.rw,
                        object_id=object_id,
                        instance_id=instance_id,
                        resource_id=_resource_id(
                            resource,
                            entry_standard
                            and _entry_uses_standard_names(resource),
                            resource_values,
                        ),
                        access=str(resource.get("access", "read")).lower(),
                    )
                )

        return bindings


class Lwm2mClient:
    """Host-side LwM2M server/client facade for Dawn Wakaama targets."""

    def __init__(
        self,
        *,
        host: str = "0.0.0.0",
        port: int = 5683,
        endpoint: str | None = None,
        timeout: float = 2.0,
        descriptor_path: str | None = None,
    ) -> None:
        """Initialize local LwM2M server state and optional descriptor map."""
        self.host = host
        self.port = port
        self.endpoint = endpoint
        self.timeout = timeout
        self.descriptor_info = (
            Lwm2mDescriptorInfo.from_path(descriptor_path)
            if descriptor_path
            else None
        )
        if self.endpoint is None and self.descriptor_info is not None:
            self.endpoint = self.descriptor_info.endpoint
        self.server = Lwm2mTestServer(host=host, port=port, timeout=timeout)
        self.registration: Lwm2mRegistration | None = None

    def connect(self, *, timeout: float | None = None) -> Lwm2mRegistration:
        """Start the local server and wait for client registration."""
        self.server.start()
        self.registration = self.server.wait_for_registration(
            endpoint=self.endpoint or None,
            timeout=timeout or self.timeout,
        )
        return self.registration

    def close(self) -> None:
        """Close the local server socket."""
        self.server.close()

    def list_bindings(self) -> list[Lwm2mResourceBinding]:
        """Return descriptor-backed bindings."""
        if self.descriptor_info is None:
            return []
        return list(self.descriptor_info.bindings)

    def resolve_path(self, io_or_path: str) -> str:
        """Resolve an IO ID or absolute path into an LwM2M path."""
        if io_or_path.startswith("/"):
            return io_or_path
        if self.descriptor_info is None:
            raise ValueError("descriptor is required for IO-name addressing")
        binding = self.descriptor_info.get_binding(io_or_path)
        if binding is None:
            raise ValueError(f"unknown LwM2M IO binding: {io_or_path}")
        return binding.path

    def binding_for(self, io_or_path: str) -> Lwm2mResourceBinding | None:
        """Return descriptor binding for an IO ID or path, if available."""
        if self.descriptor_info is None:
            return None
        return self.descriptor_info.get_binding(io_or_path)

    def read(self, io_or_path: str, *, timeout: float | None = None) -> bytes:
        """Read an LwM2M resource by IO ID or path."""
        return self.server.read_path(
            self.resolve_path(io_or_path),
            timeout=timeout or self.timeout,
        )

    def read_typed(
        self, io_or_path: str, *, timeout: float | None = None
    ) -> TypedValue:
        """Read an LwM2M resource and decode it using descriptor dtype."""
        payload = self.read(io_or_path, timeout=timeout)
        binding = self.binding_for(io_or_path)
        if binding is None:
            return payload
        return decode_value(binding.dtype, payload)

    def write(
        self,
        io_or_path: str,
        value: bytes | str,
        *,
        timeout: float | None = None,
    ) -> None:
        """Write an LwM2M resource by IO ID or path."""
        payload = value if isinstance(value, bytes) else value.encode()
        self.server.write_path(
            self.resolve_path(io_or_path),
            payload,
            timeout=timeout or self.timeout,
        )

    def write_typed(
        self,
        io_or_path: str,
        value: TypedValue,
        *,
        timeout: float | None = None,
    ) -> None:
        """Write an LwM2M resource after descriptor-aware value encoding."""
        binding = self.binding_for(io_or_path)
        payload = (
            encode_value(binding.dtype, value)
            if binding is not None
            else value if isinstance(value, bytes) else str(value).encode()
        )
        self.server.write_path(
            self.resolve_path(io_or_path),
            payload,
            timeout=timeout or self.timeout,
        )

    def execute(
        self,
        io_or_path: str,
        value: bytes | str = b"",
        *,
        timeout: float | None = None,
    ) -> None:
        """Execute an LwM2M resource by IO ID or path."""
        payload = value if isinstance(value, bytes) else value.encode()
        self.server.execute_path(
            self.resolve_path(io_or_path),
            payload,
            timeout=timeout or self.timeout,
        )

    def monitor(
        self,
        targets: list[str],
        *,
        interval: float = 1.0,
        duration: float = 10.0,
    ) -> None:  # pragma: no cover
        """Poll resources and print values for a fixed duration."""
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            for target in targets:
                path = self.resolve_path(target)
                value = self.read_typed(path)
                print(f"{path}: {format_value(value)}")
            time.sleep(interval)


def _wakaama_object_values() -> dict[str, int]:
    try:
        return load_header_enum_value_ids("CProtoWakaama", "WAKAAMA_OBJECT_")
    except HeaderDefsError:
        return {}


def _wakaama_resource_values() -> dict[str, int]:
    try:
        return load_header_enum_value_ids("CProtoWakaama", "WAKAAMA_RESOURCE_")
    except HeaderDefsError:
        return {}


def _object_id(
    entry: dict[str, Any],
    standard: bool,
    values: dict[str, int],
) -> int:
    if standard:
        raw = str(entry.get("object", "")).lower()
        if raw in values:
            return values[raw]
        if raw:
            try:
                return int(raw)
            except ValueError:
                pass
        if "object_id" in entry:
            return int(entry["object_id"])
        raise ValueError(f"unknown Wakaama standard object: {raw}")
    return int(entry.get("object_id", entry.get("object", 0)))


def _resource_id(
    entry: dict[str, Any],
    standard: bool,
    values: dict[str, int],
) -> int:
    if standard:
        raw = str(entry.get("resource", "")).lower()
        if raw in values:
            return values[raw]
        if raw:
            try:
                return int(raw)
            except ValueError:
                pass
        if "resource_id" in entry:
            return int(entry["resource_id"])
        raise ValueError(f"unknown Wakaama standard resource: {raw}")
    return int(entry.get("resource_id", entry.get("resource", 0)))


def _entry_uses_standard_names(entry: dict[str, Any]) -> bool:
    """Return true when a Wakaama YAML entry uses firmware enum names."""
    return "object" in entry or "resource" in entry


def decode_value(dtype: str, payload: bytes) -> TypedValue:
    """Decode a text-format LwM2M payload using a Dawn dtype."""
    normalized = dtype.lower()
    text = payload.decode(errors="strict").strip()

    if normalized == "bool":
        lowered = text.lower()
        if lowered in ("1", "true"):
            return True
        if lowered in ("0", "false"):
            return False
        raise ValueError(f"cannot decode bool payload: {payload!r}")

    if normalized in _INT_DTYPES:
        return int(text, 0)

    if normalized in _FLOAT_DTYPES:
        return float(text)

    if normalized in ("char", "string"):
        return payload.decode(errors="replace")

    if normalized == "block":
        return base64.b64decode(payload, validate=True)

    return payload


def encode_value(dtype: str, value: TypedValue) -> bytes:
    """Encode a Python value into the text/opaque payload Wakaama expects."""
    normalized = dtype.lower()

    if normalized == "bool":
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in ("1", "true", "yes", "on"):
                return b"1"
            if lowered in ("0", "false", "no", "off"):
                return b"0"
            raise ValueError(f"invalid bool value: {value!r}")
        return b"1" if bool(value) else b"0"

    if normalized in _INT_DTYPES:
        parsed = int(value, 0) if isinstance(value, str) else int(value)
        return str(parsed).encode()

    if normalized in _FLOAT_DTYPES:
        return str(float(value)).encode()

    if normalized in ("char", "string"):
        return value if isinstance(value, bytes) else str(value).encode()

    if normalized == "block":
        return _parse_block_value(value)

    return value if isinstance(value, bytes) else str(value).encode()


def format_value(value: TypedValue) -> str:
    """Format a typed value for console display."""
    if isinstance(value, bytes):
        return f"base64:{base64.b64encode(value).decode()}"
    return str(value)


def _parse_block_value(value: TypedValue) -> bytes:
    if isinstance(value, bytes):
        return value
    if not isinstance(value, str):
        return str(value).encode()

    if value.startswith("hex:"):
        try:
            return bytes.fromhex(value[4:])
        except ValueError as exc:
            raise ValueError(f"invalid hex block payload: {value!r}") from exc

    if value.startswith("base64:"):
        try:
            return base64.b64decode(value[7:], validate=True)
        except binascii.Error as exc:
            raise ValueError(
                f"invalid base64 block payload: {value!r}"
            ) from exc

    if value.startswith("@"):
        return Path(value[1:]).read_bytes()

    return value.encode()


_INT_DTYPES = {
    "int8",
    "uint8",
    "int16",
    "uint16",
    "int32",
    "uint32",
    "int64",
    "uint64",
}
_FLOAT_DTYPES = {"float", "double", "b16", "ub16"}
