FROM rust:1-trixie AS rust-builder

WORKDIR /src/derivation-helper

COPY derivation-helper/Cargo.toml ./
COPY derivation-helper/src ./src

RUN rustc --version \
    && cargo --version \
    && cargo build --release


FROM python:3.12-slim-trixie

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tor \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --system --uid 10001 --home-dir /nonexistent --shell /usr/sbin/nologin wwg \
    && mkdir -p /app/data \
    && chown -R wwg:wwg /app/data

COPY --from=rust-builder --chown=root:root --chmod=0755 \
    /src/derivation-helper/target/release/wwg-derive \
    /app/wwg-derive

COPY pyproject.toml README.md ./
COPY wallet_watchguard ./wallet_watchguard

RUN pip install --no-cache-dir .

USER wwg

CMD ["wwg", "run"]

LABEL org.opencontainers.image.description="Bitcoin Wallet Watchguard: Talk to your Bitcoin node via Electrum and Ntfy"
LABEL org.opencontainers.image.source=https://github.com/xannythepleb/bitcoin-wallet-watchguard