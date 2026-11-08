#!/usr/bin/env python3
"""Fetch TorBox download links for all available torrent files."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DEFAULT_BASE_URL = "https://api.torbox.app/v1"
DEFAULT_OUTPUT = "torbox_links.txt"
APP_NAME = "tbcli"
CONFIG_FILENAME = "config"
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class ApiError(Exception):
    pass


def xdg_config_home() -> Path:
    value = os.environ.get("XDG_CONFIG_HOME", "")
    if value and Path(value).is_absolute():
        return Path(value)
    return Path.home() / ".config"


def config_path() -> Path:
    return xdg_config_home() / APP_NAME / CONFIG_FILENAME


def load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and not os.environ.get(key):
            os.environ[key] = value


def find_config_file(explicit: str | None) -> Path | None:
    if explicit:
        path = Path(explicit).expanduser()
        return path if path.is_file() else None
    path = config_path()
    return path if path.is_file() else None


def find_env_file(explicit: str | None) -> Path | None:
    if explicit:
        path = Path(explicit).expanduser()
        return path if path.is_file() else None
    candidates = [Path(__file__).resolve().parent / ".env.local", Path.cwd() / ".env.local"]
    for path in candidates:
        if path.is_file():
            return path
    return None


def request_json(
    url: str,
    token: str,
    params: dict[str, Any] | None = None,
    retries: int = 3,
    timeout: float = 60.0,
) -> Any:
    if params:
        url = f"{url}?{urlencode(params)}"
    request = Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "tbcli/1.0",
        },
    )

    for attempt in range(retries + 1):
        try:
            with urlopen(request, timeout=timeout) as response:
                payload = json.load(response)
        except HTTPError as exc:
            if exc.code in RETRYABLE_STATUS and attempt < retries:
                delay = retry_delay(exc, attempt)
                print(f"  retrying after HTTP {exc.code} in {delay:.1f}s", file=sys.stderr)
                time.sleep(delay)
                continue
            raise ApiError(f"HTTP {exc.code} {exc.reason}") from None
        except URLError as exc:
            if attempt < retries:
                delay = 2**attempt
                print(f"  retrying after network error in {delay}s", file=sys.stderr)
                time.sleep(delay)
                continue
            raise ApiError(f"network error: {exc.reason}") from None
        except json.JSONDecodeError:
            raise ApiError("server returned invalid JSON") from None

        if not isinstance(payload, dict):
            raise ApiError("unexpected API response")
        if payload.get("success") is False:
            detail = payload.get("detail") or payload.get("error") or "unknown API error"
            raise ApiError(str(detail))
        return payload.get("data")

    raise ApiError("request failed after retries")


def retry_delay(exc: HTTPError, attempt: int) -> float:
    header = exc.headers.get("Retry-After") if exc.headers else None
    if header:
        try:
            return max(0.0, float(header))
        except ValueError:
            pass
    return float(2**attempt)


def fetch_torrents(base_url: str, token: str, limit: int) -> Iterator[dict[str, Any]]:
    offset = 0
    seen: set[Any] = set()
    while True:
        data = request_json(
            f"{base_url}/api/torrents/mylist",
            token,
            {"offset": offset, "limit": limit},
        )
        if data is None:
            return
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list) or not data:
            return

        new_items = 0
        for torrent in data:
            if not isinstance(torrent, dict):
                continue
            torrent_id = torrent.get("id")
            if torrent_id in seen:
                continue
            seen.add(torrent_id)
            new_items += 1
            yield torrent

        if len(data) < limit or new_items == 0:
            return
        offset += len(data)


def is_downloadable(torrent: dict[str, Any]) -> bool:
    if "download_present" in torrent:
        return torrent.get("download_present") is True
    if torrent.get("download_finished") is True:
        return True
    return torrent.get("download_state") in {"completed", "cached", "uploading"}


def request_download_link(base_url: str, token: str, torrent_id: Any, file_id: Any) -> str:
    data = request_json(
        f"{base_url}/api/torrents/requestdl",
        token,
        {"token": token, "torrent_id": torrent_id, "file_id": file_id},
    )
    if not isinstance(data, str) or not data:
        raise ApiError("response did not contain a download link")
    return data


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Write TorBox download links for all available torrent files, one per line."
    )
    parser.add_argument(
        "output",
        nargs="?",
        help=f"output file (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument("-o", "--output", dest="output_flag", metavar="FILE", help="output file (same as positional)")
    parser.add_argument("--stdout", action="store_true", help="write links to stdout instead of a file")
    parser.add_argument("--env-file", help="path to .env.local (default: script dir, then cwd)")
    parser.add_argument("--config", help="path to config file (default: $XDG_CONFIG_HOME/tbcli/config)")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help=f"TorBox API base URL (default: {DEFAULT_BASE_URL})")
    parser.add_argument("--limit", type=int, default=1000, help="torrents per API page (default: 1000)")
    parser.add_argument(
        "--delay",
        type=float,
        default=0.25,
        help="seconds between download-link requests (default: 0.25)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="show each file as it is processed")
    args = parser.parse_args()
    if args.output and args.output_flag:
        parser.error("specify the output file either positionally or with -o, not both")
    if args.stdout and (args.output or args.output_flag):
        parser.error("--stdout cannot be combined with an output file")
    args.output = None if args.stdout else (args.output or args.output_flag or DEFAULT_OUTPUT)
    return args


def main() -> int:
    args = parse_args()

    config_file = find_config_file(args.config)
    if config_file:
        load_env_file(config_file)
    elif args.config:
        print(f"error: config file not found: {args.config}", file=sys.stderr)
        return 2

    env_file = find_env_file(args.env_file)
    if env_file:
        load_env_file(env_file)
    elif args.env_file:
        print(f"error: env file not found: {args.env_file}", file=sys.stderr)
        return 2

    token = os.environ.get("TB_API", "").strip()
    if not token:
        print(
            "error: TB_API is not set (expected in the environment, "
            f"{config_path()}, or .env.local)",
            file=sys.stderr,
        )
        return 2

    output_path = None if args.stdout else Path(args.output).expanduser()
    links: list[str] = []
    torrent_count = 0
    skipped_count = 0
    failed_count = 0

    try:
        torrents = fetch_torrents(args.base_url, token, args.limit)
        for torrent in torrents:
            torrent_name = torrent.get("name") or f"id={torrent.get('id')}"
            torrent_id = torrent.get("id")

            if not is_downloadable(torrent):
                skipped_count += 1
                if args.verbose:
                    print(f"skip (not available): {torrent_name}", file=sys.stderr)
                continue

            files = torrent.get("files")
            if not isinstance(files, list) or not files:
                skipped_count += 1
                if args.verbose:
                    print(f"skip (no files): {torrent_name}", file=sys.stderr)
                continue

            torrent_count += 1
            for file_info in files:
                if not isinstance(file_info, dict):
                    continue
                file_id = file_info.get("id")
                if file_id is None:
                    continue
                file_name = file_info.get("short_name") or file_info.get("name") or f"id={file_id}"
                if args.verbose:
                    print(f"{torrent_name} / {file_name}", file=sys.stderr)
                try:
                    links.append(request_download_link(args.base_url, token, torrent_id, file_id))
                except ApiError as exc:
                    failed_count += 1
                    print(f"warning: failed {torrent_name} / {file_name}: {exc}", file=sys.stderr)
                if args.delay > 0:
                    time.sleep(args.delay)
    except ApiError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130

    if args.stdout:
        try:
            for link in links:
                print(link)
            sys.stdout.flush()
        except BrokenPipeError:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
            os.close(devnull)
            return 1
        destination = "stdout"
    else:
        assert output_path is not None
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with output_path.open("w", encoding="utf-8") as handle:
                for link in links:
                    handle.write(link + "\n")
        except OSError as exc:
            print(f"error: cannot write {output_path}: {exc}", file=sys.stderr)
            return 1
        destination = str(output_path)

    print(
        f"Wrote {len(links)} links from {torrent_count} torrents to {destination}"
        + (f" ({skipped_count} torrents skipped, {failed_count} files failed)" if skipped_count or failed_count else ""),
        file=sys.stderr,
    )
    return 1 if failed_count else 0


if __name__ == "__main__":
    sys.exit(main())
