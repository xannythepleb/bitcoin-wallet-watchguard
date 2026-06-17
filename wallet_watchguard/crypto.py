from __future__ import annotations

import base64
import getpass
from dataclasses import dataclass

from nacl import pwhash, secret, utils
from nacl.exceptions import CryptoError


SCHEME = "pynacl-secretbox-argon2id-v1"


@dataclass(frozen=True)
class SecretEncryptionMetadata:
    scheme: str
    kdf: str
    opslimit: int
    memlimit: int
    salt_b64: str


# Backwards-compatible alias for existing xpub code/imports.
XpubEncryptionMetadata = SecretEncryptionMetadata


def _b64e(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _b64d(data: str) -> bytes:
    return base64.b64decode(data.encode("ascii"), validate=True)


def generate_salt() -> bytes:
    return utils.random(pwhash.argon2id.SALTBYTES)


def derive_key_from_passphrase(
    passphrase: str,
    salt: bytes,
    opslimit: int = pwhash.argon2id.OPSLIMIT_MODERATE,
    memlimit: int = pwhash.argon2id.MEMLIMIT_MODERATE,
) -> bytes:
    """
    Derive a 32-byte SecretBox key from a user passphrase using Argon2id.
    """
    if not passphrase:
        raise ValueError("Passphrase must not be blank")

    return pwhash.argon2id.kdf(
        secret.SecretBox.KEY_SIZE,
        passphrase.encode("utf-8"),
        salt,
        opslimit=opslimit,
        memlimit=memlimit,
    )


def encrypt_string_with_passphrase(
    plaintext: str,
    passphrase: str,
) -> tuple[str, SecretEncryptionMetadata]:
    """
    Encrypt a UTF-8 string using a passphrase-derived SecretBox key.
    """
    if plaintext is None:
        raise ValueError("Plaintext must not be None")

    salt = generate_salt()
    opslimit = pwhash.argon2id.OPSLIMIT_MODERATE
    memlimit = pwhash.argon2id.MEMLIMIT_MODERATE

    key = derive_key_from_passphrase(
        passphrase=passphrase,
        salt=salt,
        opslimit=opslimit,
        memlimit=memlimit,
    )

    box = secret.SecretBox(key)
    encrypted = box.encrypt(plaintext.encode("utf-8"))

    metadata = SecretEncryptionMetadata(
        scheme=SCHEME,
        kdf="argon2id",
        opslimit=opslimit,
        memlimit=memlimit,
        salt_b64=_b64e(salt),
    )

    return _b64e(bytes(encrypted)), metadata


def decrypt_string_with_passphrase(
    encrypted_value_b64: str,
    passphrase: str,
    metadata: SecretEncryptionMetadata,
    *,
    secret_name: str = "secret",
) -> str:
    if metadata.scheme != SCHEME:
        raise ValueError(f"Unsupported {secret_name} encryption scheme: {metadata.scheme}")

    if metadata.kdf != "argon2id":
        raise ValueError(f"Unsupported {secret_name} KDF: {metadata.kdf}")

    salt = _b64d(metadata.salt_b64)

    key = derive_key_from_passphrase(
        passphrase=passphrase,
        salt=salt,
        opslimit=metadata.opslimit,
        memlimit=metadata.memlimit,
    )

    box = secret.SecretBox(key)

    try:
        decrypted = box.decrypt(_b64d(encrypted_value_b64))
    except CryptoError as exc:
        raise ValueError(f"Unable to decrypt {secret_name}. The passphrase may be incorrect.") from exc

    return decrypted.decode("utf-8")


def encrypt_xpub_with_passphrase(
    xpub: str,
    passphrase: str,
) -> tuple[str, SecretEncryptionMetadata]:
    if not xpub:
        raise ValueError("xpub must not be blank")

    return encrypt_string_with_passphrase(xpub, passphrase)


def decrypt_xpub_with_passphrase(
    encrypted_xpub_b64: str,
    passphrase: str,
    metadata: SecretEncryptionMetadata,
) -> str:
    return decrypt_string_with_passphrase(
        encrypted_value_b64=encrypted_xpub_b64,
        passphrase=passphrase,
        metadata=metadata,
        secret_name="xpub",
    )


def metadata_from_config(config: dict) -> SecretEncryptionMetadata:
    return SecretEncryptionMetadata(
        scheme=config["scheme"],
        kdf=config["kdf"],
        opslimit=int(config["opslimit"]),
        memlimit=int(config["memlimit"]),
        salt_b64=config["salt"],
    )


def metadata_to_config(metadata: SecretEncryptionMetadata) -> dict[str, object]:
    return {
        "scheme": metadata.scheme,
        "kdf": metadata.kdf,
        "opslimit": metadata.opslimit,
        "memlimit": metadata.memlimit,
        "salt": metadata.salt_b64,
    }


def prompt_new_passphrase() -> str:
    while True:
        passphrase = getpass.getpass("Set Wallet Watchguard encryption passphrase: ")
        confirm = getpass.getpass("Confirm encryption passphrase: ")

        if not passphrase:
            print("Passphrase must not be blank.")
            continue

        if passphrase != confirm:
            print("Passphrases do not match.")
            continue

        return passphrase


def prompt_existing_passphrase() -> str:
    passphrase = getpass.getpass("Wallet Watchguard encryption passphrase: ")

    if not passphrase:
        raise ValueError("Passphrase must not be blank")

    return passphrase