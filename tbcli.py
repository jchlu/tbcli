#!/usr/bin/env python3
"""Fetch TorBox download links for all available torrent files."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DEFAULT_BASE_URL = "https://api.torbox.app/v1"
DEFAULT_OUTPUT = "torbox_links.txt"
DEFAULT_LIMIT = 1000
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


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key:
            values[key] = value
    return values


def load_env_file(path: Path) -> None:
    for key, value in parse_env_file(path).items():
        if not os.environ.get(key):
            os.environ[key] = value


def install_state_path() -> Path:
    return config_path().with_name("install")


def git_output(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        check=False,
    )


def git_head(repo: Path) -> str:
    result = git_output(repo, "rev-parse", "HEAD")
    if result.returncode != 0:
        return ""
    return result.stdout.decode("utf-8", "replace").strip()


def update_installation() -> int:
    if shutil.which("git") is None:
        print("error: git is required for --update", file=sys.stderr)
        return 2

    state = parse_env_file(install_state_path())
    repo_value = os.environ.get("TB_REPO") or state.get("TB_REPO")
    if not repo_value:
        candidate = Path(__file__).resolve().parent
        if (candidate / ".git").exists():
            repo_value = str(candidate)
    if not repo_value:
        print(
            "error: cannot locate the source repository; re-run install.sh or set TB_REPO",
            file=sys.stderr,
        )
        return 2

    repo = Path(repo_value).expanduser().resolve()
    if not (repo / ".git").exists():
        print(f"error: not a git repository: {repo}", file=sys.stderr)
        return 2

    target_value = os.environ.get("TB_INSTALL_PATH") or state.get("TB_INSTALL_PATH")
    if target_value:
        target = Path(target_value).expanduser()
    else:
        running = Path(sys.argv[0]).resolve()
        if running.parent == repo:
            target = Path.home() / ".local" / "bin" / "tbcli"
        else:
            target = running

    before = git_head(repo)
    if not before:
        print("error: cannot determine current git revision", file=sys.stderr)
        return 1
    result = subprocess.run(
        ["git", "-C", str(repo), "pull", "--ff-only"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        if detail:
            print(detail, file=sys.stderr)
        print("error: git pull failed", file=sys.stderr)
        return 1

    after = git_head(repo)
    if not after:
        print("error: cannot determine updated git revision", file=sys.stderr)
        return 1
    if before == after:
        print("Already up to date.")
        return 0

    source = git_output(repo, "show", "HEAD:tbcli.py")
    if source.returncode != 0:
        print("error: cannot read tbcli.py from the updated repository", file=sys.stderr)
        return 1
    try:
        compile(source.stdout, "tbcli.py", "exec")
    except SyntaxError as exc:
        print(f"error: updated tbcli.py is invalid: {exc}", file=sys.stderr)
        return 1

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".tbcli-", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(source.stdout)
            os.chmod(temp_name, 0o755)
            os.replace(temp_name, target)
        except BaseException:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise
    except OSError as exc:
        print(f"error: cannot update {target}: {exc}", file=sys.stderr)
        return 1

    print(f"Updated {target} to {after[:7]}.")
    return 0


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
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> Any:
    if params:
        url = f"{url}?{urlencode(params)}"
    data = None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "tbcli/1.0",
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method=method)

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


def confirm_purge() -> bool:
    print(
        "This will permanently delete all torrents, usenet downloads, web downloads, "
        "and queued downloads from your TorBox dashboard.",
        file=sys.stderr,
    )
    print("Type 'purge' to continue: ", end="", file=sys.stderr, flush=True)
    try:
        answer = input()
    except EOFError:
        print("\nPurge cancelled.", file=sys.stderr)
        return False
    except KeyboardInterrupt:
        print("\nPurge cancelled.", file=sys.stderr)
        return False
    if not sys.stdin.isatty():
        print(file=sys.stderr)
    if answer.strip() != "purge":
        print("Purge cancelled.", file=sys.stderr)
        return False
    return True


def purge_dashboard(args: argparse.Namespace, token: str) -> int:
    targets = [
        ("queued downloads", f"{args.base_url}/api/queued/controlqueued"),
        ("torrents", f"{args.base_url}/api/torrents/controltorrent"),
        ("usenet downloads", f"{args.base_url}/api/usenet/controlusenetdownload"),
        ("web downloads", f"{args.base_url}/api/webdl/controlwebdownload"),
    ]
    failed = 0
    try:
        for label, url in targets:
            try:
                request_json(
                    url,
                    token,
                    method="POST",
                    payload={"operation": "delete", "all": True},
                )
            except ApiError as exc:
                failed += 1
                print(f"warning: failed to purge {label}: {exc}", file=sys.stderr)
                continue
            print(f"Purged {label}.", file=sys.stderr)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130

    if failed:
        print(f"Purge finished with {failed} failure(s).", file=sys.stderr)
        return 1
    print("TorBox dashboard purged.", file=sys.stderr)
    return 0


def human_size(size: Any) -> str:
    try:
        value = float(size)
    except (TypeError, ValueError):
        return ""
    units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return ""


def availability_label(torrent: dict[str, Any]) -> str:
    if "download_present" in torrent:
        return "yes" if torrent.get("download_present") else "no"
    if torrent.get("download_finished") is True:
        return "yes"
    return "-"


def print_torrent_table(torrents: list[dict[str, Any]], verbose: bool) -> None:
    headers = ("ID", "TITLE", "STATE", "PRESENT", "FILES")
    rows = []
    for torrent in torrents:
        files = torrent.get("files")
        if not isinstance(files, list):
            files = []
        rows.append(
            (
                str(torrent.get("id", "?")),
                torrent.get("name") or f"id={torrent.get('id')}",
                torrent.get("download_state") or "-",
                availability_label(torrent),
                str(len(files)),
            )
        )

    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    print("  ".join(cell.ljust(widths[index]) for index, cell in enumerate(headers)).rstrip())
    for torrent, row in zip(torrents, rows):
        print("  ".join(cell.ljust(widths[index]) for index, cell in enumerate(row)).rstrip())
        if not verbose:
            continue
        files = torrent.get("files")
        if not isinstance(files, list):
            continue
        for file_info in files:
            if not isinstance(file_info, dict):
                continue
            file_id = file_info.get("id", "?")
            file_name = file_info.get("short_name") or file_info.get("name") or "?"
            size = human_size(file_info.get("size"))
            suffix = f" ({size})" if size else ""
            print(f"    [{file_id}] {file_name}{suffix}")


def list_torrents(args: argparse.Namespace, token: str, grep_pattern: re.Pattern[str] | None) -> int:
    torrents: list[dict[str, Any]] = []
    filtered_count = 0

    try:
        for torrent in fetch_torrents(args.base_url, token, DEFAULT_LIMIT):
            torrent_name = torrent.get("name") or f"id={torrent.get('id')}"
            if grep_pattern and not grep_pattern.search(torrent_name):
                filtered_count += 1
                continue
            torrents.append(torrent)
    except ApiError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130

    if not torrents:
        message = "No torrents found."
        if filtered_count:
            message += f" ({filtered_count} torrents filtered)"
        print(message, file=sys.stderr)
        return 0

    print_torrent_table(torrents, args.verbose)
    suffix = f" ({filtered_count} torrents filtered)" if filtered_count else ""
    print(f"Listed {len(torrents)} torrents{suffix}", file=sys.stderr)
    return 0


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
    parser.add_argument("-s", "--stdout", action="store_true", help="write links to stdout instead of a file")
    parser.add_argument("-l", "--list", action="store_true", help="list torrents instead of fetching download links")
    parser.add_argument(
        "--purge-dashboard",
        action="store_true",
        help="permanently delete all downloads from the TorBox dashboard after confirmation",
    )
    parser.add_argument(
        "-u",
        "--update",
        action="store_true",
        help="pull the latest version and reinstall the executable; ignores all other options",
    )
    parser.add_argument(
        "-g",
        "--grep",
        metavar="PATTERN",
        help="only include torrents whose name matches this case-insensitive regular expression",
    )
    parser.add_argument("-e", "--env-file", help="path to .env.local (default: script dir, then cwd)")
    parser.add_argument("-c", "--config", help="path to config file (default: $XDG_CONFIG_HOME/tbcli/config)")
    parser.add_argument("-b", "--base-url", default=DEFAULT_BASE_URL, help=f"TorBox API base URL (default: {DEFAULT_BASE_URL})")
    parser.add_argument(
        "-d",
        "--delay",
        type=float,
        default=0.25,
        help="seconds between download-link requests (default: 0.25)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="show each file as it is processed; with --list, show files within each torrent",
    )
    args = parser.parse_args()
    if args.update:
        return args
    if args.purge_dashboard:
        if args.output or args.output_flag or args.list or args.stdout or args.grep:
            parser.error(
                "--purge-dashboard cannot be combined with output, list, stdout, or grep options"
            )
        return args
    if args.output and args.output_flag:
        parser.error("specify the output file either positionally or with -o, not both")
    if args.stdout and (args.output or args.output_flag):
        parser.error("-s/--stdout cannot be combined with an output file")
    if args.list and (args.output or args.output_flag):
        parser.error("--list implies -s/--stdout and cannot be combined with an output file")
    if args.list:
        args.stdout = True
    args.output = None if args.stdout else (args.output or args.output_flag or DEFAULT_OUTPUT)
    return args


def main() -> int:
    args = parse_args()

    if args.update:
        return update_installation()

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

    if args.purge_dashboard:
        if not confirm_purge():
            return 1
        return purge_dashboard(args, token)

    try:
        grep_pattern = re.compile(args.grep, re.IGNORECASE) if args.grep else None
    except re.error as exc:
        print(f"error: invalid --grep pattern: {exc}", file=sys.stderr)
        return 2

    if args.list:
        return list_torrents(args, token, grep_pattern)

    output_path = None if args.stdout else Path(args.output).expanduser()
    links: list[str] = []
    torrent_count = 0
    filtered_count = 0
    skipped_count = 0
    failed_count = 0

    try:
        torrents = fetch_torrents(args.base_url, token, DEFAULT_LIMIT)
        for torrent in torrents:
            torrent_name = torrent.get("name") or f"id={torrent.get('id')}"
            torrent_id = torrent.get("id")

            if grep_pattern and not grep_pattern.search(torrent_name):
                filtered_count += 1
                if args.verbose:
                    print(f"skip (no grep match): {torrent_name}", file=sys.stderr)
                continue

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

    summary = []
    if filtered_count:
        summary.append(f"{filtered_count} torrents filtered")
    if skipped_count:
        summary.append(f"{skipped_count} torrents skipped")
    if failed_count:
        summary.append(f"{failed_count} files failed")
    suffix = f" ({', '.join(summary)})" if summary else ""

    print(
        f"Wrote {len(links)} links from {torrent_count} torrents to {destination}{suffix}",
        file=sys.stderr,
    )
    return 1 if failed_count else 0


if __name__ == "__main__":
    sys.exit(main())
