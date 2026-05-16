from types import SimpleNamespace

import pytest

import dawnpy_lwm2m.client as client_mod
from dawnpy_lwm2m.client import (
    Lwm2mClient,
    Lwm2mDescriptorInfo,
    decode_value,
    encode_value,
    format_value,
)


class _Descriptor:
    def __init__(self, proto, ios):
        self._proto = proto
        self._ios = ios

    def get_protocol(self, proto_type):
        return self._proto if proto_type == "wakaama" else None

    def get_io(self, io_id):
        return self._ios.get(io_id)


def _descriptor():
    proto = SimpleNamespace(
        config={
            "endpoint": "ntfc-wakaama",
            "server_host": "192.168.8.1",
            "objects": {
                "standard": [
                    {
                        "object": "temperature",
                        "instance": 0,
                        "resources": [
                            {
                                "resource": "sensor_value",
                                "io": {"id": "temp"},
                                "access": "read",
                            }
                        ],
                    }
                ],
                "custom": [
                    {
                        "object_id": 33000,
                        "instance": 0,
                        "resources": [
                            {
                                "resource_id": 1,
                                "io": "counter",
                                "access": "rw",
                            }
                        ],
                    }
                ],
            },
        }
    )
    ios = {
        "temp": SimpleNamespace(dtype="float", rw=False),
        "counter": SimpleNamespace(dtype="uint32", rw=True),
    }
    return _Descriptor(proto, ios)


def _list_descriptor():
    proto = SimpleNamespace(
        config={
            "endpoint": "ntfc-wakaama",
            "objects": [
                {
                    "object": "temperature",
                    "instance": 0,
                    "resources": [
                        {
                            "resource": "sensor_value",
                            "io": "temp",
                            "access": "read",
                        }
                    ],
                },
                {
                    "object_id": 33000,
                    "instance": 0,
                    "resources": [
                        {
                            "resource_id": 1,
                            "io": "counter",
                            "access": "rw",
                        }
                    ],
                },
            ],
        }
    )
    ios = {
        "temp": SimpleNamespace(dtype="float", rw=False),
        "counter": SimpleNamespace(dtype="uint32", rw=True),
    }
    return _Descriptor(proto, ios)


def test_descriptor_info_builds_paths_from_firmware_enums(monkeypatch):
    def _enum_values(owner, prefix):
        assert owner == "CProtoWakaama"
        if prefix == "WAKAAMA_OBJECT_":
            return {"temperature": 111}
        if prefix == "WAKAAMA_RESOURCE_":
            return {"sensor_value": 222}
        return {}

    monkeypatch.setattr(client_mod, "load_header_enum_value_ids", _enum_values)

    info = Lwm2mDescriptorInfo(_descriptor())

    assert info.endpoint == "ntfc-wakaama"
    assert info.get_binding("temp").path == "/111/0/222"
    assert info.get_binding("counter").path == "/33000/0/1"
    assert info.get_binding("/111/0/222").io_id == "temp"


def test_descriptor_info_accepts_current_wakaama_object_list(monkeypatch):
    monkeypatch.setattr(
        client_mod,
        "load_header_enum_value_ids",
        lambda owner, prefix: (
            {"temperature": 3303}
            if prefix == "WAKAAMA_OBJECT_"
            else {"sensor_value": 5700}
        ),
    )

    info = Lwm2mDescriptorInfo(_list_descriptor())

    assert info.endpoint == "ntfc-wakaama"
    assert info.get_binding("temp").path == "/3303/0/5700"
    assert info.get_binding("counter").path == "/33000/0/1"


def test_descriptor_info_rejects_missing_protocol():
    desc = _Descriptor(None, {})

    with pytest.raises(ValueError, match="descriptor has no wakaama"):
        Lwm2mDescriptorInfo(desc)


def test_client_resolves_io_ids(monkeypatch):
    monkeypatch.setattr(
        client_mod.Lwm2mDescriptorInfo,
        "from_path",
        lambda path: Lwm2mDescriptorInfo(_descriptor()),
    )
    monkeypatch.setattr(
        client_mod,
        "load_header_enum_value_ids",
        lambda owner, prefix: (
            {"temperature": 111}
            if prefix == "WAKAAMA_OBJECT_"
            else {"sensor_value": 222}
        ),
    )
    lwm2m = Lwm2mClient(descriptor_path="descriptor.yaml")

    assert lwm2m.endpoint == "ntfc-wakaama"
    assert lwm2m.resolve_path("temp") == "/111/0/222"
    assert lwm2m.resolve_path("/33000/0/1") == "/33000/0/1"


def test_typed_value_helpers_decode_and_encode_scalars():
    assert decode_value("bool", b"1") is True
    assert decode_value("bool", b"false") is False
    assert decode_value("uint32", b"1234") == 1234
    assert decode_value("float", b"12.5") == pytest.approx(12.5)
    assert decode_value("char", b"abc") == "abc"

    assert encode_value("bool", "on") == b"1"
    assert encode_value("bool", "0") == b"0"
    assert encode_value("uint32", "0x10") == b"16"
    assert encode_value("float", "1.25") == b"1.25"
    assert encode_value("char", "abc") == b"abc"

    with pytest.raises(ValueError, match="invalid bool value"):
        encode_value("bool", "maybe")


def test_typed_value_helpers_decode_and_encode_block(tmp_path):
    payload_file = tmp_path / "payload.bin"
    payload_file.write_bytes(b"file-payload")

    assert decode_value("block", b"ZGF3bg==") == b"dawn"
    assert encode_value("block", "hex:6461776e") == b"dawn"
    assert encode_value("block", "base64:ZGF3bg==") == b"dawn"
    assert encode_value("block", f"@{payload_file}") == b"file-payload"
    assert format_value(b"dawn") == "base64:ZGF3bg=="


def test_client_typed_read_write_uses_descriptor_binding(monkeypatch):
    monkeypatch.setattr(
        client_mod.Lwm2mDescriptorInfo,
        "from_path",
        lambda path: Lwm2mDescriptorInfo(_list_descriptor()),
    )
    monkeypatch.setattr(
        client_mod,
        "load_header_enum_value_ids",
        lambda owner, prefix: (
            {"temperature": 3303}
            if prefix == "WAKAAMA_OBJECT_"
            else {"sensor_value": 5700}
        ),
    )
    lwm2m = Lwm2mClient(descriptor_path="descriptor.yaml")
    writes = []

    class _Server:
        def read_path(self, path, timeout=None):
            assert path == "/33000/0/1"
            return b"123"

        def write_path(self, path, payload, timeout=None):
            writes.append((path, payload, timeout))

    lwm2m.server = _Server()

    assert lwm2m.read_typed("counter") == 123
    lwm2m.write_typed("counter", "0x2a", timeout=4.0)

    assert writes == [("/33000/0/1", b"42", 4.0)]
