# Bitcoin Wallet Watchguard

**Say hello to a local encrypted watch only Bitcoin wallet you can talk to anywhere in the world.**

Bitcoin Wallet Watchguard allows you to get notified of any transaction to or from your wallets by using the xpub. It then lets you talk to your wallet if you enable Conversation Mode, so you can query all your addresses, transactions, and more from your phone.

All of this is made possible by [ntfy](https://github.com/binwiederhier/ntfy), an open source self-hostable project supported by both Umbrel and Start9. Therefore, Wallet Watchguard does all of this while maintaining sovereignty and privacy so long as you configure it to use your own node and host ntfy yourself.

Your xpub is only ever stored locally on the machine you install Wallet Watchguard on. It is encrypted at rest using XSalsa20-Poly1305 and Argon2id, provided by the battle tested libsodium library.

## Roadmap

The current iteration of this tool is only scratching the surface of its potential. At its core, this lets you use Ntfy to talk to your node from your phone anywhere in the world without any third parties or port forwarding required. It's like a Telegram bot but fully self-hosted.

The existing view only wallet features via command line syntax are useful but I'm already working on more big updates to this because it fills a genuine gap in my own setup. This will get a lot cooler very quickly.

## First Time Setup

### Local

```bash
wwg init
wwg run
```

### Docker Compose

```bash
mkdir -p data
docker compose build
docker compose run --rm wallet-watchguard wwg init
WWG_PASSPHRASE='your passphrase here' docker compose up -d
```

If the config already exists, `wwg init` asks whether to update part of the config, reset it, or exit.

You can jump directly to a section:

```bash
wwg init --add ntfy
wwg init --add electrum
wwg init --add wallet
wwg init --add mempool
wwg init --add app
wwg init --reset
```

### Docker Images

Wallet Watchguard publishes Docker images to GitHub automatically. Images are built and pushed when a new version is released.

For example:

```bash
docker pull ghcr.io/xannythepleb/bitcoin-wallet-watchguard:latest
```

Or:

```bash
docker run ghcr.io/xannythepleb/bitcoin-wallet-watchguard:latest wwg init
```

## Conversation Mode Setup

The standard method of generating ntfy access tokens already works perfectly for Conversation Mode:

```bash
ntfy token add wallet-watchguard
```

You can also create a new user and use it to generate an access token through the web UI under the account section.

For Start9/StartOS, **Provision Publisher is not enough for Conversation Mode**. Provision Publisher creates a write-only token, which is ideal for normal alerts but cannot subscribe/read commands.

Use the standard ntfy access token process instead:

```text
1. In the Start9 ntfy service, create a regular user, e.g. wallet-watchguard.
2. Grant that user read-write access to the Wallet Watchguard topic.
3. Log into the ntfy web UI as that user.
4. Create an access token manually in the ntfy web UI.
5. Run: wwg init --add ntfy
6. Choose token auth and paste that access token.
7. Keep anonymous read and anonymous write denied.
```

TL;DR: Make an access token through ntfy instead of the StartOS UI. Use this token to configure ntfy for WWG and Conversation Mode will work.

See [the ntfy docs](https://docs.ntfy.sh/config/#access-tokens) for more on how their access tokens work.

You can use the same topic as your normal alerts. If your existing config already stores a topic name, the wizard will remind you of it so you can grant access to the correct topic.

The expected Conversation Mode protection check is:

```text
authenticated read: ok
authenticated write: ok
anonymous read: blocked HTTP 401/403
anonymous write: blocked HTTP 401/403
```

If you see:

```text
authenticated read: failed HTTP 403
authenticated write: ok
```

This means Wallet Watchguard has a write-only credential. Normal notifications will work, but Conversation Mode cannot receive commands. On Start9 this usually means you are still using a Provision Publisher token rather than a token created by a regular read-write user.

### Start9 / StartOS Ntfy Setup

If you use Start9/StartOS ntfy, run:

```bash
wwg init --add ntfy
```

Choose the Start9/StartOS provision publisher path.

In the Start9 ntfy UI, use **Provision Publisher if you only want notifications and do not want Conversation Mode.** If you want Conversation Mode to work, follow the [Conversation Mode Setup][#conversation-mode-setup] instructions above to generate an access token through ntfy.

Note: you can always generate a new token and configure WWG to use it if you change your mind later.

For the reference name, use something like `wallet-watchguard`. For the topic name, use the topic shown by the wizard, especially if one is already stored in your config.

Start9 will show:

```text
publishUrl
token
topic
username
```

Paste those into the wizard. Wallet Watchguard stores the token encrypted at rest using the same passphrase used for your xpub.

Note: `publishUrl` can sometimes print something useless like `ntfy.start`. If it does, ignore it and put the actual base domain or IP address you set for your ntfy instance. If you aren't using StartTunnel, make sure to also include the port like so:

`https://192.168.67.69:4200`

### Start9 / Umbrel Self-Signed TLS Certificates

StartOS, Umbrel, and other local node systems often use private or self-signed TLS certificates. If you see an error like:

```text
[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed certificate in certificate chain
```

Run the relevant setup section and disable TLS certificate verification for that local service:

```bash
wwg init --add electrum
wwg init --add ntfy
wwg init --add mempool
```

TLS encryption still remains enabled; only public CA/hostname verification is skipped for that local self-hosted service.

For existing configs without `electrum.tls_verify`, Wallet Watchguard automatically relaxes Electrum TLS verification for localhost, private IPs, `.local`, `.lan`, `.onion`, and similar local targets.

## Ntfy Test Command

After configuring ntfy, send a test notification:

```bash
wwg test-ntfy
```

With Docker Compose:

```bash
WWG_PASSPHRASE='your passphrase here' docker compose run --rm wallet-watchguard wwg test-ntfy
```

You can also store your `WWG_PASSPHRASE` in your `.env` for convenience, with obvious security tradeoffs.

## Address and Balance Listing

List receive addresses and balances. If more than one wallet is configured, Wallet Watchguard now asks which wallet you want to view:

```bash
wwg addresses --limit 20
```

Show every configured wallet without prompting:

```bash
wwg addresses --all --limit 20
```

Limit to one wallet by exact name or unique partial name:

```bash
wwg addresses --wallet "Main Taproot wallet"
```

Limit to one wallet by its 1-based index in config:

```bash
wwg addresses --wallet-index 2
```

Include change addresses:

```bash
wwg addresses --include-change --limit 20
```

Only show non-zero addresses:

```bash
wwg addresses --only-nonzero --include-change --limit 100
```

Only show addresses that already have Electrum history:

```bash
wwg addresses --only-used --include-change --limit 100
```

The address table includes a `used`/`unused` status column. Unused receive addresses are shown by default, even when their balance is zero.

## Conversation Mode: Talk to Your Wallet Anywhere

Conversation Mode lets you query Wallet Watchguard remotely through ntfy. You can keep an eye on even your hardware cold storage wherever you are via 100% self-hosted infrastructure. No third party middleman if you configure it correctly with your own node and your own ntfy instance. You can run both of these on your own physical hardware using Start9 or Umbrel for true sovereignty.

I specifically added this feature because I had a hard time finding a reliable watch only wallet that could work with any hardware wallet and allowed you to use your own node on the backend. So I made one. And because ntfy runs on Android, iOS, and in the browser, it is truly universal.

It is off by default and checks are in place to ensure it is only enabled on secure sessions (password protected, no public read/write access).

Enable it in config:

```bash
wwg init --add conversation
```

Or enable it for a single daemon run:

```bash
WWG_PASSPHRASE='your passphrase here' docker compose run --rm wallet-watchguard \
  wwg run --conversation
```

Conversation Mode will only start if the configured ntfy topic passes runtime protection checks:

```text
configured token/basic credentials can read the topic
configured token/basic credentials can publish to the topic
anonymous read is denied
anonymous write is denied
```

If the topic is public, or anonymous read/write is allowed, Wallet Watchguard will continue normal wallet monitoring but will not honour Conversation Mode.

Conversation Mode performs an anonymous write probe on startup because ntfy's normal client APIs can publish, subscribe and authenticate, but do not expose a harmless per-topic ACL inspection endpoint for ordinary clients. The probe uses `Cache: no`, `Firebase: no` and minimum priority. If that anonymous probe succeeds, conversation Mode is refused.

### Conversation Commands

Example ntfy commands:

```text
wwg help
wwg status
wwg wallets
wwg next address
wwg next 3
wwg addresses --wallet-index 1 --limit 5
wwg addresses --wallet "Main Taproot wallet" --include-change --only-used
wwg balance
wwg fees
```

The default command prefix is `wwg`. The prefix avoids accidental replies to unrelated messages on the same topic.

## Optional Mempool API Integration

Fulcrum/Electrum remains the source of truth for wallet activity detection.

Mempool support is optional and only enriches notifications with decoded transaction data such as:

```text
amount
fee
vsize
fee rate
received/sent/self-transfer classification
```

Enable it with:

```bash
wwg init --add mempool
```

Use a local/self-hosted Mempool API where possible, for example:

```yaml
mempool:
  enabled: true
  base_url: https://mempool.local:1234/api
  tls_verify: false
  timeout_seconds: 15
  enrich_notifications: true
```

Test it with:

```bash
wwg fees
```

## Status Command

`wwg status` prints the same non-secret summary format used at startup. When run as a one-shot CLI command it cannot know the daemon's live subscription count, but it still shows the effective config, Electrum/Fulcrum target, Tor Upstream setting, ntfy target, Mempool setting, Conversation Mode setting, and configured wallets.

Conversation Mode also understands:

```text
wwg status
```

That ntfy response is generated by the running daemon, so it includes live runtime values such as subscribed script count and the startup Tor probe result.

## Docker Compose Logs

When running successfully, the daemon prints a non-secret startup summary to the logs:

```text
Bitcoin Wallet Watchguard is running
Config: /data/config.yaml
Database: /data/watchguard.sqlite3
Electrum/Fulcrum: ...
ntfy: ...
Mempool API: enabled/disabled
Wallets: ...
Useful commands: ...
```

View logs:

```bash
docker compose logs -f wallet-watchguard
```

## Tech Stack

Bitcoin Wallet Watchguard uses:

* Python for orchestration, CLI, async networking, config, SQLite, ntfy, and Mempool integration
* Rust for exact Bitcoin address/script derivation
* Fulcrum/Electrum for wallet activity subscriptions
* SOCKS proxy support including an integrated Tor package in the Dockerfile for upstream connectivity to Tor only Bitcoin nodes
* SQLite for local deduplication and state
* Ntfy for self-hosted notifications and commands (effectively a self-hosted bot for your node)
* Optional Mempool integration for richer transaction and fee data
* Argon2id for password hashing
* XSalsa20-Poly1305 for encrypted-at-rest xpubs and credentials (via PyNaCl/libsodium)
* Docker Compose for reliable 24/7 deployment

## Security Notes

The xpub and ntfy credentials are encrypted at rest using the Wallet Watchguard passphrase. You need this passphrase whenever the daemon starts.

An xpub cannot spend funds, but it can reveal wallet history and future addresses. Run Wallet Watchguard on infrastructure you control, including your own Bitcoin node, Fulcrum, and ntfy instance for the highest level of privacy.