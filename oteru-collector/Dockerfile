# Oteru Collector — custom distro via OCB.
# OTERU_PII_HMAC_KEY is never an ARG, ENV, or image layer. Inject it only at
# container runtime (compose: environment from .env; collector config:
# ${env:OTERU_PII_HMAC_KEY}).

# golang:1.26.10-bookworm — OTTL generics in contrib v0.155.0 fail to link
# on the Go 1.25 linker (relocation target not defined). Duto's working
# toolchain is Go 1.26.x / GOTOOLCHAIN=go1.26.10. Index digest of the
# 1.26.10-bookworm tag (not a floating latest).
FROM golang:1.26.10-bookworm@sha256:ee3901c61e423732605a51f1b8157c849ec01194ea0a274213b5202b2c11e7bf AS builder

WORKDIR /src
ENV CGO_ENABLED=0 \
    GOPROXY=https://proxy.golang.org,direct \
    GOBIN=/usr/local/bin \
    GOTOOLCHAIN=go1.26.10

# OCB (collector builder) at the same version as the distro.
RUN go install go.opentelemetry.io/collector/cmd/builder@v0.155.0 \
    && mv /usr/local/bin/builder /usr/local/bin/ocb

COPY . /tmp/context
RUN cp /tmp/context/builder-config.yaml /src/builder-config.yaml \
    && if [ -d /tmp/context/hmacconverter ]; then \
         cp -a /tmp/context/hmacconverter /src/hmacconverter; \
       fi \
    && rm -rf /tmp/context \
    && if [ ! -f /src/hmacconverter/go.mod ]; then \
         mkdir -p /src/hmacconverter \
         && printf 'module github.com/luanmorenommaciel/oteru/oteru-collector/hmacconverter\n\ngo 1.26.0\n' \
              > /src/hmacconverter/go.mod; \
       fi \
    && ocb --config=/src/builder-config.yaml \
    && test -x /src/_build/otelcol-oteru

# gcr.io/distroless/static-debian12:nonroot
FROM gcr.io/distroless/static-debian12:nonroot@sha256:afa5c872c891853ca7fcf1f12c3edb23f7eeef36189728842dd51042ff57f7ab

COPY --from=builder --chown=nonroot:nonroot /src/_build/otelcol-oteru /otelcol-oteru

USER nonroot

ENTRYPOINT ["/otelcol-oteru"]
CMD ["--config=/etc/oteru-collector-config.yml"]
