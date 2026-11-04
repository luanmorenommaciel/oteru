# OCB collector — vuln proof (otelcol-oteru 0.155.0)

Toolchain: Go 1.26.10 (`GOTOOLCHAIN=go1.26.10`).
Collector line: every `go.opentelemetry.io/collector*` and
`opentelemetry-collector-contrib*` module is v0.155.0 or v1.61.0.
OCB has no `requires` field; floors are explicit `replaces` in
`oteru-collector/builder-config.yaml`.

## Replaces (HIGH floors)

| Module | Pin | Closes |
|---|---|---|
| `golang.org/x/net` | v0.60.0 | GO-2026-6617 / 6612 / 6611 / 6610 / 6603 |
| `google.golang.org/grpc` | v1.83.2 | GO-2026-6443 (needs 1.83.2), 6348, 6061 |
| `golang.org/x/text` | v0.41.0 | GO-2026-5970 (@0.39.0), GO-2026-6629 (@0.41.0) |
| `github.com/klauspost/compress` | v1.18.7 | GO-2026-5841 / CVE-2026-63209 |

`compress` is pulled by `fileexporter` → `klauspost/compress/zstd`
(`go mod why`). v1.18.7 is a patch on the same minor.

## GO-2026-5932 — `golang.org/x/crypto/openpgp` (no upstream fix)

**ID:** GO-2026-5932. Module `golang.org/x/crypto` (resolved v0.57.0).
**Fixed in:** N/A. The `openpgp` package is unmaintained and marked
unsafe by design; there is no patched version of `x/crypto` that
removes the advisory.

### Where `x/crypto` comes from (not `openpgp`)

`go mod why -m golang.org/x/crypto` and `go mod graph`:

```
oteru-collector
  → clickhouseexporter v0.155.0
    → go.opentelemetry.io/collector/config/configtls v1.61.0
      → github.com/foxboron/go-tpm-keyfiles
        → golang.org/x/crypto/{chacha20poly1305,cryptobyte,hkdf}
```

The same module is also required by `configgrpc` / `confighttp` /
`otlpreceiver` / `otlphttpexporter` / `service` on the v0.155.0 line
(TLS). Dropping `x/crypto` would mean dropping ClickHouse TLS or the
OTLP stack — that leaves the collector line.

### The `openpgp` *package* is not compiled

```
go mod why golang.org/x/crypto/openpgp
# (main module does not need package golang.org/x/crypto/openpgp)

go list -deps . | grep openpgp
# (empty)

# unstripped rebuild (OCB default is -ldflags=-s -w, no symbol section)
go build -o otelcol-oteru.unstripped .
go tool nm otelcol-oteru.unstripped | grep -ci openpgp
# 0
# linked x/crypto packages: chacha20, chacha20poly1305, cryptobyte, hkdf
```

govulncheck **source** does not mark GO-2026-5932 reachable. Binary
mode still attributes the module-level advisory to `x/crypto@v0.57.0`
because the module is present; that is not a linked `openpgp` symbol.
Trivy gobinary / go.mod report 0 HIGH/CRITICAL after the compress pin.

`go.mod` cannot exclude a single package inside `golang.org/x/crypto`.
A `replace` of the whole module cannot delete `openpgp` without a
fork, and a fork would leave the v0.155.0 / v1.61.0 line.

### Why this is the justified Trivy / govulncheck remainder

- Not source-reachable (`go mod why` / `go list -deps` / `nm`).
- No upstream fix (`Fixed in: N/A`).
- Cannot drop `x/crypto` without dropping `clickhouseexporter` or
  `configtls` (both v0.155.0 / v1.61.0).

## Remaining not-HIGH (not part of the Trivy HIGH/CRITICAL bar)

Collector self-telemetry, reachable, **MODERATE/LOW**:

- GO-2026-6615 `go.opentelemetry.io/otel/sdk/log@v0.20.0` — MODERATE
- GO-2026-6508 `.../otlploggrpc@v0.20.0` — MODERATE
- GO-2026-6505 `otel/sdk` + `otlptrace*` @v1.44.0 — LOW

Fixing these would mean replacing the OTel Go SDK that otelcol
v0.155.0 pins, which is outside the HIGH floor.
