"""Black-box contracts against the local OCB Collector, never a Python scrubber.

Requires a container runtime: docker, otherwise podman, otherwise buildah.
An unavailable runtime is a failure, not a successful skip. The image is
built locally from this directory's Dockerfile (`oteru-collector:0.155.0`);
never the upstream contrib digest. All published ports are random,
loopback-only. Only synthetic data is sent.
"""

from __future__ import annotations

import copy
import io
import json
import os
import secrets
import shutil
import subprocess
import tarfile
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import grpc
import pytest
import requests
import yaml
from google.protobuf.json_format import MessageToDict, ParseDict
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest
from opentelemetry.proto.collector.logs.v1.logs_service_pb2_grpc import LogsServiceStub
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2_grpc import MetricsServiceStub
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2_grpc import TraceServiceStub
from support import ROOT, default, hmac_v1 as _hmac_v1, policy

# Bound by the session fixture. Never written to disk or printed.
_HMAC_RUNTIME_KEY = ""


def hmac_v1(field: str, value: str, key: str | None = None) -> str:
    return _hmac_v1(field, value, key if key is not None else _HMAC_RUNTIME_KEY)

# Local OCB distro. Built from oteru-collector/Dockerfile. Override with
# OTERU_COLLECTOR_IMAGE only for a already-built local tag — never contrib.
IMAGE = os.environ.get("OTERU_COLLECTOR_IMAGE", "oteru-collector:0.155.0")
_RUN_VERBS = frozenset(
    {"run", "network", "compose", "exec", "cp", "logs", "inspect", "rm"}
)
SIGNALS = {
    "logs": ("resourceLogs", "scopeLogs", "logRecords", ExportLogsServiceRequest, LogsServiceStub),
    "traces": ("resourceSpans", "scopeSpans", "spans", ExportTraceServiceRequest, TraceServiceStub),
    "metrics": (
        "resourceMetrics",
        "scopeMetrics",
        "metrics",
        ExportMetricsServiceRequest,
        MetricsServiceStub,
    ),
}
# Deliberately independent of the compiler's catalog and regular expressions.
IDENTITY = (
    "user.email",
    "user.id",
    "user.account_uuid",
    "user.account_id",
    "organization.id",
    "host.name",
)
CONTENT = (
    "user_prompt",
    "gen_ai.prompt",
    "gen_ai.completion",
    "gen_ai.input.messages",
    "gen_ai.output.messages",
)
CANARY = "privacy-canary@example.com"
OUTPUT_CONTAINERS = {}


@pytest.fixture(scope="session", autouse=True)
def hmac_test_key():
    """32+ byte HMAC key for this pytest process. Not a committed secret."""
    global _HMAC_RUNTIME_KEY
    key = secrets.token_hex(32)
    previous = os.environ.get("OTERU_PII_HMAC_KEY")
    _HMAC_RUNTIME_KEY = key
    os.environ["OTERU_PII_HMAC_KEY"] = key
    try:
        yield key
    finally:
        _HMAC_RUNTIME_KEY = ""
        if previous is None:
            os.environ.pop("OTERU_PII_HMAC_KEY", None)
        else:
            os.environ["OTERU_PII_HMAC_KEY"] = previous


@pytest.fixture(autouse=True)
def docker_mount_access(tmp_path):
    """Allow the container UID to traverse pytest's private Linux temp parents."""
    temp_root = Path(tempfile.gettempdir()).resolve()
    for directory in (tmp_path, *tmp_path.parents):
        if directory == temp_root:
            break
        directory.chmod(directory.stat().st_mode | 0o111)


