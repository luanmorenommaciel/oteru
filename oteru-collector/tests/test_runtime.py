"""Black-box contracts against the pinned Collector, never a Python scrubber.

Requires Docker; unavailable runtime is a failure, not a successful skip.
All published ports are random, loopback-only. Only synthetic data is sent.
"""

from __future__ import annotations

import copy
import io
import json
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
from google.protobuf.json_format import ParseDict
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest
from opentelemetry.proto.collector.logs.v1.logs_service_pb2_grpc import LogsServiceStub
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2_grpc import MetricsServiceStub
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2_grpc import TraceServiceStub
from support import ROOT, default, policy

IMAGE = (
    "otel/opentelemetry-collector-contrib@"
    "sha256:4935caa35e9a4cb387e35732e8fb22b2b5759af8d12e7043357f03837f6e8df5"
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


@pytest.fixture(autouse=True)
def docker_mount_access(tmp_path):
    """Allow the container UID to traverse pytest's private Linux temp parents."""
    temp_root = Path(tempfile.gettempdir()).resolve()
    for directory in (tmp_path, *tmp_path.parents):
        if directory == temp_root:
            break
        directory.chmod(directory.stat().st_mode | 0o111)


def docker(*args, check=True):
    result = subprocess.run(
        ["docker", *args], check=False, text=True, capture_output=True, timeout=120
    )
    if check and result.returncode:
        raise AssertionError(f"docker {args[0]} failed: {result.stderr}")
    return result


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
        archive = subprocess.run(
            ["docker", "cp", f"{name}:{container_path}", "-"],
            check=True,
            capture_output=True,
            timeout=120,
        ).stdout
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
        assert attrs["user.email"] == {"stringValue": "REDACTED"}
        assert attrs["user_prompt"] == {"stringValue": CANARY}


def test_bypass_mutation_is_detectable(tmp_path):
    config = config_for()
    config["service"]["pipelines"]["logs"].pop("processors")
    with collector(tmp_path, config) as instance:
        send(instance, "logs", "http", payload("logs"))
        assert CANARY in json.dumps(wait_records(instance[3], "logs", 1))


@pytest.mark.parametrize("signal", ["traces", "metrics"])
def test_uninspectable_links_and_exemplars_are_rejected(tmp_path, signal):
    with collector(tmp_path, config_for()) as instance:
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


def test_clickstack_forwarder_receives_only_scrubbed_otlp(tmp_path):
    sink_dir = tmp_path / "sink"
    sink_dir.mkdir(mode=0o777)
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
            source_config = config_for(profile="clickstack")
            source_config["exporters"]["otlphttp/clickstack"]["endpoint"] = "http://sink:4318"
            with collector(tmp_path / "source", source_config, network) as source:
                for signal in ("logs", "traces", "metrics"):
                    send(source, signal, "http", payload(signal))
                send(source, "metrics", "http", payload("metrics", "benign"))
                for signal in ("logs", "traces", "metrics"):
                    wait_records(sink_dir / "telemetry.json", signal, 1)
                batches = read_batches(sink_dir / "telemetry.json")
                assert CANARY not in json.dumps(batches)
                assert all(len(records(batches, signal)) == 1 for signal in SIGNALS)
                assert CANARY not in json.dumps(read_batches(source[3]))
        finally:
            OUTPUT_CONTAINERS.pop(sink_dir / "telemetry.json", None)
            docker("rm", "-f", sink_name, check=False)


def test_clickhouse_tables_receive_only_scrubbed_records(tmp_path):
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
                "clickhouse/clickhouse-server:latest",
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
                tmp_path / "source", config_for(profile="clickhouse"), network
            ) as source:
                for signal in ("logs", "traces", "metrics"):
                    send(source, signal, "grpc", payload(signal))
                send(source, "metrics", "grpc", payload("metrics", "benign"))
                tables = ("otel_logs", "otel_traces", "otel_metrics_gauge")
                for table in tables:
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
                        if result.returncode == 0 and result.stdout.strip():
                            break
                        time.sleep(0.1)
                    else:
                        raise AssertionError(f"no records in {table}")
                    assert len(result.stdout.splitlines()) == 1
                    assert CANARY not in result.stdout
                assert CANARY not in json.dumps(read_batches(source[3]))
        finally:
            docker("rm", "-f", database_name, check=False)


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

    command = ["docker", "compose", "-f", str(ROOT / "docker-compose.yml")]
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
            "docker",
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
