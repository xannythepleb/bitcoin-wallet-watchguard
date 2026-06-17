FROM rust:1-bookworm AS rust-builder
WORKDIR /src/derivation-helper
COPY derivation-helper/Cargo.toml derivation-helper/Cargo.lock* ./
COPY derivation-helper/src ./src
RUN rustc --version && cargo --version && cargo build --release

FROM python:3.12-slim-bookworm
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=rust-builder /src/derivation-helper/target/release/wwg-derive /app/wwg-derive
COPY pyproject.toml README.md ./
COPY wallet_watchguard ./wallet_watchguard
RUN pip install --no-cache-dir .

VOLUME ["/data"]
CMD ["wwg", "run", "--config", "/data/config.yaml"]
