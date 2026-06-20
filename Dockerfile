# Rust builder
FROM rust:1-trixie AS rust-builder

WORKDIR /src/derivation-helper

COPY derivation-helper/Cargo.toml ./
COPY derivation-helper/src ./src

RUN rustc --version \
    && cargo --version \
    && cargo build --release

# Python runner
FROM python:3.12-slim-trixie

# Pull in the uv binary from its official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    PATH="/app/.venv/bin:$PATH"

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tor \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --system --uid 10001 --home-dir /nonexistent --shell /usr/sbin/nologin wwg \
    && mkdir -p /app/data \
    && chown -R wwg:wwg /app/data

COPY --from=rust-builder --chown=root:root --chmod=0755 \
    /src/derivation-helper/target/release/wwg-derive \
    /app/wwg-derive

# 1) Dependencies only — this layer is cached unless uv.lock changes
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

# 2) Now the project itself
COPY README.md ./
COPY wallet_watchguard ./wallet_watchguard
RUN uv sync --locked --no-dev

USER wwg

CMD ["wwg", "run"]

LABEL org.opencontainers.image.description="Bitcoin Wallet Watchguard: Talk to your Bitcoin node via Electrum and Ntfy"
LABEL org.opencontainers.image.source=https://github.com/xannythepleb/bitcoin-wallet-watchguard