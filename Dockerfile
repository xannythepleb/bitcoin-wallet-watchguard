# syntax=docker/dockerfile:1.7
ARG PYTHON_IMAGE=python:3.12-slim-trixie

# Shared Rust source stage
FROM rust:1-trixie AS rust-source

WORKDIR /src/derivation-helper

COPY derivation-helper/Cargo.toml ./
COPY derivation-helper/src ./src

# Derivation helper builder. Slim and full images both need this binary.
FROM rust-source AS rust-derive-builder

# Strip symbols at compile time to reduce the helper binary size
RUN rustc --version \
    && cargo --version \
    && RUSTFLAGS="-C strip=symbols" cargo build --release --bin wwg-derive

# Nostr helper builder. Only the full/default image copies this binary.
FROM rust-source AS rust-nostr-builder

# Strip symbols at compile time to reduce the helper binary size
RUN rustc --version \
    && cargo --version \
    && RUSTFLAGS="-C strip=symbols" cargo build --release --bin wwg-nostr

# Python dependency builder
FROM ${PYTHON_IMAGE} AS python-builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    PATH="/app/.venv/bin:$PATH"

# Dependencies only
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

# Now install the actual project into the venv.
COPY wallet_watchguard ./wallet_watchguard
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable \
    && find /app/.venv -type d -name "__pycache__" -prune -exec rm -rf {} + \
    && find /app/.venv -type f -name "*.pyc" -delete

# Shared runtime base
FROM ${PYTHON_IMAGE} AS runtime-base

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# ca-certificates is needed for TLS Electrum connections
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Remove runtime pip/setuptools/wheel, avoids shipping package manager the user doesn't need
RUN python -m pip uninstall -y pip setuptools wheel || true \
    && rm -rf /usr/local/bin/pip* \
    && rm -rf /usr/local/lib/python*/site-packages/pip* \
    && rm -rf /usr/local/lib/python*/site-packages/setuptools* \
    && rm -rf /usr/local/lib/python*/site-packages/wheel* \
    && rm -rf /usr/local/lib/python*/ensurepip

RUN useradd --system --uid 10001 --home-dir /nonexistent --shell /usr/sbin/nologin wwg \
    && mkdir -p /app/data \
    && chown -R wwg:wwg /app/data

COPY --from=python-builder --chown=root:root /app/.venv /app/.venv
COPY --from=rust-derive-builder --chown=root:root --chmod=0755 \
    /src/derivation-helper/target/release/wwg-derive \
    /app/wwg-derive

# Slim runtime: no Tor or Nostr helper bundled
FROM runtime-base AS runtime-slim

USER wwg

HEALTHCHECK --interval=5m --timeout=30s --start-period=90s --retries=3 \
    CMD ["wwg", "healthcheck"]

CMD ["wwg", "run"]

# Full/default runtime: Tor and Nostr helper bundled
FROM runtime-base AS runtime-tor

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends tor \
    && rm -rf /var/lib/apt/lists/*

COPY --from=rust-nostr-builder --chown=root:root --chmod=0755 \
    /src/derivation-helper/target/release/wwg-nostr \
    /app/wwg-nostr

USER wwg

HEALTHCHECK --interval=5m --timeout=30s --start-period=90s --retries=3 \
    CMD ["wwg", "healthcheck"]

CMD ["wwg", "run"]

LABEL org.opencontainers.image.description="Bitcoin Wallet Watchguard: Talk to your Bitcoin node via Electrum, Ntfy, and Nostr"
LABEL org.opencontainers.image.source=https://github.com/xannythepleb/bitcoin-wallet-watchguard
