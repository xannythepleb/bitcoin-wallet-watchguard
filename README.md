# Bitcoin Wallet Watchguard

Bitcoin Wallet Watchguard, or Wallet Watchguard for short, is a small self-hosted watcher for Bitcoin extended public keys.

It derives addresses from an encrypted account xpub, subscribes to matching Electrum/Fulcrum scripthashes, stores state in SQLite, deduplicates wallet events, and publishes notifications to a private ntfy topic.

The intended setup is:

```text
Your Bitcoin node + Fulcrum -> Wallet Watchguard -> Your self-hosted ntfy instance -> Your phone
```

## Status

This is a v0.1 starter scaffold. It gives you the core shape:

- Python CLI and daemon
- raw async Electrum JSON-RPC client
- SQLite schema
- ntfy publisher
- passphrase-based xpub encryption at rest using Argon2id + SecretBox
- Rust derivation helper for Taproot, Native SegWit, Nested SegWit, and Legacy address generation
- Docker Compose deployment skeleton

## Privacy model

Wallet Watchguard is designed for self-hosting.

For best privacy and security:

- use your own Bitcoin node
- use your own Fulcrum server
- use your own ntfy instance
- use a strong Wallet Watchguard passphrase
- do not use public Electrum servers with real xpub-derived wallets unless you accept the privacy leak

An xpub cannot spend funds, but it can reveal wallet activity. Treat it as sensitive.

## Xpub encryption model

Wallet Watchguard stores encrypted xpubs in `config.yaml`.

The app no longer uses an X25519 private key at launch. Instead, it prompts for a passphrase and derives an encryption key using Argon2id. The encrypted xpub itself acts as the passphrase check: if the passphrase is wrong, decryption fails.

The config stores KDF metadata such as salt and Argon2id limits, but it does not store the passphrase or a separate passphrase hash.

## Basic usage

Build the Rust derivation helper:

```bash
cd derivation-helper
cargo build --release
cp target/release/wwg-derive ../wwg-derive
```

Install the Python package locally:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Create a config interactively:

```bash
wwg init --config config.yaml
```

Run the watcher:

```bash
wwg run --config config.yaml
```

You will be prompted for the Wallet Watchguard encryption passphrase at startup unless you provide it via:

```bash
export WWG_PASSPHRASE="your long passphrase here"
```

## Docker Compose

Build first:

```bash
docker compose build
```

Run:

```bash
WWG_PASSPHRASE="your long passphrase here" docker compose up -d
```

Or use a local `.env` file that is not committed to git:

```env
WWG_PASSPHRASE=your long passphrase here
```

The Compose file intentionally fails fast if `WWG_PASSPHRASE` is missing or blank.

## Electrum/Fulcrum URLs

- unencrypted TCP usually uses port `50001`
- TLS usually uses port `50002`
- onion services need Tor routing or a SOCKS5 proxy such as `127.0.0.1:9050`

## ntfy setup

Use a long random topic name and restrict permissions on your ntfy server.

Example topic:

```text
wallet-watchguard-c3f7b6e63b11405fb911
```

Example publish URL:

```text
https://ntfy.example.com/wallet-watchguard-c3f7b6e63b11405fb911
```

Use a token or HTTP basic auth if your ntfy instance requires it.

## Notes on derivation

The xpub should normally be an account-level xpub, for example:

- Taproot/BIP86: `m/86'/0'/0'`
- Native SegWit/BIP84: `m/84'/0'/0'`
- Nested SegWit/BIP49: `m/49'/0'/0'`
- Legacy/BIP44: `m/44'/0'/0'`

Wallet Watchguard then derives non-hardened receive/change paths from that account xpub:

```text
0/*  receive
1/*  change
```

Hardened derivation cannot be performed from an xpub.

## Docker build note: Rust edition 2024

The derivation helper should be built with Rust 1.85 or newer. Rust 1.85 stabilised the 2024 edition; older Cargo versions such as 1.82 can fail when resolving newer dependencies with an `edition2024` error.

The Dockerfile therefore uses `rust:1-bookworm` for the build stage. For local builds, the helper includes `derivation-helper/rust-toolchain.toml` pinned to Rust 1.85.0 as the minimum known-good toolchain.
