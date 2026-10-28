# oteru-ingestor, Rust implementation (#26). Build context: oteru-ingestor/
#   docker build -f rust/Dockerfile -t oteru-ingestor-rust oteru-ingestor
FROM rust:1 AS build
WORKDIR /src
COPY rust/Cargo.toml rust/Cargo.lock ./
# Cache dependencies: build once against a stub main, then the real source.
RUN mkdir src && echo 'fn main() {}' > src/main.rs && cargo build --release --locked && rm -rf src
COPY rust/src ./src
RUN touch src/main.rs && cargo build --release --locked

FROM gcr.io/distroless/cc-debian12:nonroot
WORKDIR /app
COPY --from=build /src/target/release/oteru-ingestor /app/ingestor
COPY schema.sql /app/schema.sql
ENV PORT=4318 INGEST_DB=ingest_rust SCHEMA_PATH=/app/schema.sql
EXPOSE 4318
ENTRYPOINT ["/app/ingestor"]
