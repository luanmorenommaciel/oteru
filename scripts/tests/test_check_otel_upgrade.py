from scripts.check_otel_upgrade import evaluate, parse_modules


def test_reports_blocked_and_ready_modules() -> None:
    modules = parse_modules(
        '{"Path":"go.opentelemetry.io/otel/sdk/log","Version":"v0.21.0"}\n'
        '{"Path":"go.opentelemetry.io/otel/exporters/otlp/otlplog/otlploggrpc","Version":"v0.20.0"}\n'
        '{"Path":"go.opentelemetry.io/otel/exporters/otlp/otlptrace","Version":"v1.45.1"}\n'
    )
    report = evaluate(modules)
    assert report["go.opentelemetry.io/otel/sdk/log"]["ready"] is True
    assert (
        report["go.opentelemetry.io/otel/exporters/otlp/otlplog/otlploggrpc"]["ready"]
        is False
    )
    assert report["go.opentelemetry.io/otel/exporters/otlp/otlptrace"]["ready"] is True


def test_missing_module_is_not_ready() -> None:
    report = evaluate({})
    assert all(item["ready"] is False for item in report.values())
