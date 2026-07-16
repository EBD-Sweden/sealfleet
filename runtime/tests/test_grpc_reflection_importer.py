"""Tests for opt-in gRPC reflection-to-MCP manifest import spike.

The fixture builds protobuf descriptors locally so tests do not need a live gRPC
server or production deployment.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

# The gRPC reflection importer is an opt-in spike; protobuf/grpcio are optional
# deps. Skip the whole module gracefully when they are not installed so a fresh
# clone can run `pytest runtime/tests` without the gRPC stack.
descriptor_pb2 = pytest.importorskip("google.protobuf.descriptor_pb2")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from grpc_reflection_importer import (  # noqa: E402
    GrpcReflectionImporter,
    ImporterOptions,
    redact_metadata,
)


def fake_echo_descriptor_set() -> descriptor_pb2.FileDescriptorSet:
    file_proto = descriptor_pb2.FileDescriptorProto(
        name="acme/echo/v1/echo.proto",
        package="acme.echo.v1",
        syntax="proto3",
    )

    request = file_proto.message_type.add(name="EchoRequest")
    field = request.field.add(name="message", number=1, label=1, type=9)
    field.json_name = "message"
    field = request.field.add(name="request_id", number=2, label=1, type=9)
    field.json_name = "requestId"

    response = file_proto.message_type.add(name="EchoResponse")
    field = response.field.add(name="reply", number=1, label=1, type=9)
    field.json_name = "reply"

    svc = file_proto.service.add(name="EchoService")
    unary = svc.method.add(name="Echo")
    unary.input_type = ".acme.echo.v1.EchoRequest"
    unary.output_type = ".acme.echo.v1.EchoResponse"

    server_stream = svc.method.add(name="WatchEcho")
    server_stream.input_type = ".acme.echo.v1.EchoRequest"
    server_stream.output_type = ".acme.echo.v1.EchoResponse"
    server_stream.server_streaming = True

    return descriptor_pb2.FileDescriptorSet(file=[file_proto])


def test_importer_is_disabled_by_default() -> None:
    importer = GrpcReflectionImporter()

    with pytest.raises(PermissionError, match="disabled"):
        importer.import_descriptor_set(
            fake_echo_descriptor_set(),
            endpoint="localhost:50051",
            manifest_name="echo-grpc",
        )


def test_importer_rejects_anonymous_even_when_enabled() -> None:
    importer = GrpcReflectionImporter(ImporterOptions(enabled=True))

    with pytest.raises(PermissionError, match="authenticated"):
        importer.import_descriptor_set(
            fake_echo_descriptor_set(),
            endpoint="localhost:50051",
            manifest_name="echo-grpc",
        )


def test_local_descriptor_fixture_generates_unary_mcp_tool_manifest() -> None:
    importer = GrpcReflectionImporter(
        ImporterOptions(enabled=True, requester_identity="tenant-admin@example.com")
    )

    manifest = importer.import_descriptor_set(
        fake_echo_descriptor_set(),
        endpoint="localhost:50051",
        manifest_name="echo-grpc",
    )

    assert manifest["name"] == "echo-grpc"
    assert manifest["transport"] == "grpc-reflection"
    assert manifest["endpoint"] == "localhost:50051"
    assert manifest["x-grpc-reflection"]["generated_by"] == "mcpfinder.grpc_reflection_importer"
    assert manifest["x-grpc-reflection"]["unsupported_streaming"] == [
        "/acme.echo.v1.EchoService/WatchEcho"
    ]

    assert len(manifest["tools"]) == 1
    tool = manifest["tools"][0]
    assert tool["name"] == "acme_echo_v1_EchoService_Echo"
    assert tool["description"] == "Unary gRPC method /acme.echo.v1.EchoService/Echo"
    assert tool["inputs"] == {
        "message": {"type": "String", "required": False, "description": "EchoRequest.message"},
        "request_id": {"type": "String", "required": False, "description": "EchoRequest.request_id"},
    }
    assert tool["input_schema"] == {
        "type": "object",
        "properties": {
            "message": {"type": "string"},
            "request_id": {"type": "string"},
        },
        "additionalProperties": False,
    }
    assert tool["outputs"] == {"response": {"type": "EchoResponse"}}
    assert tool["grpc"] == {
        "service": "acme.echo.v1.EchoService",
        "method": "Echo",
        "full_method": "/acme.echo.v1.EchoService/Echo",
        "client_streaming": False,
        "server_streaming": False,
    }


def test_metadata_redaction_keeps_auth_secret_values_out_of_manifest() -> None:
    importer = GrpcReflectionImporter(
        ImporterOptions(
            enabled=True,
            requester_identity="tenant-admin@example.com",
            metadata={
                "authorization": "Bearer super-secret",
                "x-api-key": "abc123-secret",
                "x-trace-id": "trace-123",
            },
        )
    )

    manifest = importer.import_descriptor_set(
        fake_echo_descriptor_set(),
        endpoint="localhost:50051",
        manifest_name="echo-grpc",
    )

    assert "super-secret" not in repr(manifest)
    assert "abc123-secret" not in repr(manifest)
    assert manifest["x-grpc-reflection"]["metadata"] == {
        "authorization": "<redacted>",
        "x-api-key": "<redacted>",
        "x-trace-id": "trace-123",
    }
    assert redact_metadata({"cookie": "session=secret", "safe": "ok"}) == {
        "cookie": "<redacted>",
        "safe": "ok",
    }


def test_live_reflection_rejects_metadata_without_secure_channel_before_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_insecure_channel_is_constructed(target: str):
        raise AssertionError(f"insecure_channel must not be called for {target}")

    fake_grpc = types.SimpleNamespace(insecure_channel=fail_if_insecure_channel_is_constructed)
    fake_reflection_pb2 = types.SimpleNamespace()
    fake_reflection_pb2_grpc = types.SimpleNamespace(ServerReflectionStub=lambda channel: object())

    monkeypatch.setitem(sys.modules, "grpc", fake_grpc)
    monkeypatch.setitem(sys.modules, "grpc_reflection", types.ModuleType("grpc_reflection"))
    monkeypatch.setitem(sys.modules, "grpc_reflection.v1alpha", types.ModuleType("grpc_reflection.v1alpha"))
    monkeypatch.setitem(sys.modules, "grpc_reflection.v1alpha.reflection_pb2", fake_reflection_pb2)
    monkeypatch.setitem(sys.modules, "grpc_reflection.v1alpha.reflection_pb2_grpc", fake_reflection_pb2_grpc)

    importer = GrpcReflectionImporter(
        ImporterOptions(
            enabled=True,
            requester_identity="tenant-admin@example.com",
            metadata={"authorization": "Bearer super-secret"},
        )
    )

    with pytest.raises(PermissionError, match="secure channel"):
        importer.discover_from_reflection(target="localhost:50051", manifest_name="echo-grpc")


def test_live_reflection_with_metadata_uses_injected_secure_channel_and_redacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_insecure_channel_is_constructed(target: str):
        raise AssertionError(f"insecure_channel must not be called for {target}")

    class FakeReflectionRequest:
        def __init__(self, file_containing_symbol: str = "", list_services: str = "") -> None:
            self.file_containing_symbol = file_containing_symbol
            self.list_services = list_services

    class FakeStub:
        def __init__(self, channel: object) -> None:
            self.channel = channel
            self.calls: list[tuple[object, tuple[tuple[str, str], ...], float]] = []

        def ServerReflectionInfo(self, requests, *, metadata, timeout):
            self.calls.append((next(requests), metadata, timeout))
            return []

    fake_grpc = types.SimpleNamespace(insecure_channel=fail_if_insecure_channel_is_constructed)
    fake_reflection_pb2 = types.SimpleNamespace(ServerReflectionRequest=FakeReflectionRequest)
    fake_reflection_pb2_grpc = types.SimpleNamespace(ServerReflectionStub=FakeStub)

    monkeypatch.setitem(sys.modules, "grpc", fake_grpc)
    monkeypatch.setitem(sys.modules, "grpc_reflection", types.ModuleType("grpc_reflection"))
    monkeypatch.setitem(sys.modules, "grpc_reflection.v1alpha", types.ModuleType("grpc_reflection.v1alpha"))
    monkeypatch.setitem(sys.modules, "grpc_reflection.v1alpha.reflection_pb2", fake_reflection_pb2)
    monkeypatch.setitem(sys.modules, "grpc_reflection.v1alpha.reflection_pb2_grpc", fake_reflection_pb2_grpc)

    importer = GrpcReflectionImporter(
        ImporterOptions(
            enabled=True,
            requester_identity="tenant-admin@example.com",
            metadata={"authorization": "Bearer super-secret"},
        )
    )

    manifest = importer.discover_from_reflection(
        target="prod-grpc.example.com:443",
        manifest_name="echo-grpc",
        service_names=["acme.echo.v1.EchoService"],
        secure_channel=object(),
    )

    assert "super-secret" not in repr(manifest)
    assert manifest["x-grpc-reflection"]["metadata"] == {"authorization": "<redacted>"}
    assert manifest["tools"] == []


def test_insecure_live_reflection_requires_explicit_metadata_free_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructed_targets: list[str] = []

    class FakeChannel:
        def __init__(self, target: str) -> None:
            self.target = target
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class FakeReflectionRequest:
        def __init__(self, file_containing_symbol: str = "", list_services: str = "") -> None:
            self.file_containing_symbol = file_containing_symbol
            self.list_services = list_services

    class FakeStub:
        def __init__(self, channel: FakeChannel) -> None:
            self.channel = channel

        def ServerReflectionInfo(self, requests, *, metadata, timeout):
            assert metadata == ()
            return []

    def fake_insecure_channel(target: str) -> FakeChannel:
        constructed_targets.append(target)
        return FakeChannel(target)

    fake_grpc = types.SimpleNamespace(insecure_channel=fake_insecure_channel)
    fake_reflection_pb2 = types.SimpleNamespace(ServerReflectionRequest=FakeReflectionRequest)
    fake_reflection_pb2_grpc = types.SimpleNamespace(ServerReflectionStub=FakeStub)

    monkeypatch.setitem(sys.modules, "grpc", fake_grpc)
    monkeypatch.setitem(sys.modules, "grpc_reflection", types.ModuleType("grpc_reflection"))
    monkeypatch.setitem(sys.modules, "grpc_reflection.v1alpha", types.ModuleType("grpc_reflection.v1alpha"))
    monkeypatch.setitem(sys.modules, "grpc_reflection.v1alpha.reflection_pb2", fake_reflection_pb2)
    monkeypatch.setitem(sys.modules, "grpc_reflection.v1alpha.reflection_pb2_grpc", fake_reflection_pb2_grpc)

    base_options = {"enabled": True, "requester_identity": "tenant-admin@example.com"}
    default_importer = GrpcReflectionImporter(ImporterOptions(**base_options))
    with pytest.raises(PermissionError, match="insecure gRPC reflection is disabled"):
        default_importer.discover_from_reflection(target="localhost:50051", manifest_name="echo-grpc")

    explicit_dev_importer = GrpcReflectionImporter(
        ImporterOptions(**base_options, allow_insecure_reflection=True)
    )
    manifest = explicit_dev_importer.discover_from_reflection(
        target="localhost:50051",
        manifest_name="echo-grpc",
        service_names=["acme.echo.v1.EchoService"],
    )

    assert constructed_targets == ["localhost:50051"]
    assert manifest["tools"] == []