def _engine_ready(name: str) -> bool:
    if not shutil.which(name):
        return False
    probe = subprocess.run(
        [name, "info"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return probe.returncode == 0


def resolve_engine() -> str:
    for name in ("docker", "podman"):
        if _engine_ready(name):
            return name
    if shutil.which("buildah"):
        return "buildah"
    raise AssertionError(
        "no container runtime: need docker, podman, or buildah to run collector tests"
    )


def engine() -> str:
    return resolve_engine()


def _engine_argv(exe: str, args: tuple[str, ...]) -> list[str]:
    verb = args[0] if args else ""
    if exe != "buildah":
        return [exe, *args]
    if verb == "build":
        return [exe, "bud", *args[1:]]
    if verb == "image" and len(args) >= 2 and args[1] == "inspect":
        return [exe, "inspect", *args[2:]]
    return [exe, *args]


def docker(*args, check=True, timeout=120, text=True):
    """Container-engine CLI. Name kept so existing call sites stay stable.

    Prefers docker, then podman. buildah can build the local image; run /
    network / compose / exec / cp / logs require docker or podman.
    """
    exe = engine()
    verb = args[0] if args else ""
    if exe == "buildah" and verb in _RUN_VERBS:
        raise AssertionError(
            "buildah can build oteru-collector but docker or podman is required to run it"
        )
    result = subprocess.run(
        _engine_argv(exe, args),
        check=False,
        text=text,
        capture_output=True,
        timeout=timeout,
    )
    if check and result.returncode:
        err = result.stderr if text else (result.stderr or b"").decode("utf-8", "replace")
        if _HMAC_RUNTIME_KEY:
            err = err.replace(_HMAC_RUNTIME_KEY, "<redacted>")
        raise AssertionError(f"{exe} {verb} failed: {err}")
    return result


@pytest.fixture(scope="session", autouse=True)
def local_ocb_image():
    """Build oteru-collector:0.155.0 from the local Dockerfile if missing."""
    probed = docker("image", "inspect", IMAGE, check=False)
    if probed.returncode == 0:
        return IMAGE
    dockerfile = ROOT / "Dockerfile"
    assert dockerfile.is_file(), f"missing {dockerfile}"
    docker("build", "-t", IMAGE, "-f", str(dockerfile), str(ROOT), timeout=900)
    return IMAGE


@contextmanager
def collector(tmp_path, config, network=None):
    name = "oteru-privacy-" + uuid.uuid4().hex[:12]
    output = tmp_path / "telemetry.json"
    tmp_path.mkdir(parents=True, exist_ok=True)
    tmp_path.chmod(0o777)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    path.chmod(0o644)
    try:
        network_args = ["--network", network] if network else []
        docker(
            "run",
            "-d",
            "--name",
            name,
            "-p",
            "127.0.0.1::4317",
            "-p",
            "127.0.0.1::4318",
            *network_args,
            "--mount",
            f"type=bind,src={tmp_path},dst=/out",
            "-e",
            f"OTERU_PII_HMAC_KEY={_HMAC_RUNTIME_KEY}",
            IMAGE,
            "--config=/out/config.json",
        )
        ports = json.loads(docker("inspect", name).stdout)[0]["NetworkSettings"]["Ports"]
        http = "http://127.0.0.1:" + ports["4318/tcp"][0]["HostPort"]
        grpc_endpoint = "127.0.0.1:" + ports["4317/tcp"][0]["HostPort"]
        for _ in range(100):
            try:
                response = requests.post(http + "/v1/logs", json={}, timeout=0.3)
                if response.status_code == 200:
                    break
            except requests.RequestException:
                pass
            time.sleep(0.1)
        else:
            raise AssertionError(
                "Collector did not start:\n"
                + docker("logs", name).stdout
                + docker("logs", name).stderr
            )
        OUTPUT_CONTAINERS[output] = (name, "/out/telemetry.json")
        yield name, http, grpc_endpoint, output
    finally:
        OUTPUT_CONTAINERS.pop(output, None)
        docker("rm", "-f", name, check=False)


@contextmanager
def isolated_network():
    name = "oteru-privacy-" + uuid.uuid4().hex[:12]
    docker("network", "create", name)
    try:
        yield name
    finally:
        docker("network", "rm", name, check=False)


@contextmanager
def isolated_clickhouse(network):
    name = "oteru-privacy-db-" + uuid.uuid4().hex[:12]
    try:
        docker(
            "run",
            "--rm",
            "-d",
            "--name",
            name,
            "--network",
            network,
            "--network-alias",
            "clickhouse",
            "-e",
            "CLICKHOUSE_DB=otel",
            "-e",
            "CLICKHOUSE_USER=otel",
            "-e",
            "CLICKHOUSE_PASSWORD=otel",
            "clickhouse/clickhouse-server:26.9.23.15@sha256:d40ed32b22730ef2c1ba995e8affd28cf324b47399323f8b4c43f2cdcb7c35b4",
            timeout=300,
        )
        for _ in range(120):
            result = docker(
                "exec",
                name,
                "clickhouse-client",
                "--host",
                "clickhouse",
                "--user",
                "otel",
                "--password",
                "otel",
                "--query",
                "SELECT 1",
                check=False,
            )
            if result.returncode == 0:
                break
            time.sleep(0.1)
        else:
            raise AssertionError("isolated ClickHouse did not become ready")
        yield name
    finally:
        docker("rm", "-f", name, check=False)


def config_for(action="mask", profile="base"):
    filename = "oteru-collector-config" + ("" if profile == "base" else "." + profile) + ".yml"
    config = yaml.safe_load((ROOT / filename).read_text())
    if "processors" in config:
        source = default()
        source["identity"] = dict.fromkeys(IDENTITY, action)
        config["processors"] = policy.compile_policy(source)
    config["exporters"]["file"]["path"] = "/out/telemetry.json"
    config["exporters"]["file"]["flush_interval"] = "100ms"
    return config


def attributes(value):
    return [{"key": k, "value": copy.deepcopy(value)} for k in (*IDENTITY, *CONTENT)]


def payload(signal, context="record", value=None):
    value = value if value is not None else {"stringValue": CANARY}
    secret = attributes(value)
    benign = [
        {"key": "test.id", "value": {"stringValue": "control"}},
        {"key": "session.id", "value": {"stringValue": "session-control"}},
        {"key": "prompt.id", "value": {"stringValue": "prompt-control"}},
    ]
    now = str(time.time_ns())
    if signal == "logs":
        record = {
            "timeUnixNano": now,
            "body": {"stringValue": "operational body"},
            "attributes": benign,
        }
    elif signal == "traces":
        record = {
            "traceId": "AAAAAAAAAAAAAAAAAAAAAQ==",
            "spanId": "AAAAAAAAAAE=",
            "name": "operation",
            "startTimeUnixNano": now,
            "endTimeUnixNano": str(int(now) + 1000),
            "attributes": benign,
        }
    else:
        record = {
            "name": "privacy.test",
            "gauge": {
                "dataPoints": [{"timeUnixNano": now, "asDouble": 42.5, "attributes": benign}]
            },
        }
    resource_key, scope_key, record_key, _, _ = SIGNALS[signal]
    envelope = {
        "resource": {"attributes": []},
        scope_key: [{"scope": {"name": "privacy-test", "attributes": []}, record_key: [record]}],
    }
    if context == "resource":
        envelope["resource"]["attributes"] = secret
    elif context == "scope":
        envelope[scope_key][0]["scope"]["attributes"] = secret
    elif context == "event":
        record["events"] = [{"timeUnixNano": now, "name": "event", "attributes": secret}]
    elif context == "record":
        target = record if signal != "metrics" else record["gauge"]["dataPoints"][0]
        target["attributes"].extend(secret)
    return {resource_key: [envelope]}


def send(instance, signal, transport, data):
    _, http, endpoint, _ = instance
    request_cls, stub_cls = SIGNALS[signal][3:]
    request = ParseDict(data, request_cls())
    if transport == "http":
        response = requests.post(
            http + "/v1/" + signal,
            data=request.SerializeToString(),
            headers={"Content-Type": "application/x-protobuf"},
            timeout=10,
        )
        response.raise_for_status()
    else:
        with grpc.insecure_channel(endpoint) as channel:
            stub_cls(channel).Export(request, timeout=10)


def read_batches(path):
    if not path.exists():
        return []
    try:
        content = path.read_text()
    except PermissionError:
        # On Linux the Collector owns its 0600 file. Inspect the same output
        # through Docker without changing the Collector's production UID.
        name, container_path = OUTPUT_CONTAINERS[path]
        archive = docker("cp", f"{name}:{container_path}", "-", text=False).stdout
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as stream:
            member = stream.next()
            assert member is not None
            file = stream.extractfile(member)
            assert file is not None
            content = file.read().decode("utf-8")
    return [json.loads(line) for line in content.splitlines() if line.strip()]


def records(batches, signal):
    resource_key, scope_key, record_key, _, _ = SIGNALS[signal]
    return [
        r
        for batch in batches
        for env in batch.get(resource_key, [])
        for scope in env.get(scope_key, [])
        for r in scope.get(record_key, [])
    ]


def wait_records(path, signal, count):
    for _ in range(100):
        batches = read_batches(path)
        if len(records(batches, signal)) >= count:
            return batches
        time.sleep(0.05)
    raise AssertionError(
        f"expected {count} {signal} records, received {len(records(read_batches(path), signal))}"
    )


def walk(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk(item)


@pytest.mark.parametrize("action", ["mask", "drop"])
def test_identity_is_scrubbed_before_file_and_stdout(tmp_path, action):
    with collector(tmp_path, config_for(action)) as instance:
        send(instance, "logs", "http", payload("logs"))
        batches = wait_records(instance[3], "logs", 1)
        assert CANARY not in json.dumps(batches)
        attrs = {a["key"]: a["value"] for a in records(batches, "logs")[0]["attributes"]}
        for key in IDENTITY:
            if action == "mask":
                assert attrs[key] == {"stringValue": "REDACTED"}
            else:
                assert key not in attrs
        assert attrs["session.id"] == {"stringValue": "session-control"}
        assert records(batches, "logs")[0]["body"] == {"stringValue": "operational body"}
        output = docker("logs", instance[0])
        assert CANARY not in output.stdout + output.stderr


def test_hash_uses_versioned_field_domain_and_preserves_metric_identity(tmp_path):
    value = "same"
    expected_email = hmac_v1("user.email", value)
    expected_id = hmac_v1("user.id", value)
    assert expected_email.startswith("hmac:v1:")
    assert expected_email != expected_id
    with collector(tmp_path, config_for("hash")) as instance:
        send(instance, "logs", "http", payload("logs", value={"stringValue": value}))
        metric = payload("metrics", value={"stringValue": value})
        point = metric["resourceMetrics"][0]["scopeMetrics"][0]["metrics"][0]
        point = point["gauge"]["dataPoints"][0]
        point["attributes"] = [a for a in point["attributes"] if a["key"] not in CONTENT]
        send(instance, "metrics", "http", metric)
        log_batches = wait_records(instance[3], "logs", 1)
        metric_batches = wait_records(instance[3], "metrics", 1)
        for signal in ("logs", "metrics"):
            batches = log_batches if signal == "logs" else metric_batches
            record = records(batches, signal)[0]
            attributes = (
                record["attributes"]
                if signal == "logs"
                else record["gauge"]["dataPoints"][0]["attributes"]
            )
            values = {attribute["key"]: attribute["value"] for attribute in attributes}
            assert values["user.email"] == {"stringValue": expected_email}
            assert values["user.id"] == {"stringValue": expected_id}
            assert expected_email != expected_id


def test_reference_metrics_keep_all_934_points_and_measurements(tmp_path):
    source = {"resourceMetrics": []}
    sample = ROOT.parent / "oteru-emitter/samples/telemetry-sample.json"
    for line in sample.read_text().splitlines():
        source["resourceMetrics"].extend(json.loads(line).get("resourceMetrics", []))
    normalized = MessageToDict(
        ParseDict(source, ExportMetricsServiceRequest()), use_integers_for_enums=True
    )

    def points(envelopes):
        return [
            point
            for resource in envelopes
            for scope in resource.get("scopeMetrics", [])
            for metric in scope.get("metrics", [])
            for point in metric["sum"].get("dataPoints", [])
        ]

    assert len(points(normalized["resourceMetrics"])) == 934

    def pseudonymized(value):
        if isinstance(value, list):
            return [pseudonymized(item) for item in value]
        if isinstance(value, dict):
            if value.get("key") in IDENTITY and isinstance(value.get("value"), dict):
                original = value["value"]["stringValue"]
                return {**value, "value": {"stringValue": hmac_v1(value["key"], original)}}
            return {key: pseudonymized(item) for key, item in value.items()}
        return value

    with collector(tmp_path, config_for("hash")) as instance:
        send(instance, "metrics", "http", source)
        batches = wait_records(instance[3], "metrics", 439)
        actual = {
            "resourceMetrics": [
                resource for batch in batches for resource in batch.get("resourceMetrics", [])
            ]
        }
        assert len(points(actual["resourceMetrics"])) == 934
        # Compares every metric name, value, timestamp, temporality, monotonicity,
        # non-sensitive dimension and independently computed pseudonym.
        assert actual == pseudonymized(normalized)


@pytest.mark.parametrize("signal", ["logs", "traces", "metrics"])
def test_hash_covers_six_fields_in_every_supported_context(tmp_path, signal):
    contexts = ["resource", "scope", "record"] + (["event"] if signal == "traces" else [])
    value = "principal-a"
    with collector(tmp_path, config_for("hash")) as instance:
        for context in contexts:
            data = payload(signal, context, {"stringValue": value})
            if signal == "metrics":
                for item in walk(data):
                    if isinstance(item.get("attributes"), list):
                        item["attributes"] = [
                            attr for attr in item["attributes"] if attr["key"] not in CONTENT
                        ]
            send(instance, signal, "grpc", data)
        batches = wait_records(instance[3], signal, len(contexts))
        for key in IDENTITY:
            expected = hmac_v1(key, value)
            values = [
                item["value"]["stringValue"] for item in walk(batches) if item.get("key") == key
            ]
            assert values == [expected] * len(contexts)
        assert value not in json.dumps(batches)


def test_two_principals_keep_stable_pseudonyms_across_signals_and_restart(tmp_path):
    seen = {}
    for run in ("before", "after"):
        with collector(tmp_path / run, config_for("hash")) as instance:
            for signal in ("logs", "traces", "metrics"):
                for value in ("principal-a", "principal-b", "principal-a"):
                    data = payload(signal, "record", {"stringValue": value})
                    if signal == "metrics":
                        point = data["resourceMetrics"][0]["scopeMetrics"][0]
                        point = point["metrics"][0]["gauge"]["dataPoints"][0]
                        point["attributes"] = [
                            attr for attr in point["attributes"] if attr["key"] not in CONTENT
                        ]
                    send(instance, signal, "http", data)
                batches = wait_records(instance[3], signal, 3)
                for value, record in zip(
                    ("principal-a", "principal-b", "principal-a"),
                    records(batches, signal),
                    strict=True,
                ):
                    attrs = (
                        record["attributes"]
                        if signal != "metrics"
                        else record["gauge"]["dataPoints"][0]["attributes"]
                    )
                    user_id = next(
                        a["value"]["stringValue"] for a in attrs if a["key"] == "user.id"
                    )
                    expected = hmac_v1("user.id", value)
                    assert user_id == expected
                    seen.setdefault(value, set()).add(user_id)
            assert "principal-a" not in json.dumps(read_batches(instance[3]))
            assert "principal-b" not in json.dumps(read_batches(instance[3]))
    assert len(seen["principal-a"]) == len(seen["principal-b"]) == 1
    assert seen["principal-a"] != seen["principal-b"]


@pytest.mark.parametrize("signal", ["logs", "traces", "metrics"])
@pytest.mark.parametrize("context", ["resource", "scope", "record"])
@pytest.mark.parametrize("invalid", [{"stringValue": ""}, {"intValue": "17"}])
def test_invalid_hash_input_rejects_only_unsafe_item(tmp_path, signal, context, invalid):
    unsafe = payload(signal, context, invalid)
    control = payload(signal, "benign")
    resource_key = SIGNALS[signal][0]
    if signal == "metrics":
        for item in walk(unsafe):
            if isinstance(item.get("attributes"), list):
                item["attributes"] = [
                    attr for attr in item["attributes"] if attr["key"] not in CONTENT
                ]
    with collector(tmp_path, config_for("hash")) as instance:
        send(instance, signal, "http", {resource_key: unsafe[resource_key] + control[resource_key]})
        batches = wait_records(instance[3], signal, 1)
        time.sleep(0.2)
        assert len(records(read_batches(instance[3]), signal)) == 1
        record = records(batches, signal)[0]
        attrs = (
            record["attributes"]
            if signal != "metrics"
            else record["gauge"]["dataPoints"][0]["attributes"]
        )
        assert {a["key"]: a["value"] for a in attrs}["test.id"] == {"stringValue": "control"}
        assert all(a["key"] not in IDENTITY for a in attrs)


@pytest.mark.parametrize("invalid", [{"stringValue": ""}, {"intValue": "17"}])
def test_invalid_hash_input_drops_span_event_but_keeps_spans(tmp_path, invalid):
    unsafe = payload("traces", "event", invalid)
    control = payload("traces", "benign")
    with collector(tmp_path, config_for("hash")) as instance:
        send(
            instance,
            "traces",
            "http",
            {"resourceSpans": unsafe["resourceSpans"] + control["resourceSpans"]},
        )
        batches = wait_records(instance[3], "traces", 2)
        spans = records(batches, "traces")
        assert len(spans) == 2
        assert all(not span.get("events") for span in spans)
        assert all(
            {a["key"]: a["value"] for a in span["attributes"]}["test.id"]
            == {"stringValue": "control"}
            for span in spans
        )


VALUES = [
    {"stringValue": CANARY},
    {"stringValue": ""},
    {"intValue": "17"},
    {"boolValue": False},
    {"doubleValue": 12.5},
    {},
    {"arrayValue": {"values": [{"stringValue": CANARY}]}},
    {"kvlistValue": {"values": [{"key": "nested", "value": {"stringValue": CANARY}}]}},
]


@pytest.mark.parametrize("action", ["mask", "drop"])
@pytest.mark.parametrize("transport", ["http", "grpc"])
@pytest.mark.parametrize("signal", ["logs", "traces", "metrics"])
def test_signal_context_type_contract(tmp_path, action, transport, signal):
    contexts = ["resource", "scope", "record"] + (["event"] if signal == "traces" else [])
    with collector(tmp_path, config_for(action)) as instance:
        for context in contexts:
            for value in VALUES:
                send(instance, signal, transport, payload(signal, context, value))
        send(instance, signal, transport, payload(signal, "benign"))
        expected = 1 if signal == "metrics" else len(contexts) * len(VALUES) + 1
        batches = wait_records(instance[3], signal, expected)
        # Allow asynchronous exporter flushes to reveal unexpected rejected data.
        time.sleep(0.2)
        batches = read_batches(instance[3])
        assert len(records(batches, signal)) == expected
        for item in walk(batches):
            if item.get("key") in IDENTITY:
                assert action == "mask" and signal != "metrics"
                assert item["value"] == {"stringValue": "REDACTED"}
            assert item.get("key") not in CONTENT
        if signal != "metrics" and action == "mask":
            assert sum(item.get("key") in IDENTITY for item in walk(batches)) == len(
                contexts
            ) * len(VALUES) * len(IDENTITY)
        if signal == "metrics":
            point = records(batches, signal)[0]["gauge"]["dataPoints"][0]
            assert point["asDouble"] == 42.5
            assert {a["key"]: a["value"] for a in point["attributes"]}["session.id"] == {
                "stringValue": "session-control"
            }
        else:
            for record in records(batches, signal):
                attrs = {a["key"]: a["value"] for a in record["attributes"]}
                assert attrs["prompt.id"] == {"stringValue": "prompt-control"}
                if signal == "logs":
                    assert record["body"] == {"stringValue": "operational body"}
                else:
                    assert record["traceId"] == "00000000000000000000000000000001"
                    assert int(record["endTimeUnixNano"]) - int(record["startTimeUnixNano"]) == 1000
        output = docker("logs", instance[0])
        assert CANARY not in json.dumps(batches) + output.stdout + output.stderr


@pytest.mark.parametrize("transport", ["http", "grpc"])
def test_runtime_error_fails_closed_without_payload_in_diagnostics(tmp_path, transport):
    config = config_for()
    config["processors"]["transform/privacy"]["log_statements"].insert(
        0,
        {
            "context": "log",
            "statements": [
                'set(log.attributes["test.result"], Len(log.attributes["test.error"])) '
                'where log.attributes["test.error"] != nil'
            ],
        },
    )
    with collector(tmp_path, config) as instance:
        data = payload("logs")
        data["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]["attributes"].append(
            {
                "key": "test.error",
                "value": {"intValue": "42"},
            }
        )
        with pytest.raises((requests.HTTPError, grpc.RpcError)):
            send(instance, "logs", transport, data)
        send(instance, "logs", transport, payload("logs", "benign"))
        batches = wait_records(instance[3], "logs", 1)
        assert len(records(batches, "logs")) == 1
        output = docker("logs", instance[0])
        assert CANARY not in json.dumps(batches) + output.stdout + output.stderr
        assert "error" in output.stderr.lower()


def test_content_controls_are_independent(tmp_path):
    source = default()
    source["content"] = {"known_fields": "preserve", "log_body": "drop"}
    config = config_for()
    config["processors"] = policy.compile_policy(source)
    with collector(tmp_path, config) as instance:
        send(instance, "logs", "http", payload("logs"))
        batches = wait_records(instance[3], "logs", 1)
        record = records(batches, "logs")[0]
        assert record["body"] == {"stringValue": "REDACTED"}
        attrs = {a["key"]: a["value"] for a in record["attributes"]}
        assert attrs["user.email"] == {
            "stringValue": hmac_v1("user.email", "privacy-canary@example.com")
        }
        assert attrs["user_prompt"] == {"stringValue": CANARY}


def test_bypass_mutation_is_detectable(tmp_path):
    config = config_for()
    config["service"]["pipelines"]["logs"].pop("processors")
    with collector(tmp_path, config) as instance:
        send(instance, "logs", "http", payload("logs"))
        assert CANARY in json.dumps(wait_records(instance[3], "logs", 1))


@pytest.mark.parametrize("signal", ["traces", "metrics"])
@pytest.mark.parametrize("action", ["mask", "hash"])
def test_uninspectable_links_and_exemplars_are_rejected(tmp_path, signal, action):
    with collector(tmp_path, config_for(action)) as instance:
        unsafe = payload(signal, "benign")
        if signal == "traces":
            span = unsafe["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
            span["links"] = [
                {
                    "traceId": "AAAAAAAAAAAAAAAAAAAAAg==",
                    "spanId": "AAAAAAAAAAI=",
                    "attributes": attributes({"stringValue": CANARY}),
                }
            ]
        else:
            metric = unsafe["resourceMetrics"][0]["scopeMetrics"][0]["metrics"][0]
            point = metric["gauge"]["dataPoints"][0]
            point["exemplars"] = [
                {
                    "timeUnixNano": str(time.time_ns()),
                    "asDouble": 42.5,
                    "filteredAttributes": attributes({"stringValue": CANARY}),
                }
            ]
        send(instance, signal, "grpc", unsafe)
        send(instance, signal, "grpc", payload(signal, "benign"))
        batches = wait_records(instance[3], signal, 1)
        time.sleep(0.2)
        assert len(records(read_batches(instance[3]), signal)) == 1
        output = docker("logs", instance[0])
        assert CANARY not in json.dumps(batches) + output.stdout + output.stderr


@pytest.mark.parametrize(
    "profile,exporter",
    [
        ("clickstack", "otlphttp/clickstack"),
        ("clickstack-local", "otlp_http/hyperdx"),
    ],
)
@pytest.mark.parametrize("action", ["mask", "hash"])
def test_clickstack_forwarder_receives_only_scrubbed_otlp(tmp_path, profile, exporter, action):
    sink_dir = tmp_path / "sink"
    sink_dir.mkdir(mode=0o777)
    sink_dir.chmod(0o777)
    sink_config = {
        "receivers": {"otlp": {"protocols": {"http": {"endpoint": "0.0.0.0:4318"}}}},
        "exporters": {"file": {"path": "/out/telemetry.json", "flush_interval": "100ms"}},
        "service": {
            "pipelines": {
                signal: {"receivers": ["otlp"], "exporters": ["file"]}
                for signal in ("logs", "traces", "metrics")
            }
        },
    }
    (sink_dir / "config.json").write_text(json.dumps(sink_config))
    sink_name = "oteru-privacy-sink-" + uuid.uuid4().hex[:12]
    with isolated_network() as network:
        try:
            docker(
                "run",
                "-d",
                "--rm",
                "--name",
                sink_name,
                "--network",
                network,
                "--network-alias",
                "sink",
                "--mount",
                f"type=bind,src={sink_dir},dst=/out",
                IMAGE,
                "--config=/out/config.json",
            )
            OUTPUT_CONTAINERS[sink_dir / "telemetry.json"] = (sink_name, "/out/telemetry.json")
            source_config = config_for(action, profile=profile)
            source_config["exporters"][exporter]["endpoint"] = "http://sink:4318"
            source_config["exporters"][exporter]["headers"] = {"authorization": "test-only"}
            with collector(tmp_path / "source", source_config, network) as source:
                for signal in ("logs", "traces", "metrics"):
                    data = payload(signal)
                    if signal == "metrics" and action == "hash":
                        point = data["resourceMetrics"][0]["scopeMetrics"][0]
                        point = point["metrics"][0]["gauge"]["dataPoints"][0]
                        point["attributes"] = [
                            attr for attr in point["attributes"] if attr["key"] not in CONTENT
                        ]
                    send(source, signal, "http", data)
                send(source, "metrics", "http", payload("metrics", "benign"))
                for signal in ("logs", "traces", "metrics"):
                    expected_count = 2 if signal == "metrics" and action == "hash" else 1
                    wait_records(sink_dir / "telemetry.json", signal, expected_count)
                batches = read_batches(sink_dir / "telemetry.json")
                assert CANARY not in json.dumps(batches)
                assert len(records(batches, "logs")) == 1
                assert len(records(batches, "traces")) == 1
                assert len(records(batches, "metrics")) == (2 if action == "hash" else 1)
                if action == "hash":
                    expected = hmac_v1("user.email", "privacy-canary@example.com")
                    assert expected in json.dumps(batches)
                assert CANARY not in json.dumps(read_batches(source[3]))
        finally:
            OUTPUT_CONTAINERS.pop(sink_dir / "telemetry.json", None)
            docker("rm", "-f", sink_name, check=False)


@pytest.mark.parametrize("action", ["mask", "hash"])
def test_clickhouse_tables_receive_only_scrubbed_records(tmp_path, action):
    database_name = "oteru-privacy-db-" + uuid.uuid4().hex[:12]
    with isolated_network() as network:
        try:
            docker(
                "run",
                "--rm",
                "-d",
                "--name",
                database_name,
                "--network",
                network,
                "--network-alias",
                "clickhouse",
                "-e",
                "CLICKHOUSE_DB=otel",
                "-e",
                "CLICKHOUSE_USER=otel",
                "-e",
                "CLICKHOUSE_PASSWORD=otel",
                "clickhouse/clickhouse-server:26.9.23.15@sha256:d40ed32b22730ef2c1ba995e8affd28cf324b47399323f8b4c43f2cdcb7c35b4",
                timeout=300,
            )
            for _ in range(120):
                response = docker(
                    "exec",
                    database_name,
                    "clickhouse-client",
                    "--host",
                    "clickhouse",
                    "--user",
                    "otel",
                    "--password",
                    "otel",
                    "--query",
                    "SELECT 1",
                    check=False,
                )
                if response.returncode == 0:
                    break
                time.sleep(0.1)
            else:
                raise AssertionError("isolated ClickHouse did not become ready")
            with collector(
                tmp_path / "source", config_for(action, profile="clickhouse"), network
            ) as source:
                for signal in ("logs", "traces", "metrics"):
                    data = payload(signal)
                    if signal == "metrics" and action == "hash":
                        point = data["resourceMetrics"][0]["scopeMetrics"][0]
                        point = point["metrics"][0]["gauge"]["dataPoints"][0]
                        point["attributes"] = [
                            attr for attr in point["attributes"] if attr["key"] not in CONTENT
                        ]
                    send(source, signal, "grpc", data)
                send(source, "metrics", "grpc", payload("metrics", "benign"))
                tables = ("otel_logs", "otel_traces", "otel_metrics_gauge")
                for table in tables:
                    expected_count = 2 if table == "otel_metrics_gauge" and action == "hash" else 1
                    for _ in range(100):
                        result = docker(
                            "exec",
                            database_name,
                            "clickhouse-client",
                            "--user",
                            "otel",
                            "--password",
                            "otel",
                            "--query",
                            f"SELECT * FROM otel.{table} FORMAT JSONEachRow",
                            check=False,
                        )
                        if (
                            result.returncode == 0
                            and len(result.stdout.splitlines()) == expected_count
                        ):
                            break
                        time.sleep(0.1)
                    else:
                        raise AssertionError(f"no records in {table}")
                    assert len(result.stdout.splitlines()) == expected_count
                    assert CANARY not in result.stdout
                    if action == "hash":
                        expected = hmac_v1("user.email", "privacy-canary@example.com")
                        assert expected in result.stdout
                assert CANARY not in json.dumps(read_batches(source[3]))
        finally:
            docker("rm", "-f", database_name, check=False)


def test_clickhouse_cost_volume_and_trend_by_two_pseudonyms(tmp_path):
    minute = time.time_ns() // 60_000_000_000 * 60_000_000_000
    expected = {}
    with isolated_network() as network, isolated_clickhouse(network) as database:
        with collector(tmp_path, config_for("hash", profile="clickhouse"), network) as instance:
            for principal, period, name, value in (
                ("principal-a", 0, "privacy.cost", 1.25),
                ("principal-a", 0, "privacy.volume", 100.0),
                ("principal-a", 1, "privacy.cost", 2.5),
                ("principal-a", 1, "privacy.volume", 150.0),
                ("principal-b", 0, "privacy.cost", 3.0),
                ("principal-b", 0, "privacy.volume", 200.0),
                ("principal-b", 1, "privacy.cost", 4.0),
                ("principal-b", 1, "privacy.volume", 300.0),
            ):
                timestamp = minute + (period * 60 + 10) * 1_000_000_000
                data = {
                    "resourceMetrics": [
                        {
                            "scopeMetrics": [
                                {
                                    "scope": {"name": "privacy-analytics"},
                                    "metrics": [
                                        {
                                            "name": name,
                                            "sum": {
                                                "aggregationTemporality": 1,
                                                "isMonotonic": False,
                                                "dataPoints": [
                                                    {
                                                        "timeUnixNano": str(timestamp),
                                                        "asDouble": value,
                                                        "attributes": [
                                                            {
                                                                "key": "user.id",
                                                                "value": {"stringValue": principal},
                                                            },
                                                            {
                                                                "key": "environment",
                                                                "value": {"stringValue": "test"},
                                                            },
                                                        ],
                                                    }
                                                ],
                                            },
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                }
                send(instance, "metrics", "http", data)
                pseudonym = hmac_v1("user.id", principal)
                expected[(pseudonym, name, period)] = value

            query = (
                "SELECT Attributes['user.id'] AS principal, MetricName AS metric, "
                "intDiv(toUnixTimestamp(TimeUnix), 60) AS minute, sum(Value) AS total "
                "FROM otel.otel_metrics_sum WHERE MetricName IN ('privacy.cost', 'privacy.volume') "
                "GROUP BY principal, metric, minute ORDER BY principal, metric, minute "
                "FORMAT JSONEachRow"
            )
            for _ in range(100):
                result = docker(
                    "exec",
                    database,
                    "clickhouse-client",
                    "--user",
                    "otel",
                    "--password",
                    "otel",
                    "--query",
                    query,
                    check=False,
                )
                if result.returncode == 0 and len(result.stdout.splitlines()) == 8:
                    break
                time.sleep(0.1)
            else:
                raise AssertionError("eight cost/volume trend buckets were not queryable")
            rows = [json.loads(line) for line in result.stdout.splitlines()]
            actual = {
                (
                    row["principal"],
                    row["metric"],
                    int(row["minute"]) - minute // 60_000_000_000,
                ): float(row["total"])
                for row in rows
            }
            assert actual == expected
            assert len({row["principal"] for row in rows}) == 2
            assert "principal-a" not in result.stdout
            assert "principal-b" not in result.stdout


@pytest.mark.parametrize("profile", ["base", "clickhouse", "clickstack", "clickstack-local"])
def test_production_config_validates_in_pinned_collector(tmp_path, profile):
    output = tmp_path / "processors.json"
    assert (
        policy.main(["--policy", str(ROOT / "privacy/policy.json"), "--output", str(output)]) == 0
    )
    suffix = "" if profile == "base" else "." + profile
    docker(
        "run",
        "--rm",
        "--network",
        "none",
        "-e",
        "CLICKSTACK_ENDPOINT=http://127.0.0.1:4318",
        "-e",
        "CLICKSTACK_API_KEY=test-only",
        "-e",
        f"OTERU_PII_HMAC_KEY={_HMAC_RUNTIME_KEY}",
        "--mount",
        f"type=bind,src={tmp_path},dst=/etc/oteru-privacy,readonly",
        "--mount",
        f"type=bind,src={ROOT},dst=/config,readonly",
        IMAGE,
        "validate",
        f"--config=/config/oteru-collector-config{suffix}.yml",
    )


@pytest.mark.parametrize(
    "profile", ["base", "clickhouse", "clickstack", "clickstack-local", "hyperdx"]
)
def test_effective_compose_mounts_policy_before_every_profile(profile):
    import os

    command = [engine(), "compose", "-f", str(ROOT / "docker-compose.yml")]
    if profile in ("clickhouse", "hyperdx"):
        command += ["-f", str(ROOT / "docker-compose.clickhouse.yml")]
    if profile == "clickstack":
        command += ["-f", str(ROOT / "docker-compose.clickstack.yml")]
    if profile == "clickstack-local":
        command += ["-f", str(ROOT / "docker-compose.clickstack-local.yml")]
    if profile == "hyperdx":
        command += ["-f", str(ROOT / "docker-compose.hyperdx.yml")]
    env = {
        **os.environ,
        "CLICKSTACK_ENDPOINT": "http://test-only:4318",
        "CLICKSTACK_API_KEY": "test-only",
        "HYPERDX_API_KEY": "test-only",
    }
    result = subprocess.run(
        command + ["config", "--format", "json"],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    config = json.loads(result.stdout)
    service = config["services"]["oteru-collector"]
    assert service["depends_on"]["privacy-policy"]["condition"] == "service_completed_successfully"
    mounts = {v["target"]: v for v in service["volumes"]}
    assert mounts["/etc/oteru-privacy"]["read_only"]
    suffix = "" if profile == "base" else ".clickhouse" if profile == "hyperdx" else "." + profile
    assert mounts["/etc/oteru-collector-config.yml"]["source"].endswith(
        f"oteru-collector-config{suffix}.yml"
    )


def test_policy_activation_replaces_valid_policy_and_stops_on_invalid(tmp_path, monkeypatch):
    import importlib.util
    import shutil

    stack = tmp_path / "stack"
    stack.mkdir()
    shutil.copy(ROOT / "docker-compose.yml", stack)
    shutil.copy(ROOT / "oteru-collector-config.yml", stack)
    shutil.copytree(
        ROOT / "privacy", stack / "privacy", ignore=shutil.ignore_patterns("__pycache__")
    )
    (stack / "telemetry").mkdir(mode=0o777)
    (stack / "telemetry").chmod(0o777)
    # Isolate both volumes and ports from any live Oteru installation.
    override = stack / "test.yml"
    override.write_text(
        "services:\n  oteru-collector:\n"
        '    ports: !override ["127.0.0.1::4317", "127.0.0.1::4318"]\n'
    )
    project = "privacy-" + uuid.uuid4().hex[:12]
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", project)
    # apply_privacy explicitly sets -f; use the test override as the base in this isolated copy.
    resolved = subprocess.run(
        [
            engine(),
            "compose",
            "-f",
            str(stack / "docker-compose.yml"),
            "-f",
            str(override),
            "config",
            "--format",
            "json",
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    (stack / "docker-compose.yml").write_text(resolved.stdout)
    compose = ["compose", "-f", str(stack / "docker-compose.yml")]
    spec = importlib.util.spec_from_file_location(
        "apply_privacy", ROOT.parent / "scripts/apply_privacy.py"
    )
    activation = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(activation)
    activation.ROOT = stack
    try:
        docker(*compose, "up", "-d")
        container_id = docker(*compose, "ps", "-q", "oteru-collector").stdout.strip()
        ports = json.loads(docker("inspect", container_id).stdout)[0]["NetworkSettings"]["Ports"]
        instance = (
            container_id,
            "http://127.0.0.1:" + ports["4318/tcp"][0]["HostPort"],
            "",
            stack / "telemetry/telemetry.json",
        )
        OUTPUT_CONTAINERS[instance[3]] = (container_id, "/telemetry/telemetry.json")
        time.sleep(0.5)
        send(instance, "logs", "http", payload("logs"))
        assert CANARY not in json.dumps(wait_records(instance[3], "logs", 1))
        source = default()
        source["identity"] = dict.fromkeys(IDENTITY, "drop")
        (stack / "privacy/policy.json").write_text(json.dumps(source))
        assert activation.main([]) == 0
        new_id = docker(*compose, "ps", "-q", "oteru-collector").stdout.strip()
        ports = json.loads(docker("inspect", new_id).stdout)[0]["NetworkSettings"]["Ports"]
        instance = (new_id, "http://127.0.0.1:" + ports["4318/tcp"][0]["HostPort"], "", instance[3])
        OUTPUT_CONTAINERS[instance[3]] = (new_id, "/telemetry/telemetry.json")
        time.sleep(0.5)
        send(instance, "logs", "http", payload("logs"))
        data = wait_records(instance[3], "logs", 2)
        assert all(a["key"] not in IDENTITY for a in records(data, "logs")[-1]["attributes"])
        (stack / "privacy/policy.json").write_text('{"version": 99}')
        assert activation.main([]) != 0
        assert not docker(
            *compose, "ps", "-q", "--status", "running", "oteru-collector"
        ).stdout.strip()
        # The supported startup path also fails, even with a previously populated volume.
        assert docker(*compose, "up", "-d", "--force-recreate", check=False).returncode != 0
        assert not docker(
            *compose, "ps", "-q", "--status", "running", "oteru-collector"
        ).stdout.strip()
    finally:
        OUTPUT_CONTAINERS.pop(stack / "telemetry/telemetry.json", None)
        docker(*compose, "down", "-v", "--remove-orphans", check=False)
