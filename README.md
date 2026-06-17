# Bitcoin Wallet Watchguard

Bitcoin Wallet Watchguard allows you to get notified of any transaction to or from your wallets by using the xpub. It then lets you talk to your wallet if you enable Conversation Mode, so you can query all your addresses, transactions, and more from your phone.

All of this is made possible by [ntfy](https://github.com/binwiederhier/ntfy), an open source self-hostable project supported by both Umbrel and Start9. Therefore, Wallet Watchguard does all of this while maintaining sovereignty and privacy so long as you configure it to use your own node and host ntfy yourself.

Your xpub is only ever stored locally on the machine you install Wallet Watchguard on. It is encrypted at rest using libsodium and Argon2id.

## First-time setup

Local:

```bash
wwg init --config config.yaml
wwg run --config config.yaml
```

Docker Compose:

```bash
mkdir -p data
docker compose build
docker compose run --rm wallet-watchguard wwg init --config /data/config.yaml
WWG_PASSPHRASE='your passphrase here' docker compose up -d
```

If the config already exists, `wwg init` asks whether to update part of the config, reset it, or exit.

You can jump directly to a section:

```bash
wwg init --config /data/config.yaml --add ntfy
wwg init --config /data/config.yaml --add electrum
wwg init --config /data/config.yaml --add wallet
wwg init --config /data/config.yaml --add mempool
wwg init --config /data/config.yaml --add app
wwg init --config /data/config.yaml --reset
```

## ntfy test command

After configuring ntfy, send a test notification:

```bash
wwg test-ntfy --config /data/config.yaml
```

With Docker Compose:

```bash
WWG_PASSPHRASE='your passphrase here' docker compose run --rm wallet-watchguard wwg test-ntfy --config /data/config.yaml
```

## Start9 / StartOS ntfy setup

If you use Start9/StartOS ntfy, run:

```bash
wwg init --config /data/config.yaml --add ntfy
```

Choose the Start9/StartOS provision-publisher path.

In the Start9 ntfy UI, use **Provision Publisher**. For the reference name, use something like `wallet-watchguard`. For the topic name, use the topic shown by the wizard, especially if one is already stored in your config.

Start9 will show:

```text
publishUrl
token
topic
username
```

Paste those into the wizard. Wallet Watchguard stores the token encrypted at rest using the same passphrase used for your xpub.

Recommended access model:

```text
Wallet Watchguard publisher/token -> write permission
Phone/user account                -> read permission
Anonymous access                  -> disabled unless deliberately needed
```

## Start9 / self-signed TLS certificates

Start9/StartOS and other local-node systems often use private or self-signed TLS certificates. If you see an error like:

```text
[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed certificate in certificate chain
```

Run the relevant setup section and disable TLS certificate verification for that local service:

```bash
wwg init --config /data/config.yaml --add electrum
wwg init --config /data/config.yaml --add ntfy
wwg init --config /data/config.yaml --add mempool
```

TLS encryption still remains enabled; only public CA/hostname verification is skipped for that local self-hosted service.

For existing configs without `electrum.tls_verify`, Wallet Watchguard automatically relaxes Electrum TLS verification for localhost, private IPs, `.local`, `.lan`, `.onion`, and similar local targets.

## Address and balance listing

List receive addresses and balances. If more than one wallet is configured, Wallet Watchguard now asks which wallet you want to view:

```bash
wwg addresses --config /data/config.yaml --limit 20
```

Show every configured wallet without prompting:

```bash
wwg addresses --config /data/config.yaml --all --limit 20
```

Limit to one wallet by exact name or unique partial name:

```bash
wwg addresses --config /data/config.yaml --wallet "Main Taproot wallet"
```

Limit to one wallet by its 1-based index in config:

```bash
wwg addresses --config /data/config.yaml --wallet-index 2
```

Include change addresses:

```bash
wwg addresses --config /data/config.yaml --include-change --limit 20
```

Only show non-zero addresses:

```bash
wwg addresses --config /data/config.yaml --only-nonzero --include-change --limit 100
```

Only show addresses that already have Electrum history:

```bash
wwg addresses --config /data/config.yaml --only-used --include-change --limit 100
```

The address table includes a `used`/`unused` status column. Unused receive addresses are shown by default, even when their balance is zero.

## Conversation Mode: Talk to your wallet anywhere

Conversation Mode lets you query Wallet Watchguard remotely through ntfy. You can keep an eye on even your hardware cold storage wherever you are via 100% self-hosted infrastructure. No third party middleman if you configure it correctly with your own node and your own ntfy instance. You can run both of these on your own physical hardware using Start9 or Umbrel for true sovereignty.

I specifically added this feature because I had a hard time finding a reliable watch only wallet that could work with any hardware wallet and allowed you to use your own node on the backend. So I made one. And because ntfy runs on Android, iOS, and in the browser, it is truly universal.

It is off by default and checks are in place to ensure it is only enabled on secure sessions (password protected, no public read/write access).

Enable it in config:

```bash
wwg init --config /data/config.yaml --add conversation
```

Or enable it for a single daemon run:

```bash
WWG_PASSPHRASE='your passphrase here' docker compose run --rm wallet-watchguard \
  wwg run --config /data/config.yaml --conversation
```

Conversation mode will only start if the configured ntfy topic passes runtime protection checks:

```text
configured token/basic credentials can read the topic
configured token/basic credentials can publish to the topic
anonymous read is denied
anonymous write is denied
```

If the topic is public, or anonymous read/write is allowed, Wallet Watchguard will continue normal wallet monitoring but will not honour conversation mode.

Example ntfy commands:

```text
wwg help
wwg wallets
wwg next address
wwg next 3
wwg addresses --wallet-index 1 --limit 5
wwg addresses --wallet "Main Taproot wallet" --include-change --only-used
wwg balance
```

The default command prefix is `wwg`. The prefix avoids accidental replies to unrelated messages on the same topic.

Conversation mode performs an anonymous write probe on startup because ntfy's normal client APIs can publish, subscribe and authenticate, but do not expose a harmless per-topic ACL inspection endpoint for ordinary clients. The probe uses `Cache: no`, `Firebase: no` and minimum priority. If that anonymous probe succeeds, conversation mode is refused.

## Optional Mempool API enrichment

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
wwg init --config /data/config.yaml --add mempool
```

Use a local/self-hosted Mempool API where possible, for example:

```yaml
mempool:
  enabled: true
  base_url: https://mempool.local/api
  tls_verify: false
  timeout_seconds: 15
  enrich_notifications: true
```

## Docker Compose logs

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

## Security notes

The xpub and ntfy credentials are encrypted at rest using the Wallet Watchguard passphrase. You need this passphrase whenever the daemon starts.

An xpub cannot spend funds, but it can reveal wallet history and future addresses. Run Wallet Watchguard on infrastructure you control, and prefer your own Bitcoin node, Fulcrum and ntfy instance.
