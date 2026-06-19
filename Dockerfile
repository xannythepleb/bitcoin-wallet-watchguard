FROM rust:1-bookworm AS rust-builder
WORKDIR /src/derivation-helper
COPY derivation-helper/Cargo.toml derivation-helper/Cargo.lock* ./
COPY derivation-helper/src ./src
RUN rustc --version && cargo --version && cargo build --release

FROM python:3.12-slim-trixie
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tor \
    && rm -rf /var/lib/apt/lists/*

COPY --from=rust-builder /src/derivation-helper/target/release/wwg-derive /app/wwg-derive
COPY pyproject.toml README.md ./
COPY wallet_watchguard ./wallet_watchguard
RUN pip install --no-cache-dir .

CMD ["wwg", "run"]

LABEL org.opencontainers.image.description="Bitcoin Wallet Watchguard: Talk to your Bitcoin node via Electrum and Ntfy"
LABEL org.opencontainers.image.source=https://github.com/xannythepleb/bitcoin-wallet-watchguard