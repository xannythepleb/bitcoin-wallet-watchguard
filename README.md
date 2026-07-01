# Bitcoin Wallet Watchguard

**Say hello to a local encrypted watch only Bitcoin wallet you can talk to anywhere in the world.**

Bitcoin Wallet Watchguard allows you to get notified of any transaction to or from your wallets by using the xpub. It then lets you talk to your wallet if you enable Conversation Mode, so you can query all your addresses, transactions, and more from your phone.

All of this is made possible by [ntfy](https://github.com/binwiederhier/ntfy), an open source self-hostable project supported by both Umbrel and Start9. Therefore, Wallet Watchguard does all of this while maintaining sovereignty and privacy so long as you configure it to use your own node and host ntfy yourself.

Your xpub is only ever stored locally on the machine you install Wallet Watchguard on. It is encrypted at rest using XSalsa20-Poly1305 and protected by a passphrase hashed with Argon2id, both provided by the battle tested libsodium cryptography library.

## Roadmap

The current iteration of this tool is only scratching the surface of its potential. At its core, this lets you use Ntfy to talk to your node from your phone anywhere in the world without any third parties or port forwarding required. It's like a Telegram bot but fully self-hosted.

The existing view only wallet features via command line syntax are useful but I'm already working on more big updates to this because it fills a genuine gap in my own setup. This will get a lot cooler very quickly.

## First Time Setup

### Docker Compose

Docker Compose is the recommended and officially supported installation method:

```bash
mkdir -p bitcoin-wallet-watchguard && cd bitcoin-wallet-watchguard

curl -fsSL -o docker-compose.yml \
  https://raw.githubusercontent.com/xannythepleb/bitcoin-wallet-watchguard/main/docker-compose.yml

docker compose pull
docker compose run --rm wallet-watchguard wwg init
docker compose up -d
```

Alternatively, if you prefer `wget`:

```bash
wget -O docker-compose.yml https://raw.githubusercontent.com/xannythepleb/bitcoin-wallet-watchguard/refs/heads/main/docker-compose.yml
```

### One liner

Paste this in your terminal to get up and running:

```bash
mkdir -p bitcoin-wallet-watchguard && cd bitcoin-wallet-watchguard && curl -fsSL -o docker-compose.yml https://raw.githubusercontent.com/xannythepleb/bitcoin-wallet-watchguard/main/docker-compose.yml && docker compose pull && docker compose run --rm wallet-watchguard wwg init && docker compose up -d
```

To launch in the future:

```bash
WWG_PASSPHRASE='your passphrase here' docker compose up -d
```

You can choose to store your `WWG_PASSPHRASE` in your `.env` for convenience, especially seamless restarts, with obvious security tradeoffs.

Don't stress if you got something wrong during the initial configuration or need to change something later. You can jump directly to an `init` section to alter your configuration whenever you need:

```bash
wwg init --add ntfy
wwg init --add electrum
wwg init --add wallet
wwg init --add mempool
wwg init --add tor
wwg init --add app
wwg init --reset
```

If the config already exists, `wwg init` asks whether to update part of the config, reset it, or exit.

### Update (Docker Compose)

Once it's installed, simply `cd` into the directory you installed it in and run:

```bash
docker compose pull
docker compose up -d
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

We recommend using Docker Compose as described above. It is the simplest way to install WWG: simply download `docker-compose.yml` into the `bitcoin-wallet-watchguard` directory (or whatever you want to name it) then run `docker compose pull` to automatically grab the latest stable image.

### Local

```bash
git clone https://github.com/xannythepleb/bitcoin-wallet-watchguard.git
cd bitcoin-wallet-watchguard
wwg init
wwg run
```

We only recommend using a local install unless you really really don't like Docker or you want to help with development and debugging in your local environment. With how Python manages dependencies, Docker is much cleaner, and the containerised environment provides you with extra security - for example, WWG in Docker only has access to its own database and config. It has zero access to other files and applications on your OS. It can't see your hot wallet private keys, your emails, your photos, your browser history, nothing.

It is also much easier to uninstall a Docker container fully if you decide you want to (although we hope you don't) as all the dependencies are inside the container.

**This guide and the example commands it provides will assume you are using Docker Compose as this is the recommended install method. Adjust accordingly if you aren't.**

## Examples of CLI Functionality

To run commands, e.g. `status`:

```bash
docker compose exec wallet-watchguard wwg status
```

Note the distinction between how we run the Docker Compose container during `init` and after setup is complete:

```bash
# First time setup - docker compose run --rm to create a temporary container that launches the onboarding wizard to create your config
docker compose run --rm wallet-watchguard wwg init

# Start the daemon
docker compose up -d

# Example usage commands - docker compose exec to run commands inside the daemon
## Check status of WWG
docker compose exec wallet-watchguard wwg status
## Check balance of your wallets
docker compose exec wallet-watchguard wwg balance
## Show next unused address from one of your wallets with QR code
docker compose exec wallet-watchguard wwg next
## Generate QR code to request Bitcoin with optional message, enter amount in either sats or BTC
docker compose ecec wallet-watchguard wwg request
## Add, remove, or rename a wallet
docker compose exec wallet-watchguard wwg wallet add
docker compose exec wallet-watchguard wwg wallet remove
docker compose exec wallet-watchguard wwg wallet rename
```

You can get plain output from commands such as `wwg next` and `wwg request` for use in scripts, allowing you to use WWG as a tool for automating your own Bitcoin workflow or projects.

For example:

```bash
addr="$(docker compose exec wallet-watchguard wwg request --wallet-index=1 --sats 50000 --note "Coffee" --plain)"
```

Stores the following within the `$addr` variable:

```bash
bitcoin:bc1p7lwvtp2mlv4l6wkmkcjh9rjxhma8ttwpz7pyf93f4srtcdehm3ustwkef0?amount=0.0005&message=Coffee
```

You can feed this variable into your own script or application to automate requests, create your own QR codes, write NFC tags, whatever you want.

Whereas if you run the same command without the `--plain` flag in the interactive terminal, you get both the metadata and the human friendly output with a QR code:

```bash
Wallet: Cake Wallet Nostr (bitcoin / taproot)
Receive address:
  m/86'/0'/0'/0/2    bc1p7lwvtp2mlv4l6wkmkcjh9rjxhma8ttwpz7pyf93f4srtcdehm3ustwkef0
Amount: 0.0005 BTC
Note: Coffee

Payment request:

  bitcoin:bc1p7lwvtp2mlv4l6wkmkcjh9rjxhma8ttwpz7pyf93f4srtcdehm3ustwkef0?amount=0.0005&message=Coffee

QR code:

█████████████████████████████████████████████
██ ▄▄▄▄▄ ██ █▀▀█▀▀▀█▀▄█ ▀▄█▀█▀ ▀▀▄ █ ▄▄▄▄▄ ██
██ █   █ █ ▄▄▀ █▀███▀█  ▄█▄▀ █▀▀█▄▀█ █   █ ██
██ █▄▄▄█ █ █▀▀███ ▀▄█▄▀▄█▄ ▄▀▀▄▀ ▄▄█ █▄▄▄█ ██
██▄▄▄▄▄▄▄█ ▀ █ █▄▀ █ █▄▀▄▀ █▄▀▄█ ▀▄█▄▄▄▄▄▄▄██
██▄▀  ▄ ▄█▀█▀ ▀ ▀██▄▄ ▀▀▄ ▀▄ █▀ ▀ ▀▀   ▄▄█▀██
████ ▀██▄▀█ ▄▄ ▄▀█ ▄▄█▀▀ █ █▄▄ █ ▀█▀▀▀ ▀▄ ███
██  ▄ █▀▄▄██▀▄  █   ▀█▀█ ▀▄▄  ▀▄▀  ▀▀▀▀▀ ▄▀██
██▄ ▄▀ ▀▄▄▄▄▀▀█▄█▀▀██ ▀  ███   ██▀▄█ ██ ██▀██
██▄█▀▀ █▄██▀▀ ▀ ▄█▀ ▄  ▀▄▀▄ ▄█▀▄█▀▄▀▀▀▀█▄ ▀██
██ ▀ ▄▀▄▄▀█ ▄██▄█▄  ▀▀▀ █▀█▄ ▄ █▄█  ▄▄▀▀▄█▀██
██▀▀ ▀▀▀▄▄▀▄  ▀ ▄█▄█▀ ▄▄█ █▄▄▄▄▄▀ ▀▄▀█▀▄▄  ██
██▀█ █  ▄█  █▄▀▀▀█ ▄▀▀▀  █ ▄  ▄▄▄▀███  ███▀██
██▀▀█▄▀ ▄ █ ▄ █▄▄▀▀▄█▀▄▀▄▀█▄ ▄▄ ▄▀▄▀▀▀▀▄ ▄▀██
██▄▄ ▄ ▀▄▀▄▀▄▄▀ ██▀▄▀▀▀ ██▄█▄ ██ ▀▀█▄▄▀ █ ▀██
██  ▀▄█ ▄ █  ▄██▄▄▄▄▄█▄█ ▀▄▄ ▄▄▄▀  ▀▀▀▀▄ █ ██
██ █▀█▀█▄█▄█▄ ▀██ ▀█ █▄▀ ▀▄▄▄  ▄▄█ ▄▄▄▀ ▄▀███
██▄█▄███▄▄▀ ▀ █ ███▄ ▀▀█ ▀▀▄▀▄ ▄██ ▄▄▄ ▀▄▀▀██
██ ▄▄▄▄▄ █▀▄█▄████▀█▄█ █▄█ ▄▄ ▀▄█  █▄█ ▀██▀██
██ █   █ █ ▄▀█▀▀▄ ▀█▄▀▀▄▄▀█▄▄▄▀▄▀█▄▄▄▄▄█▄▀▀██
██ █▄▄▄█ █▄▄▀█  ▄▀  ██▄ ▄▀█▄▄  █▄▀   ▄ ██▀███
██▄▄▄▄▄▄▄█▄▄▄▄██▄█▄▄█▄▄█▄██▄▄█▄▄█▄███▄▄▄▄████
▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀
```

These QR codes are extremely clear and readable even within a terminal:

<p align="center">
<img width="584" height="400" alt="image" src="https://github.com/user-attachments/assets/1a94f40c-f3a3-4882-8a10-b96f9d948774" />
</p>


When scanned, you will get something like this on your mobile Bitcoin wallet (UX varies by wallet, of course):

<p align="center">
<img width="645" height="718" alt="image" src="https://github.com/user-attachments/assets/c0a676e8-ac0d-4582-9d6f-ecb2d70c315b" />
</p>


QR codes are also generated when you run `wwg next` to get a new unused Bitcoin address. These do not include amounts or messages, just the address. If you don't want the QR codes when running `wwg next`, you can simply add the `--no-qr` flag, or use `--plain` to get only the address and nothing else, useful for variables and scripting.

For example if you want a list of 10 unused addresses for your first wallet without QR codes or any other metadata printed at all, just a simple list of line separated addresses, you can run:

```bash
docker compose exec wallet-watchguard wwg next 10 --wallet-index=1 --plain
```

If you want a list of three new addresses from your second wallet with both the derivation path and the addresses but no QR codes, use `--no-qr` instead:

```bash
docker compose exec wallet-watchguard wwg next 3 --wallet-index=2 --no-qr
```

As you can see, this tool is designed to be extremely flexible and usable both within the interactive terminal and within your own scripts and automations.

## Transaction Notifications

Whenever a transaction occurs, you will be notified via Ntfy.

If you have Mempool integration configured correctly, you will get extra details about the transaction like so:

```
Wallet: Jade Cold Wallet

Type: received

Status: unconfirmed/mempool

Amount: +69,420 sats

vsize: 420 vB

Fee rate: 2.67 sat/vB

Inputs:

* bc1q67xanz...
* bc1p420asd...

Outputs:

* ✅ bc1p999tap... - 69,418 sats

Wallet path: m/86'/0'/0'/0/4

Tx: abc123...
```

Without Mempool, you will get a simpler notification:

```
Wallet: Jade Cold Wallet

Type: activity

Status: confirmed at height 9999999

Address: bc1p999tap...

Wallet path: m/86'/0'/0'/0/4

Tx: abc123...
```

As you can see, Mempool allows WWG to pick up the amount, whether it was sent or received, details of the input and output, and fee paid. Instructions on how to configure Mempool integration are further down.

## Nostr Notifications

Currently, Conversation Mode is only available with Ntfy. However, you can configure Nostr for notifications. Nostr notifications use NIP-17 DMs that protect both the message and the metadata via NIP-44 encryption and NIP-59 gift wrapping.

The initial setup wizard will ask you if you want to configure Ntfy, Nostr, or both.

If you already configured WWG, you can add a Nostr config at any time by running:

```bash
docker compose exec wallet-watchguard wwg init --add nostr
```

This will create an npub for your WWG instance, ask for your npub (you can add multiple if you wish), and of course the relays you want to use - make sure these relays are configured as your DM inbox relays so the DMs reach your Nostr client.

WWG will then print its npub and follow yours. It is recommended you follow it back so the DMs don't get filtered out by your client. If you follow each other, this also allows you to use WoT relays for your WWG notifications.

You can enable or disable the notification providers you have configured via the CLI:

```bash
docker compose exec wallet-watchguard wwg enable nostr
docker compose exec wallet-watchguard wwg enable ntfy
docker compose exec wallet-watchguard wwg disable nostr
docker compose exec wallet-watchguard wwg disable ntfy
```

Note: Make sure you use the full image if you want Nostr support. The full image is the default one tagged latest. The slim image only supports Ntfy. If you want to switch, simply change your `docker-compose.yml` to:

```yml
image: ghcr.io/xannythepleb/bitcoin-wallet-watchguard:latest
```

Instead of:

```yml
image: ghcr.io/xannythepleb/bitcoin-wallet-watchguard:latest
```

Again, this is the default, so you only have the slim image if you actively chose it. If you pulled from the `docker-compose.yml` without editing it, you already have the full image.

## Conversation Mode: Talk to Your Wallet Anywhere

Conversation Mode lets you query Wallet Watchguard remotely through ntfy. You can keep an eye on even your hardware cold storage wherever you are via 100% self-hosted infrastructure. No third party middleman if you configure it correctly with your own node and your own ntfy instance. You can run both of these on your own physical hardware using Start9 or Umbrel for true sovereignty.

I specifically added this feature because I had a hard time finding a reliable watch only wallet that could work with any hardware wallet and allowed you to use your own node on the backend. So I made one. And because ntfy runs on Android, iOS, and in the browser, it is truly universal.

It is off by default and checks are in place to ensure it is only enabled on secure sessions (password protected, no public read/write access).

Enable it in config:

```bash
docker compose exec wallet-watchguard wwg init --add conversation
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

All of these commands are also accepted by the CLI.

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

## Start9 / StartOS Ntfy Setup

If you use Start9/StartOS ntfy, run:

```bash
docker compose exec wallet-watchguard wwg init --add ntfy
```

Choose the Start9/StartOS provision publisher path.

In the Start9 ntfy UI, use **Provision Publisher if you only want notifications and do not want Conversation Mode.** If you want Conversation Mode to work, follow the [Conversation Mode Setup](#conversation-mode-setup) instructions above to generate an access token through ntfy.

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
docker compose exec wallet-watchguard wwg init --add electrum
docker compose exec wallet-watchguard wwg init --add ntfy
docker compose exec wallet-watchguard wwg init --add mempool
```

TLS encryption still remains enabled; only public CA/hostname verification is skipped for that local self-hosted service.

For existing configs without `electrum.tls_verify`, Wallet Watchguard automatically relaxes Electrum TLS verification for localhost, private IPs, `.local`, `.lan`, `.onion`, and similar local targets.

## Tor Upstream for Onion Nodes

Wallet Watchguard supports `.onion` Electrum/Fulcrum hosts through both external SOCKS5 proxies and, for Docker and Docker Compose deployments, you can now enable an internal Tor proxy so the container directly supports upstream Tor only Bitcoin nodes without any manual networking config required.

This is off by default. Enable/disable it persistently from the CLI:

```bash
docker compose exec wallet-watchguard wwg tor enable
docker compose exec wallet-watchguard wwg tor disable
docker compose exec wallet-watchguard wwg tor status
```

You can also enable it for just a single session with an environment variable:

```bash
WWG_TOR_UPSTREAM=true docker compose up -d
```

Or if you don't use Docker Compose:

```bash
wwg run --tor-upstream
```

When the switch is on, Wallet Watchguard automatically sets the effective Electrum SOCKS proxy to `tor.socks_proxy`. The startup summary and `wwg status` output include a multi-line `Tor Upstream` section showing whether it is enabled, which proxy is used, and whether Wallet Watchguard manages the Tor process. The Electrum/Fulcrum section shows the effective SOCKS proxy and, after a connectivity probe, the Electrum/Fulcrum server version.

Test Tor connectivity manually with:

```bash
WWG_TOR_UPSTREAM=true docker compose run --rm wallet-watchguard wwg test-tor
```

This test probes your configured upstream Electrum/Fulcrum server. If the output shows the bootstrapping is successful but the connection to the server fails for some reason, the issue is with either your config or your server.

Make sure WWG is not configured to connect to your node via a local IP (e.g. 192.168.x.x) and make sure the port is not inside the domain (e.g. do not type domain.com:50002 into the config or the CLI). The port is separate in the config. Ideally, when using Tor to connect to Electrum, it should advertise an onion address. Put that as your Electrum server URL in the config. The Electrum port is 50002 if TLS is on or 50001 if it is not.

Do not enter your Electrum URL with an `https://` prefix even if it is using TLS/SSL. Just make sure you set port 50002. The address itself should contain no prefix at all.

If it succeeds you will see output like this when running the test:

```bash
[notice] Bootstrapped 95% (circuit_create): Establishing a Tor circuit
[notice] Bootstrapped 100% (done): Done
[notice] Catching signal TERM, exiting cleanly.
Tor connectivity test: ok (Fulcrum 2.1.1, 1.4)
```

If it times out, just try again. Tor can be fickle sometimes, that is the nature of the beast. If you run a Tor only Bitcoin node you are certainly no stranger to that. Once the connection is established it usually remains stable for low bandwidth applications. The bootstrapping itself is successful first time in 90% of cases.

## Ntfy & Electrum Test Commands

After configuring ntfy, restart the daemon and send a test notification:

```bash
docker compose restart wallet-watchguard
docker compose exec wallet-watchguard wwg test-ntfy
```

Once you have both Ntfy and Electrum up and running, you can test with a real transaction notification:

```bash
docker compose exec wallet-watchguard wwg test-ntfy --latest-tx
```

This will ask which wallet you want to test with, or you can select one directly by index or name:

```bash
docker compose exec wallet-watchguard wwg test-ntfy --latest-tx --wallet-index 1
docker compose exec wallet-watchguard wwg test-ntfy --latest-tx --wallet "Jade Cold Wallet"
```

If the `test-ntfy` command works but the notifications themselves don't, you can use `live-debug` to help diagnose this. With your daemon running:

```bash
docker compose exec wallet-watchguard wwg live-debug
```

This will test the full end-to-end process exactly as if WWG had just picked up a new transaction and print debug info on the terminal as it does it. This should help you troubleshoot issues in your config or pick up bugs in WWG. If it looks like the latter, please open an issue.

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
docker compose run --rm wallet-watchguard wwg init --add mempool
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
docker compose exec wallet-watchguard wwg fees
```

## Address and Balance Listing

List receive addresses and balances. If more than one wallet is configured, Wallet Watchguard asks which wallet you want to view:

```bash
docker compose exec wallet-watchguard wwg addresses --limit 20
```

Show the next unused wallet address:

```bash
docker compose exec wallet-watchguard wwg next
```

Show the total balance across all wallets:

```bash
docker compose exec wallet-watchguard wwg balance
```

## Status Command

`wwg status` prints the same non-secret summary format used at startup. When run as a one shot CLI command it cannot know the daemon's live subscription count, but it still shows the effective config, Electrum/Fulcrum target, Tor Upstream setting, ntfy target, Mempool setting, Conversation Mode setting, and configured wallets.

Conversation Mode also understands:

```bash
wwg status
```

That ntfy response is generated by the running daemon, so it includes live runtime values such as subscribed script count and the startup Tor probe result.

## Docker Compose Logs

When running successfully, the daemon prints a non-secret startup summary to the logs. This can be checked again at any time by running `wwg status` from the CLI.

```text
📦 Version: v0.1.8
⚙️ Config: ./data/config.yaml
💾 Database: ./data/watchguard.sqlite3
📡 Electrum/Fulcrum:
🌐 Tor Upstream: disabled
🔔 ntfy: https://ntfy.example/wwg auth=bearer tls_verify=true
🌊 Mempool API: disabled
🗣️ Conversation Mode: disabled
📜 Subscribed scripts: not connected in this status command

💵 Wallets:

🚀 Useful commands:
```

View logs:

```bash
docker compose logs -f wallet-watchguard
```

I made sure to use only emojis that correctly rendered in my terminal. But no one likes the "it works on my machine bro" guy so if the emojis cause issues, you can view status without emojis by running:

```bash
docker compose exec wallet-watchguard wwg status --no-emoji
```

Or disable them entirely by launching with the `--no-emoji` flag.

Please open an issue if you have crashes or any other weirdness with emojis enabled and tell me what distro you're using. It should not cause issues on any modern one (Ubuntu added emoji support in 18.04), but I'm documenting this just in case - if you have a niche or legacy distro and it won't run, try `--no-emoji` first.

## Slim Docker Image

There is a slim build of the Docker image that does not include the Tor package if you to not need it.

Modify your `docker-compose.yml` to use `slim` instead of `latest`:

```yml
image: ghcr.io/xannythepleb/bitcoin-wallet-watchguard:slim
```

Then run:

```bash
docker compose pull
```

Currently the only difference between the two not including Tor. The changes will become more substantial in the future.

## Tech Stack

Bitcoin Wallet Watchguard uses:

* Python for orchestration, CLI, async networking, config, SQLite, ntfy, and Mempool integration
* Rust for exact Bitcoin address/script derivation
* Fulcrum/Electrum for wallet activity subscriptions
* SOCKS5 proxy support, both external and internal (for Docker), for upstream connectivity to Tor only Bitcoin nodes
* SQLite for local deduplication and state
* Ntfy for self-hosted notifications and commands (effectively a self-hosted bot for your node)
* Optional Mempool integration for richer transaction and fee data
* Argon2id for password hashing
* XSalsa20-Poly1305 for encrypted-at-rest xpubs and credentials (via PyNaCl/libsodium)
* Docker Compose for reliable 24/7 deployment

## Dependencies

WWG uses a short list of dependencies. I wanted to strike the right balance between using battletested libraries, especially for sensitive and difficult operations such as cryptography, and reducing bloat and attack surface.

Below are the Python dependencies and what they're used for:

- **PyYAML**: Reads and writes WWG’s `config.yaml`, including wallet definitions, Electrum settings, Tor options, notifications, and all other configuration options.
- **httpx**: Makes HTTP requests for external services such as Mempool lookups, Ntfy endpoints, Electrum servers, and other API calls.
- **aiosqlite**: Provides async SQLite access for WWG’s local database, including wallet events, transaction history, and stats.
- **PyNaCl**: Handles cryptographic operations for secure passphrase based encryption and password hashing. This is used to encrypt xpubs at rest.
- **python-socks[asyncio]**: Adds async SOCKS proxy support, used for routing Electrum or HTTP connections through Tor, especially when connecting to `.onion` services.
- **segno**: Generates QR codes for latest Bitcoin addresses and payment requests.

And for the Rust helper:

* **anyhow**: Used for simple fallible CLI flow: `Result<()>`, custom validation errors via `anyhow!`, and extra context on parsing/derivation failures via `.context(...)`
* **bitcoin**: The core wallet derivation library. Used to parse the supplied `xpub`, derive public keys, and generate the four main Bitcoin address types:
  * Taproot: `p2tr`
  * Native SegWit: `p2wpkh`
  * Nested SegWit: `p2shwpkh`
  * Legacy: `p2pkh`
* **clap**: Defines the `wwg-derive` CLI interface. It parses the `derive` subcommand and arguments like `--xpub`, `--network`, `--wallet-type`, `--start`, `--end`, etc. `ValueEnum` is used for valid wallet/network choices.
* **hex**: Used once to encode each derived address’s `script_pubkey` bytes into a hex string for JSON output.
* **serde**: Used only to derive `Serialize` for `DerivedRow`, the struct representing each derived address row.
* **serde_json**: Serialises the list of derived rows into pretty printed JSON, which the Python side of WWG can consume.
* **nostr-sdk**: For Nostr notifications via end-to-end encrypted gift wrapped DMs.

Additionally, the Tor Debian package is installed on the Docker image in order to provide the built in Tor functionality that allows connection to upstream Bitcoin nodes that are accessible via an onion service without external proxy configuration.

## Security Notes

The xpub and ntfy credentials are encrypted at rest using the Wallet Watchguard passphrase. You need this passphrase whenever the daemon starts.

An xpub cannot spend funds, but it can reveal wallet history and future addresses. Run Wallet Watchguard on infrastructure you control, including your own Bitcoin node, Fulcrum, and ntfy instance for the highest level of privacy.

The Docker image has limited permissions by design. Even though this application is not designed to be accessible to the internet, and therefore the only attack vector would be whatever else is on your own machine, I've still ensured that gaining access to the WWG Docker container doesn't grant root access to it - it runs in its own user inside the Debian based environment the image is built on.