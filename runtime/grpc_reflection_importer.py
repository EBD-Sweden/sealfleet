"""Opt-in gRPC reflection to Sealfleet manifest importer.

This is intentionally a narrow spike: it converts protobuf service descriptors
into Sealfleet typed tool manifests for unary RPCs only. Live network reflection
is guarded behind explicit enablement and authentication, and grpcio remains an
optional dependency so local descriptor-set fixtures can run in CI without a
production gRPC server.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from google.protobuf import descriptor_pb2

_GENERATED_BY = "mcpfinder.grpc_reflection_importer"
_SECRET_METADATA_FRAGMENTS = (
    "authorization",
    "auth",
    "api-key",
    "apikey",
    "token",
    "secret",
    "password",
    "passwd",
    "cookie",
)

_PROTO_TO_JSON_SCHEMA: dict[int, dict[str, Any]] = {
    descriptor_pb2.FieldDescriptorProto.TYPE_DOUBLE: {"type": "number"},
    descriptor_pb2.FieldDescriptorProto.TYPE_FLOAT: {"type": "number"},
    descriptor_pb2.FieldDescriptorProto.TYPE_INT64: {"type": "integer"},
    descriptor_pb2.FieldDescriptorProto.TYPE_UINT64: {"type": "integer"},
    descriptor_pb2.FieldDescriptorProto.TYPE_INT32: {"type": "integer"},
    descriptor_pb2.FieldDescriptorProto.TYPE_FIXED64: {"type": "integer"},
    descriptor_pb2.FieldDescriptorProto.TYPE_FIXED32: {"type": "integer"},
    descriptor_pb2.FieldDescriptorProto.TYPE_BOOL: {"type": "boolean"},
    descriptor_pb2.FieldDescriptorProto.TYPE_STRING: {"type": "string"},
    descriptor_pb2.FieldDescriptorProto.TYPE_BYTES: {
        "type": "string",
        "contentEncoding": "base64",
    },
    descriptor_pb2.FieldDescriptorProto.TYPE_UINT32: {"type": "integer"},
    descriptor_pb2.FieldDescriptorProto.TYPE_SFIXED32: {"type": "integer"},
    descriptor_pb2.FieldDescriptorProto.TYPE_SFIXED64: {"type": "integer"},
    descriptor_pb2.FieldDescriptorProto.TYPE_SINT32: {"type": "integer"},
    descriptor_pb2.FieldDescriptorProto.TYPE_SINT64: {"type": "integer"},
}

_PROTO_TO_MCPFINDER_TYPE: dict[int, str] = {
    descriptor_pb2.FieldDescriptorProto.TYPE_DOUBLE: "Float",
    descriptor_pb2.FieldDescriptorProto.TYPE_FLOAT: "Float",
    descriptor_pb2.FieldDescriptorProto.TYPE_INT64: "Integer",
    descriptor_pb2.FieldDescriptorProto.TYPE_UINT64: "Integer",
    descriptor_pb2.FieldDescriptorProto.TYPE_INT32: "Integer",
    descriptor_pb2.FieldDescriptorProto.TYPE_FIXED64: "Integer",
    descriptor_pb2.FieldDescriptorProto.TYPE_FIXED32: "Integer",
    descriptor_pb2.FieldDescriptorProto.TYPE_BOOL: "Boolean",
    descriptor_pb2.FieldDescriptorProto.TYPE_STRING: "String",
    descriptor_pb2.FieldDescriptorProto.TYPE_BYTES: "Bytes",
    descriptor_pb2.FieldDescriptorProto.TYPE_UINT32: "Integer",
    descriptor_pb2.FieldDescriptorProto.TYPE_ENUM: "String",
    descriptor_pb2.FieldDescriptorProto.TYPE_SFIXED32: "Integer",
    descriptor_pb2.FieldDescriptorProto.TYPE_SFIXED64: "Integer",
    descriptor_pb2.FieldDescriptorProto.TYPE_SINT32: "Integer",
    descriptor_pb2.FieldDescriptorProto.TYPE_SINT64: "Integer",
}


@dataclass(frozen=True)
class ImporterOptions:
    """Authorization and feature flags for reflection import.

    The defaults are deliberately safe: imports are disabled and there is no
    requester identity. Callers must opt in and pass an authenticated identity
    before any descriptor or network reflection can be converted.
    """

    enabled: bool = False
    requester_identity: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)
    allow_insecure_reflection: bool = False


def redact_metadata(metadata: Mapping[str, str] | None) -> dict[str, str]:
    """Return metadata with secret-bearing values removed."""

    redacted: dict[str, str] = {}
    for key, value in (metadata or {}).items():
        normalized = key.lower().replace("_", "-")
        if any(fragment in normalized for fragment in _SECRET_METADATA_FRAGMENTS):
            redacted[key] = "<redacted>"
        else:
            redacted[key] = value
    return redacted


class GrpcReflectionImporter:
    """Convert gRPC reflection descriptors into typed Sealfleet manifests."""

    def __init__(self, options: ImporterOptions | None = None):
        self.options = options or ImporterOptions()

    def import_descriptor_set(
        self,
        descriptor_set: descriptor_pb2.FileDescriptorSet,
        *,
        endpoint: str,
        manifest_name: str,
    ) -> dict[str, Any]:
        """Build a manifest from a local FileDescriptorSet fixture.

        Only unary RPCs are exposed as tools. Client/server/bidi streaming
        methods are recorded under ``x-grpc-reflection.unsupported_streaming``
        rather than being advertised as runnable capabilities.
        """

        self._assert_authorized()
        messages = _message_index(descriptor_set.file)
        tools: list[dict[str, Any]] = []
        unsupported_streaming: list[str] = []

        for file_proto in descriptor_set.file:
            package = file_proto.package
            for service in file_proto.service:
                service_name = _qualified_name(package, service.name)
                for method in service.method:
                    full_method = f"/{service_name}/{method.name}"
                    if method.client_streaming or method.server_streaming:
                        unsupported_streaming.append(full_method)
                        continue

                    request = messages.get(method.input_type.lstrip("."))
                    response = messages.get(method.output_type.lstrip("."))
                    if request is None or response is None:
                        raise ValueError(
                            f"Descriptor set missing input/output message for {full_method}"
                        )
                    tools.append(
                        _tool_from_unary_method(
                            service_name=service_name,
                            method=method,
                            request=request,
                            response=response,
                        )
                    )

        return {
            "name": manifest_name,
            "endpoint": endpoint,
            "transport": "grpc-reflection",
            "publishes": [],
            "subscribes": [],
            "tools": tools,
            "x-grpc-reflection": {
                "generated_by": _GENERATED_BY,
                "requester_identity": self.options.requester_identity,
                "metadata": redact_metadata(self.options.metadata),
                "unsupported_streaming": unsupported_streaming,
                "limits": {
                    "streaming": "not exposed as MCP tools in this spike",
                    "tls_mtls": "caller/runtime supplies channel credentials; importer stores no private keys",
                    "metadata_auth": "metadata values are redacted from generated manifests",
                },
            },
        }

    def discover_from_reflection(
        self,
        *,
        target: str,
        manifest_name: str,
        service_names: Iterable[str] | None = None,
        timeout_seconds: float = 5.0,
        secure_channel: Any | None = None,
    ) -> dict[str, Any]:
        """Fetch descriptors via gRPC server reflection and build a manifest.

        Live reflection is fail-closed for transport security: metadata-bearing
        auth may only be sent over a caller-provided secure channel. The importer
        will construct an insecure channel only for explicit local/dev use with
        no metadata.
        """

        self._assert_authorized()
        self._assert_live_reflection_transport_allowed(secure_channel=secure_channel)
        try:
            import grpc  # type: ignore[import-not-found]
            from grpc_reflection.v1alpha import reflection_pb2, reflection_pb2_grpc  # type: ignore[import-not-found]
        except Exception as exc:  # pragma: no cover - depends on optional packages
            raise RuntimeError(
                "Live gRPC reflection requires optional dependencies: "
                "grpcio and grpcio-reflection"
            ) from exc

        metadata = tuple((key, value) for key, value in self.options.metadata.items())
        if secure_channel is not None:
            channel = secure_channel
            should_close = False
        else:
            channel = grpc.insecure_channel(target)  # pragma: no cover - optional path
            should_close = True

        try:
            stub = reflection_pb2_grpc.ServerReflectionStub(channel)
            names = list(service_names or _list_reflection_services(stub, metadata, timeout_seconds, reflection_pb2))
            files: dict[str, descriptor_pb2.FileDescriptorProto] = {}
            for service_name in names:
                request = reflection_pb2.ServerReflectionRequest(file_containing_symbol=service_name)
                responses = stub.ServerReflectionInfo(
                    iter([request]), metadata=metadata, timeout=timeout_seconds
                )
                for response in responses:
                    descriptor_response = response.file_descriptor_response
                    for raw in descriptor_response.file_descriptor_proto:
                        file_proto = descriptor_pb2.FileDescriptorProto.FromString(raw)
                        files[file_proto.name] = file_proto
            descriptor_set = descriptor_pb2.FileDescriptorSet(file=list(files.values()))
        finally:
            if should_close:
                close = getattr(channel, "close", None)
                if close is not None:  # pragma: no cover - optional path
                    close()
        return self.import_descriptor_set(
            descriptor_set,
            endpoint=target,
            manifest_name=manifest_name,
        )

    def _assert_authorized(self) -> None:
        if not self.options.enabled:
            raise PermissionError("gRPC reflection importer is disabled by default")
        if not self.options.requester_identity:
            raise PermissionError("gRPC reflection importer requires an authenticated requester")

    def _assert_live_reflection_transport_allowed(self, *, secure_channel: Any | None) -> None:
        if secure_channel is not None:
            return
        if self.options.metadata:
            raise PermissionError(
                "gRPC reflection metadata requires a caller-provided secure channel; "
                "the importer will not send auth metadata over grpc.insecure_channel"
            )
        if not self.options.allow_insecure_reflection:
            raise PermissionError(
                "insecure gRPC reflection is disabled; set allow_insecure_reflection=True "
                "only for local/dev discovery without metadata"
            )


def _list_reflection_services(stub: Any, metadata: tuple[tuple[str, str], ...], timeout: float, reflection_pb2: Any) -> list[str]:
    request = reflection_pb2.ServerReflectionRequest(list_services="")
    responses = stub.ServerReflectionInfo(iter([request]), metadata=metadata, timeout=timeout)
    service_names: list[str] = []
    for response in responses:
        for service in response.list_services_response.service:
            if service.name != "grpc.reflection.v1alpha.ServerReflection":
                service_names.append(service.name)
    return service_names


def _message_index(files: Iterable[descriptor_pb2.FileDescriptorProto]) -> dict[str, descriptor_pb2.DescriptorProto]:
    messages: dict[str, descriptor_pb2.DescriptorProto] = {}
    for file_proto in files:
        package = file_proto.package
        for message in file_proto.message_type:
            _index_message(messages, package, message)
    return messages


def _index_message(
    messages: dict[str, descriptor_pb2.DescriptorProto],
    prefix: str,
    message: descriptor_pb2.DescriptorProto,
) -> None:
    qualified = _qualified_name(prefix, message.name)
    messages[qualified] = message
    for nested in message.nested_type:
        _index_message(messages, qualified, nested)


def _qualified_name(package: str, name: str) -> str:
    return f"{package}.{name}" if package else name


def _tool_from_unary_method(
    *,
    service_name: str,
    method: descriptor_pb2.MethodDescriptorProto,
    request: descriptor_pb2.DescriptorProto,
    response: descriptor_pb2.DescriptorProto,
) -> dict[str, Any]:
    return {
        "name": _safe_tool_name(service_name, method.name),
        "description": f"Unary gRPC method /{service_name}/{method.name}",
        "inputs": _mcpfinder_inputs(request),
        "outputs": {"response": {"type": response.name}},
        "input_schema": _json_schema(request),
        "output_schema": _json_schema(response),
        "grpc": {
            "service": service_name,
            "method": method.name,
            "full_method": f"/{service_name}/{method.name}",
            "client_streaming": False,
            "server_streaming": False,
        },
    }


def _safe_tool_name(service_name: str, method_name: str) -> str:
    return f"{service_name}_{method_name}".replace(".", "_").replace("/", "_")


def _mcpfinder_inputs(message: descriptor_pb2.DescriptorProto) -> dict[str, dict[str, Any]]:
    inputs: dict[str, dict[str, Any]] = {}
    for field_proto in message.field:
        inputs[field_proto.name] = {
            "type": _mcpfinder_type(field_proto),
            "required": _is_required(field_proto),
            "description": f"{message.name}.{field_proto.name}",
        }
    return inputs


def _json_schema(message: descriptor_pb2.DescriptorProto) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    required: list[str] = []
    for field_proto in message.field:
        field_schema = _json_schema_for_field(field_proto)
        if field_proto.label == descriptor_pb2.FieldDescriptorProto.LABEL_REPEATED:
            field_schema = {"type": "array", "items": field_schema}
        schema["properties"][field_proto.name] = field_schema
        if _is_required(field_proto):
            required.append(field_proto.name)
    if required:
        schema["required"] = required
    return schema


def _json_schema_for_field(field_proto: descriptor_pb2.FieldDescriptorProto) -> dict[str, Any]:
    if field_proto.type == descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE:
        return {"type": "object", "x-protobuf-type": field_proto.type_name.lstrip(".")}
    if field_proto.type == descriptor_pb2.FieldDescriptorProto.TYPE_ENUM:
        return {"type": "string", "x-protobuf-enum": field_proto.type_name.lstrip(".")}
    return dict(_PROTO_TO_JSON_SCHEMA.get(field_proto.type, {"type": "string"}))


def _mcpfinder_type(field_proto: descriptor_pb2.FieldDescriptorProto) -> str:
    if field_proto.label == descriptor_pb2.FieldDescriptorProto.LABEL_REPEATED:
        return "Array"
    if field_proto.type == descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE:
        return field_proto.type_name.rsplit(".", 1)[-1]
    return _PROTO_TO_MCPFINDER_TYPE.get(field_proto.type, "String")


def _is_required(field_proto: descriptor_pb2.FieldDescriptorProto) -> bool:
    return field_proto.label == descriptor_pb2.FieldDescriptorProto.LABEL_REQUIRED
