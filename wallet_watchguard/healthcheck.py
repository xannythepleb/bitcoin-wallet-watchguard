"""Docker/runtime health check for Bitcoin Wallet Watchguard.

This module deliberately avoids network checks. It does not test Tor, Electrum,
wallet scanning, or notifications because those can fail for reasons outside the
container's local health.

The intended Docker usage is:

    HEALTHCHECK CMD ["wwg", "healthcheck"]

The command should exit:
    0 = healthy
    1 = unhealthy
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import DEFAULT_CONFIG_PATH, DEFAULT_DATABASE_PATH
DEFAULT_DERIVATION_HELPER_PATH = Path("./wwg-derive")


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    message: str


def _resolve_path(path: str | Path) -> Path:
    """Resolve a path without requiring it to already exist."""

    candidate = Path(path).expanduser()

    if candidate.is_absolute():
        return candidate

    return (Path.cwd() / candidate).resolve()


def _load_yaml_config(config_path: Path) -> dict[str, Any]:
    """Load and parse the YAML config file."""

    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML is not installed, so config.yaml cannot be parsed") from exc

    try:
        with config_path.open("r", encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise RuntimeError(f"invalid YAML: {exc}") from exc

    if loaded is None:
        raise RuntimeError("config file is empty")

    if not isinstance(loaded, dict):
        raise RuntimeError("config file must contain a top-level YAML mapping/object")

    return loaded


def _get_nested_value(config: dict[str, Any], *paths: tuple[str, ...]) -> Any | None:
    """Return the first matching nested config value from a list of candidate paths."""

    for path in paths:
        current: Any = config

        for part in path:
            if not isinstance(current, dict) or part not in current:
                current = None
                break

            current = current[part]

        if current is not None:
            return current

    return None


def _path_from_config_or_default(
    configured_value: Any | None,
    default_path: Path,
) -> Path:
    """Return a resolved path from config, falling back to a default."""

    if isinstance(configured_value, str) and configured_value.strip():
        return _resolve_path(configured_value)

    return _resolve_path(default_path)


def check_config(config_path: Path) -> tuple[CheckResult, dict[str, Any] | None]:
    """Check that config exists and parses as YAML."""

    if not config_path.exists():
        return (
            CheckResult("config", False, f"config file does not exist: {config_path}"),
            None,
        )

    if not config_path.is_file():
        return (
            CheckResult("config", False, f"config path is not a file: {config_path}"),
            None,
        )

    try:
        config = _load_yaml_config(config_path)
    except RuntimeError as exc:
        return CheckResult("config", False, str(exc)), None

    return CheckResult("config", True, f"config parsed: {config_path}"), config


def check_data_directory(data_dir: Path) -> CheckResult:
    """Check that the data directory exists and is readable/writable."""

    if not data_dir.exists():
        return CheckResult("data", False, f"data directory does not exist: {data_dir}")

    if not data_dir.is_dir():
        return CheckResult("data", False, f"data path is not a directory: {data_dir}")

    if not os.access(data_dir, os.R_OK):
        return CheckResult("data", False, f"data directory is not readable: {data_dir}")

    if not os.access(data_dir, os.W_OK):
        return CheckResult("data", False, f"data directory is not writable: {data_dir}")

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=".wwg-healthcheck-",
            suffix=".tmp",
            dir=data_dir,
            delete=True,
        ) as handle:
            handle.write("ok\n")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        return CheckResult(
            "data",
            False,
            f"failed to create/delete temp file in data directory {data_dir}: {exc}",
        )

    return CheckResult("data", True, f"data directory is readable/writable: {data_dir}")


def check_database(database_path: Path) -> CheckResult:
    """Check that the SQLite database can be opened if it already exists.

    If the DB does not exist yet, do not create it here. The health check should
    avoid mutating application state. In that case, check that the parent
    directory is writable and let the main application initialise the DB.
    """

    database_parent = database_path.parent

    if not database_parent.exists():
        return CheckResult(
            "database",
            False,
            f"database parent directory does not exist: {database_parent}",
        )

    if not os.access(database_parent, os.W_OK):
        return CheckResult(
            "database",
            False,
            f"database parent directory is not writable: {database_parent}",
        )

    if not database_path.exists():
        return CheckResult(
            "database",
            True,
            f"database does not exist yet, but parent directory is writable: {database_path}",
        )

    if not database_path.is_file():
        return CheckResult(
            "database",
            False,
            f"database path exists but is not a file: {database_path}",
        )

    try:
        # mode=rw avoids silently creating a new DB.
        connection = sqlite3.connect(f"file:{database_path}?mode=rw", uri=True, timeout=5)
        try:
            connection.execute("SELECT 1;").fetchone()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        return CheckResult("database", False, f"failed to open SQLite database: {exc}")

    return CheckResult("database", True, f"SQLite database can be opened: {database_path}")


def check_derivation_helper(helper_path: Path) -> CheckResult:
    """Check that the Rust derivation helper exists, is executable, and can start."""

    if not helper_path.exists():
        return CheckResult(
            "derivation-helper",
            False,
            f"derivation helper does not exist: {helper_path}",
        )

    if not helper_path.is_file():
        return CheckResult(
            "derivation-helper",
            False,
            f"derivation helper path is not a file: {helper_path}",
        )

    if not os.access(helper_path, os.X_OK):
        return CheckResult(
            "derivation-helper",
            False,
            f"derivation helper is not executable: {helper_path}",
        )

    try:
        result = subprocess.run(
            [str(helper_path), "--help"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return CheckResult(
            "derivation-helper",
            False,
            f"derivation helper timed out when running --help: {helper_path}",
        )
    except OSError as exc:
        return CheckResult(
            "derivation-helper",
            False,
            f"failed to execute derivation helper {helper_path}: {exc}",
        )

    if result.returncode != 0:
        stderr = result.stderr.strip()
        detail = f": {stderr}" if stderr else ""
        return CheckResult(
            "derivation-helper",
            False,
            f"derivation helper exited non-zero when running --help{detail}",
        )

    return CheckResult(
        "derivation-helper",
        True,
        f"derivation helper exists and can execute: {helper_path}",
    )


def run_healthcheck(
    *,
    config_path: Path,
    data_dir_override: Path | None,
    database_path_override: Path | None,
    helper_path_override: Path | None,
    verbose: bool,
) -> int:
    """Run all local health checks and return a process exit code."""

    results: list[CheckResult] = []

    config_result, config = check_config(config_path)
    results.append(config_result)

    # If config cannot be parsed, fall back to sensible defaults so the output can
    # still report useful filesystem/helper failures in one run.
    config_data = config or {}

    configured_data_dir = _get_nested_value(
        config_data,
        ("data_dir",),
        ("data", "dir"),
        ("paths", "data_dir"),
    )

    configured_database_path = _get_nested_value(
        config_data,
        ("database_path",),
        ("db_path",),
        ("database",),
        ("database", "path"),
        ("db", "path"),
        ("paths", "database"),
        ("paths", "database_path"),
    )

    configured_helper_path = _get_nested_value(
        config_data,
        ("derivation_helper",),
        ("derivation_helper_path",),
        ("derive_helper",),
        ("derive_helper_path",),
        ("paths", "derivation_helper"),
        ("paths", "derivation_helper_path"),
    )

    if data_dir_override is not None:
        data_dir = data_dir_override
    elif configured_data_dir is not None:
        data_dir = _path_from_config_or_default(configured_data_dir, config_path.parent)
    else:
        data_dir = config_path.parent

    if database_path_override is not None:
        database_path = database_path_override
    elif configured_database_path is not None:
        database_path = _path_from_config_or_default(
            configured_database_path,
            DEFAULT_DATABASE_PATH,
        )
    else:
        database_path = _resolve_path(DEFAULT_DATABASE_PATH)

    if helper_path_override is not None:
        helper_path = helper_path_override
    else:
        helper_path = _path_from_config_or_default(
            configured_helper_path,
            DEFAULT_DERIVATION_HELPER_PATH,
        )

    results.append(check_data_directory(data_dir))
    results.append(check_database(database_path))
    results.append(check_derivation_helper(helper_path))

    failed_results = [result for result in results if not result.ok]

    if verbose:
        for result in results:
            status = "ok" if result.ok else "failed"
            stream = sys.stdout if result.ok else sys.stderr
            print(f"{result.name}: {status}: {result.message}", file=stream)
    elif failed_results:
        for result in failed_results:
            print(f"{result.name}: {result.message}", file=sys.stderr)
    else:
        print("ok")

    return 1 if failed_results else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wwg healthcheck",
        description="Run local Docker/runtime health checks for Wallet Watchguard.",
    )

    parser.add_argument(
        "--config",
        default=os.environ.get("WWG_CONFIG") or os.environ.get("WWG_CONFIG_PATH") or str(DEFAULT_CONFIG_PATH),
        help="Path to the WWG config file. Defaults to ./data/config.yaml.",
    )

    parser.add_argument(
        "--data-dir",
        default=os.environ.get("WWG_DATA_DIR"),
        help="Override the data directory path.",
    )

    parser.add_argument(
        "--database",
        default=os.environ.get("WWG_DATABASE") or os.environ.get("WWG_DATABASE_PATH"),
        help="Override the SQLite database path.",
    )

    parser.add_argument(
        "--derivation-helper",
        default=(
            os.environ.get("WWG_DERIVATION_HELPER")
            or os.environ.get("WWG_DERIVATION_HELPER_PATH")
            or os.environ.get("WWG_DERIVE_PATH")
        ),
        help="Override the Rust derivation helper path.",
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print successful checks as well as failures.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    return run_healthcheck(
        config_path=_resolve_path(args.config),
        data_dir_override=_resolve_path(args.data_dir) if args.data_dir else None,
        database_path_override=_resolve_path(args.database) if args.database else None,
        helper_path_override=_resolve_path(args.derivation_helper) if args.derivation_helper else None,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    raise SystemExit(main())