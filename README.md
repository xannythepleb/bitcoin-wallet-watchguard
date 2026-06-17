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
- passphrase-based xpub and ntfy credential encryption at rest using Argon2id + SecretBox
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

## Encryption model

Wallet Watchguard stores sensitive values encrypted in `config.yaml`.

Currently encrypted values include:

- wallet xpubs
- ntfy access tokens, if token auth is used
- ntfy usernames and passwords, if basic auth is used

The app prompts for a passphrase and derives encryption keys using Argon2id. The encrypted value itself acts as the passphrase check: if the passphrase is wrong, decryption fails.

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

Run the setup wizard inside Docker:

```bash
docker compose run --rm wallet-watchguard wwg init --config /data/config.yaml
```

Then run the daemon:

```bash
WWG_PASSPHRASE="your long passphrase here" docker compose up -d
```

Or use a local `.env` file that is not committed to git:

```env
WWG_PASSPHRASE=your long passphrase here
```

## Electrum/Fulcrum URLs

- unencrypted TCP usually uses port `50001`
- TLS usually uses port `50002`
- onion services need Tor routing or a SOCKS5 proxy such as `127.0.0.1:9050`

## ntfy setup

Use a long random topic name and restrict permissions on your ntfy server.

For a locked-down Start9/StartOS-style ntfy setup, the recommended pattern is:

- create a dedicated topic for Wallet Watchguard, preferably with the wizard-generated random name
- give Wallet Watchguard a dedicated ntfy token/user with write permission to that topic
- give your phone/subscriber user read permission to that topic
- avoid broad anonymous publish permissions such as `up*` or unrestricted wildcard topics

The setup wizard supports three ntfy auth modes:

- `token` - recommended where supported; stores an encrypted bearer/access token
- `basic` - stores encrypted username and password
- `none` - only sensible for LAN-only testing or a tightly controlled private setup

Example topic:

```text
wallet-watchguard-c3f7b6e63b11405fb911
```

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

## Updating an existing config

`wwg init` is additive. If the config file already exists, the wizard asks whether you want to update part of the existing config, reset it, or exit without changes.

```bash
wwg init --config config.yaml
```

You can also jump directly to a specific section:

```bash
wwg init --config config.yaml --add ntfy
wwg init --config config.yaml --add electrum
wwg init --config config.yaml --add wallet
wwg init --config config.yaml --add app
```

With Docker Compose:

```bash
docker compose run --rm wallet-watchguard wwg init --config /data/config.yaml --add ntfy
```

Reset the config deliberately with:

```bash
wwg init --config config.yaml --reset
```

## Start9 / StartOS ntfy publisher setup

For Start9 or StartOS ntfy instances, use the ntfy service UI to **Provision Publisher**.

Recommended setup:

1. Use a reference name such as `wallet-watchguard`.
2. Use the topic name already stored in your config if one exists; the wizard prints it for you.
3. Give Wallet Watchguard publish/write access to that topic.
4. Give your phone or normal ntfy user read access to that topic.
5. Avoid broad anonymous publish permissions.

The Start9 dialogue provides:

```text
publishUrl
token
topic
username
```

When the wizard asks whether you are using Start9/StartOS provision-publisher details, answer yes and paste those values in. Wallet Watchguard stores the token encrypted with the same passphrase used for wallet xpubs. The Start9 username is stored encrypted as publisher metadata; the token is what Wallet Watchguard uses to publish notifications.
