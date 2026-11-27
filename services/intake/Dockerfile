# intake: a static Go binary on distroless (no shell, no package manager, runs as non-root).
# Build from this folder: docker build -t intake .  (CI: .github/workflows/images.yml)

FROM golang:1.27.4-alpine AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/intake ./cmd/intake

# Pinned by digest: the nonroot tag moves with every Debian security update.
FROM gcr.io/distroless/static-debian12:nonroot@sha256:afa5c872c891853ca7fcf1f12c3edb23f7eeef36189728842dd51042ff57f7ab
COPY --from=build /out/intake /intake
USER nonroot:nonroot
EXPOSE 8080 9090
ENTRYPOINT ["/intake"]
