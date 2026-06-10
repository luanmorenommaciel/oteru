"""Oteru OTel emitter — forja tráfego de telemetria OTLP fiel ao Claude Code.

Fase 1: replay byte-fiel de capturas OTLP/JSON, dual-transport
(http/protobuf + gRPC), em tempo real, com restamp de timestamps e IDs.
"""

__version__ = "0.1.0"
