import argparse
import os
from pathlib import Path


def worker_token(parser: argparse.ArgumentParser) -> tuple[argparse.Namespace, str]:
    """Parse runner arguments and resolve the bearer without printing it."""
    parser.add_argument("--token-file", type=Path, default=os.getenv("WORKER_TOKEN_FILE") or None)
    args = parser.parse_args()
    token = os.getenv("WORKER_TOKEN", "").strip()
    if not token and args.token_file:
        try:
            token = args.token_file.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError) as exc:
            parser.error(f"Cannot read worker token file {args.token_file}: {exc.__class__.__name__}")
    if not token:
        parser.error("Set WORKER_TOKEN or supply a nonempty --token-file (WORKER_TOKEN_FILE).")
    return args, token
