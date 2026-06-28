use anyhow::{bail, Context, Result};
use clap::{Parser, Subcommand};
use nostr_sdk::prelude::*;
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;
use std::io::{self, Read};
use std::time::Duration;

#[derive(Parser)]
#[command(name = "wwg-nostr")]
#[command(about = "Wallet Watchguard Nostr notification helper")]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Generate a dedicated Nostr keypair for WWG notifications.
    GenerateKey,

    /// Print the npub belonging to an nsec read from stdin.
    PublicKey,

    /// Send NIP-17 encrypted direct messages. Reads JSON from stdin.
    SendDm,

    /// Check whether the supplied relays can be reached. Reads JSON from stdin.
    TestRelays,
}

#[derive(Serialize)]
struct GenerateKeyOutput {
    npub: String,
    nsec: String,
}

#[derive(Serialize)]
struct PublicKeyOutput {
    npub: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct SendDmRequest {
    sender_nsec: String,
    message: String,
    recipients: Vec<RecipientInput>,
    #[serde(default)]
    relays: Vec<String>,
    #[serde(default = "default_min_successful_relays")]
    min_successful_relays: usize,
    #[serde(default)]
    send_copy_to_self: bool,
    #[serde(default = "default_timeout_seconds")]
    connect_timeout_seconds: u64,
}

#[derive(Debug, Deserialize)]
#[serde(untagged)]
enum RecipientInput {
    Npub(String),
    Object {
        name: Option<String>,
        npub: String,
        #[serde(default)]
        relays: Vec<String>,
    },
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct TestRelaysRequest {
    relays: Vec<String>,
    #[serde(default = "default_timeout_seconds")]
    connect_timeout_seconds: u64,
}

#[derive(Serialize)]
struct SendDmOutput {
    ok: bool,
    sender_npub: String,
    recipients: Vec<RecipientDelivery>,
    #[serde(skip_serializing_if = "Option::is_none")]
    self_copy: Option<RecipientDelivery>,
}

#[derive(Serialize)]
struct RecipientDelivery {
    ok: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    name: Option<String>,
    npub: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    event_id: Option<String>,
    accepted_relays: Vec<String>,
    failed_relays: Vec<RelayFailure>,
}

#[derive(Serialize)]
struct RelayFailure {
    url: String,
    error: String,
}

#[derive(Serialize)]
struct TestRelaysOutput {
    ok: bool,
    accepted_relays: Vec<String>,
    failed_relays: Vec<RelayFailure>,
}

fn default_min_successful_relays() -> usize {
    1
}

fn default_timeout_seconds() -> u64 {
    10
}

#[tokio::main]
async fn main() -> Result<()> {
    let cli = Cli::parse();

    match cli.command {
        Commands::GenerateKey => generate_key(),
        Commands::PublicKey => public_key(),
        Commands::SendDm => send_dm().await,
        Commands::TestRelays => test_relays().await,
    }
}

fn generate_key() -> Result<()> {
    let keys = Keys::generate();
    let output = GenerateKeyOutput {
        npub: keys.public_key().to_bech32().context("failed to encode npub")?,
        nsec: keys
            .secret_key()
            .to_bech32()
            .context("failed to encode nsec")?,
    };
    print_json(&output)
}

fn public_key() -> Result<()> {
    let nsec = read_stdin_string("nsec")?;
    let keys = Keys::parse(nsec.trim()).context("invalid sender nsec")?;
    let output = PublicKeyOutput {
        npub: keys.public_key().to_bech32().context("failed to encode npub")?,
    };
    print_json(&output)
}

async fn send_dm() -> Result<()> {
    let request: SendDmRequest = read_json_stdin("send-dm request")?;
    validate_send_dm_request(&request)?;

    let keys = Keys::parse(request.sender_nsec.trim()).context("invalid sender nsec")?;
    let sender_npub = keys.public_key().to_bech32().context("failed to encode sender npub")?;
    let client = Client::new(keys.clone());
    let timeout = Duration::from_secs(request.connect_timeout_seconds.max(1));

    let all_relays = collect_all_relays(&request.relays, &request.recipients);
    add_write_relays(&client, all_relays.iter()).await?;
    let _connect_output = client.try_connect(timeout).await;

    let mut deliveries = Vec::new();
    for recipient in &request.recipients {
        let recipient_spec = RecipientSpec::from_input(recipient)?;
        let recipient_relays = merge_relays(&request.relays, &recipient_spec.relays);
        let delivery = send_to_recipient(
            &client,
            &recipient_spec,
            &recipient_relays,
            &request.message,
            request.min_successful_relays,
        )
        .await;
        deliveries.push(delivery);
    }

    let self_copy = if request.send_copy_to_self {
        let sender_spec = RecipientSpec {
            name: Some("self".to_string()),
            npub: sender_npub.clone(),
            relays: Vec::new(),
        };
        Some(
            send_to_recipient(
                &client,
                &sender_spec,
                &request.relays,
                &request.message,
                request.min_successful_relays,
            )
            .await,
        )
    } else {
        None
    };

    client.disconnect().await;

    let recipients_ok = deliveries.iter().all(|delivery| delivery.ok);
    let self_copy_ok = self_copy.as_ref().map(|delivery| delivery.ok).unwrap_or(true);
    let output = SendDmOutput {
        ok: recipients_ok && self_copy_ok,
        sender_npub,
        recipients: deliveries,
        self_copy,
    };
    print_json(&output)
}

async fn test_relays() -> Result<()> {
    let request: TestRelaysRequest = read_json_stdin("test-relays request")?;
    if request.relays.is_empty() {
        bail!("at least one relay is required");
    }
    validate_relays(&request.relays)?;

    let client = Client::default();
    add_write_relays(&client, request.relays.iter()).await?;
    let output = client
        .try_connect(Duration::from_secs(request.connect_timeout_seconds.max(1)))
        .await;
    client.disconnect().await;

    let result = TestRelaysOutput {
        ok: !output.success.is_empty() && output.failed.is_empty(),
        accepted_relays: relay_set_to_strings(output.success),
        failed_relays: relay_failures_to_vec(output.failed),
    };
    print_json(&result)
}

fn validate_send_dm_request(request: &SendDmRequest) -> Result<()> {
    if request.sender_nsec.trim().is_empty() {
        bail!("sender_nsec is required");
    }
    if request.message.trim().is_empty() {
        bail!("message is required");
    }
    if request.recipients.is_empty() {
        bail!("at least one recipient is required");
    }
    if request.relays.is_empty() {
        bail!("at least one relay is required");
    }
    if request.min_successful_relays == 0 {
        bail!("min_successful_relays must be at least 1");
    }
    if request.send_copy_to_self && request.min_successful_relays > request.relays.len() {
        bail!(
            "min_successful_relays ({}) is greater than the global relay count ({}) for the self-copy",
            request.min_successful_relays,
            request.relays.len(),
        );
    }

    validate_relays(&request.relays)?;
    for recipient in &request.recipients {
        let recipient_spec = RecipientSpec::from_input(recipient)?;
        validate_relays(&recipient_spec.relays)?;
        let effective_relays = merge_relays(&request.relays, &recipient_spec.relays);
        if request.min_successful_relays > effective_relays.len() {
            bail!(
                "min_successful_relays ({}) is greater than the effective relay count ({}) for recipient {}",
                request.min_successful_relays,
                effective_relays.len(),
                recipient_spec.npub,
            );
        }
    }

    Ok(())
}

fn validate_relays(relays: &[String]) -> Result<()> {
    for relay in relays {
        let trimmed = relay.trim();
        if trimmed.is_empty() {
            bail!("relay URL cannot be blank");
        }
        if !(trimmed.starts_with("wss://") || trimmed.starts_with("ws://")) {
            bail!("relay URL must start with ws:// or wss://: {trimmed}");
        }
    }
    Ok(())
}

async fn add_write_relays<'a, I>(client: &Client, relays: I) -> Result<()>
where
    I: IntoIterator<Item = &'a String>,
{
    for relay in relays {
        client
            .add_write_relay(relay.as_str())
            .await
            .with_context(|| format!("failed to add relay {relay}"))?;
    }
    Ok(())
}

async fn send_to_recipient(
    client: &Client,
    recipient: &RecipientSpec,
    relays: &[String],
    message: &str,
    min_successful_relays: usize,
) -> RecipientDelivery {
    let parsed_recipient = match PublicKey::parse(recipient.npub.trim()) {
        Ok(public_key) => public_key,
        Err(error) => {
            return RecipientDelivery {
                ok: false,
                name: recipient.name.clone(),
                npub: recipient.npub.clone(),
                event_id: None,
                accepted_relays: Vec::new(),
                failed_relays: vec![RelayFailure {
                    url: "recipient".to_string(),
                    error: format!("invalid npub: {error}"),
                }],
            }
        }
    };

    match client
        .send_private_msg_to(relays.iter().map(String::as_str), parsed_recipient, message, Vec::<Tag>::new())
        .await
    {
        Ok(output) => {
            let accepted_relays = relay_set_to_strings(output.success);
            let ok = accepted_relays.len() >= min_successful_relays;
            RecipientDelivery {
                ok,
                name: recipient.name.clone(),
                npub: recipient.npub.clone(),
                event_id: Some(output.val.to_string()),
                accepted_relays,
                failed_relays: relay_failures_to_vec(output.failed),
            }
        }
        Err(error) => RecipientDelivery {
            ok: false,
            name: recipient.name.clone(),
            npub: recipient.npub.clone(),
            event_id: None,
            accepted_relays: Vec::new(),
            failed_relays: vec![RelayFailure {
                url: "send_private_msg_to".to_string(),
                error: error.to_string(),
            }],
        },
    }
}

#[derive(Debug)]
struct RecipientSpec {
    name: Option<String>,
    npub: String,
    relays: Vec<String>,
}

impl RecipientSpec {
    fn from_input(input: &RecipientInput) -> Result<Self> {
        match input {
            RecipientInput::Npub(npub) => {
                let npub = npub.trim().to_string();
                if npub.is_empty() {
                    bail!("recipient npub cannot be blank");
                }
                PublicKey::parse(&npub).with_context(|| format!("invalid recipient npub: {npub}"))?;
                Ok(Self {
                    name: None,
                    npub,
                    relays: Vec::new(),
                })
            }
            RecipientInput::Object { name, npub, relays } => {
                let npub = npub.trim().to_string();
                if npub.is_empty() {
                    bail!("recipient npub cannot be blank");
                }
                PublicKey::parse(&npub).with_context(|| format!("invalid recipient npub: {npub}"))?;
                Ok(Self {
                    name: name.as_ref().map(|value| value.trim().to_string()).filter(|value| !value.is_empty()),
                    npub,
                    relays: trim_and_dedupe_relays(relays),
                })
            }
        }
    }
}

fn collect_all_relays(global_relays: &[String], recipients: &[RecipientInput]) -> Vec<String> {
    let mut relays = trim_and_dedupe_relays(global_relays);
    for recipient in recipients {
        if let Ok(spec) = RecipientSpec::from_input(recipient) {
            relays = merge_relays(&relays, &spec.relays);
        }
    }
    relays
}

fn merge_relays(global_relays: &[String], recipient_relays: &[String]) -> Vec<String> {
    let mut values = BTreeSet::new();
    for relay in global_relays.iter().chain(recipient_relays.iter()) {
        let relay = relay.trim();
        if !relay.is_empty() {
            values.insert(relay.to_string());
        }
    }
    values.into_iter().collect()
}

fn trim_and_dedupe_relays(relays: &[String]) -> Vec<String> {
    let mut values = BTreeSet::new();
    for relay in relays {
        let relay = relay.trim();
        if !relay.is_empty() {
            values.insert(relay.to_string());
        }
    }
    values.into_iter().collect()
}

fn relay_set_to_strings(relays: std::collections::HashSet<RelayUrl>) -> Vec<String> {
    let mut values: Vec<String> = relays.into_iter().map(|relay| relay.to_string()).collect();
    values.sort();
    values
}

fn relay_failures_to_vec(failed: std::collections::HashMap<RelayUrl, String>) -> Vec<RelayFailure> {
    let mut values: Vec<RelayFailure> = failed
        .into_iter()
        .map(|(url, error)| RelayFailure {
            url: url.to_string(),
            error,
        })
        .collect();
    values.sort_by(|left, right| left.url.cmp(&right.url));
    values
}

fn read_stdin_string(label: &str) -> Result<String> {
    let mut input = String::new();
    io::stdin()
        .read_to_string(&mut input)
        .with_context(|| format!("failed to read {label} from stdin"))?;
    if input.trim().is_empty() {
        bail!("{label} is required on stdin");
    }
    Ok(input)
}

fn read_json_stdin<T>(label: &str) -> Result<T>
where
    T: for<'de> Deserialize<'de>,
{
    let input = read_stdin_string(label)?;
    serde_json::from_str(&input).with_context(|| format!("invalid {label} JSON"))
}

fn print_json<T>(value: &T) -> Result<()>
where
    T: Serialize,
{
    println!("{}", serde_json::to_string_pretty(value)?);
    Ok(())
}
