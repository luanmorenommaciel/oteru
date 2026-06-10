"""Oteru OTel emitter — forges OTLP telemetry traffic faithful to Claude Code.

Phase 1: byte-faithful replay of OTLP/JSON captures, dual-transport
(http/protobuf + gRPC), realtime pacing, with timestamp and ID restamping.
"""

__version__ = "0.1.0"
