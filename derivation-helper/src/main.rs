use anyhow::{anyhow, Context, Result};
use bitcoin::bip32::{ChildNumber, DerivationPath, Xpub};
use bitcoin::key::PublicKey;
use bitcoin::secp256k1::Secp256k1;
use bitcoin::{Address, Network};
use clap::{Parser, Subcommand, ValueEnum};
use serde::Serialize;
use std::str::FromStr;

#[derive(Parser)]
#[command(name = "wwg-derive")]
#[command(about = "Wallet Watchguard address derivation helper")]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    Derive(DeriveArgs),
}

#[derive(Parser)]
struct DeriveArgs {
    #[arg(long)]
    xpub: String,

    #[arg(long)]
    network: NetworkArg,

    #[arg(long)]
    wallet_type: WalletType,

    #[arg(long, default_value = "m/86'/0'/0'")]
    account_path: String,

    /// Relative to the account xpub. Example: 0/* or 1/*.
    #[arg(long, default_value = "0/*")]
    path_template: String,

    #[arg(long)]
    start: u32,

    #[arg(long)]
    end: u32,
}

#[derive(Copy, Clone, ValueEnum)]
enum WalletType {
    Taproot,
    NativeSegwit,
    NestedSegwit,
    Legacy,
}

#[derive(Copy, Clone, ValueEnum)]
enum NetworkArg {
    Bitcoin,
    Testnet,
    Signet,
    Regtest,
}

impl From<NetworkArg> for Network {
    fn from(value: NetworkArg) -> Self {
        match value {
            NetworkArg::Bitcoin => Network::Bitcoin,
            NetworkArg::Testnet => Network::Testnet,
            NetworkArg::Signet => Network::Signet,
            NetworkArg::Regtest => Network::Regtest,
        }
    }
}

#[derive(Serialize)]
struct DerivedRow {
    index: u32,
    path: String,
    address: String,
    script_pubkey: String,
}

fn main() -> Result<()> {
    let cli = Cli::parse();

    match cli.command {
        Commands::Derive(args) => derive(args),
    }
}

fn derive(args: DeriveArgs) -> Result<()> {
    if args.end < args.start {
        return Err(anyhow!("end must be greater than or equal to start"));
    }

    let network: Network = args.network.into();
    let xpub = Xpub::from_str(&args.xpub).context("invalid xpub")?;
    let secp = Secp256k1::verification_only();

    let mut rows = Vec::new();
    for index in args.start..=args.end {
        let relative_path_str = args.path_template.replace('*', &index.to_string());
        let relative_path = parse_relative_derivation_path(&relative_path_str)?;
        reject_hardened(&relative_path)?;

        let child = xpub
            .derive_pub(&secp, &relative_path)
            .context("failed to derive child public key")?;

        let address = match args.wallet_type {
            WalletType::Taproot => Address::p2tr(&secp, child.to_x_only_pub(), None, network),
            WalletType::NativeSegwit => Address::p2wpkh(&child.to_pub(), network),
            WalletType::NestedSegwit => Address::p2shwpkh(&child.to_pub(), network),
            WalletType::Legacy => Address::p2pkh(&PublicKey::new(child.public_key), network),
        };

        let script_pubkey = address.script_pubkey();
        rows.push(DerivedRow {
            index,
            path: join_account_and_relative_path(&args.account_path, &relative_path_str),
            address: address.to_string(),
            script_pubkey: hex::encode(script_pubkey.as_bytes()),
        });
    }

    println!("{}", serde_json::to_string_pretty(&rows)?);
    Ok(())
}

fn parse_relative_derivation_path(path: &str) -> Result<DerivationPath> {
    let clean = path.trim().trim_start_matches("m/");
    let full = if clean.is_empty() {
        "m".to_string()
    } else {
        format!("m/{clean}")
    };
    DerivationPath::from_str(&full).context("invalid relative derivation path")
}

fn reject_hardened(path: &DerivationPath) -> Result<()> {
    for child in path.as_ref() {
        match child {
            ChildNumber::Normal { .. } => {}
            ChildNumber::Hardened { .. } => {
                return Err(anyhow!("relative path contains a hardened child; cannot derive hardened children from an xpub"));
            }
        }
    }
    Ok(())
}

fn join_account_and_relative_path(account_path: &str, relative: &str) -> String {
    let account = account_path.trim_end_matches('/');
    let rel = relative.trim_start_matches("m/").trim_start_matches('/');
    if rel.is_empty() {
        account.to_string()
    } else {
        format!("{account}/{rel}")
    }
}
