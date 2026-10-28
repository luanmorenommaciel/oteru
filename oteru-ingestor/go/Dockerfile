# oteru-ingestor, Go implementation (#25). Build context: oteru-ingestor/
#   docker build -f go/Dockerfile -t oteru-ingestor-go oteru-ingestor
FROM golang:1.26 AS build
WORKDIR /src
COPY go/go.mod go/go.sum ./
RUN go mod download
COPY go/main.go ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /ingestor .

FROM gcr.io/distroless/static-debian12:nonroot
WORKDIR /app
COPY --from=build /ingestor /app/ingestor
COPY schema.sql /app/schema.sql
ENV PORT=4318 INGEST_DB=ingest_go SCHEMA_PATH=/app/schema.sql
EXPOSE 4318
ENTRYPOINT ["/app/ingestor"]
