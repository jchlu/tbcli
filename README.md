# tbcli

A small command-line utility that fetches [TorBox](https://torbox.app) download links for every file in your torrent dashboard and writes them to a text file, one URL per line, for use with a download client. It can also list the torrents currently in your dashboard as a sanity check, or permanently purge all downloads from your dashboard.

Only torrents whose files are currently available are included. Queued, expired, or otherwise unavailable torrents are skipped.

## Requirements

- Python 3.9 or newer (standard library only)
- Git
- A TorBox API key

## Installation

Clone the repository and run the setup script:

```bash
git clone https://github.com/jchlu/tbcli.git
cd tbcli
./install.sh
```

The script copies `tbcli.py` to `~/.local/bin/tbcli` and creates an example config at `~/.config/tbcli/config` (or under `$XDG_CONFIG_HOME` if set). Existing config files are never overwritten. It also records the repository and executable paths so `tbcli -u` can update itself, and warns if the install directory is not on your `PATH`.

Set `TB_BIN_DIR` to install somewhere other than `~/.local/bin`.

Then edit the generated config and set `TB_API` to your TorBox API key. See [Where to find your TorBox API key](#where-to-find-your-torbox-api-key).

### Manual installation

```bash
chmod +x tbcli.py
mkdir -p ~/.local/bin
cp tbcli.py ~/.local/bin/tbcli
```

Make sure `~/.local/bin` is on your `PATH`.

### Updating

`install.sh` records the repository and executable paths in `$XDG_CONFIG_HOME/tbcli/install` (`~/.config/tbcli/install` by default). To pull the latest version and replace the installed executable when new commits are available:

```bash
tbcli -u
```

`-u` is a standalone mode: any other options or output filename are ignored. If you installed manually or moved the repository, set `TB_REPO` to the repository path and optionally `TB_INSTALL_PATH` to the installed executable before running the update.

## Configuration

The API key is read from the `TB_API` key. Sources are checked in this order, highest priority first:

1. An exported `TB_API` environment variable
2. The XDG config file
3. A legacy `.env.local` file in the script directory or current working directory

### Config file location

Following the XDG Base Directory specification:

```
$XDG_CONFIG_HOME/tbcli/config
```

If `XDG_CONFIG_HOME` is unset or is not an absolute path, the default is:

```
~/.config/tbcli/config
```

Use `-c`/`--config FILE` to point at a different file.

### Config file format

The file uses simple `KEY=VALUE` lines. Blank lines and lines starting with `#` are ignored. Surrounding single or double quotes are stripped from values.

```ini
# TorBox API key
TB_API=your_torbox_api_key_here
```

The only required key is `TB_API`. The repository includes `config.example`; `install.sh` copies it to the config location when no config exists.

### Permissions

The config file contains a secret and should not be readable by other users:

```bash
mkdir -p "${XDG_CONFIG_HOME:-$HOME/.config}/tbcli"
install -m 600 /dev/null "${XDG_CONFIG_HOME:-$HOME/.config}/tbcli/config"
$EDITOR "${XDG_CONFIG_HOME:-$HOME/.config}/tbcli/config"
```

Then add the `TB_API=...` line and save. The file should be mode `600`.

### Legacy `.env.local`

For backwards compatibility, `.env.local` is still read from the script directory first, then the current working directory. It uses the same `KEY=VALUE` format. The XDG config file takes precedence over `.env.local`.

### Where to find your TorBox API key

Create or copy an API key from the TorBox account settings page:

<https://torbox.app/settings?section=account>

Do not commit the key to this repository. `.env.local`, the XDG config file, and common secret file patterns are ignored by `.gitignore`.

## Usage

```bash
tbcli [OUTPUT] [options]
```

`OUTPUT` is the file to write links to. If omitted, it defaults to `torbox_links.txt`. You can also use `-o FILE`; specifying both is an error. Use `-s`/`--stdout` to write links to stdout instead of a file.

Examples:

```bash
tbcli links.txt
tbcli -o links.txt
tbcli -g "ubuntu" links.txt
tbcli -c /path/to/config links.txt
tbcli -v links.txt
tbcli --list
tbcli -l -v
tbcli --purge-dashboard
tbcli -u
```

Options:

| Option | Description |
| --- | --- |
| `-o, --output FILE` | Output file (same as the positional argument) |
| `-s, --stdout` | Write links to stdout instead of a file (implied by `--list`) |
| `-l, --list` | List torrents in the dashboard instead of fetching download links; implies `-s`/`--stdout` |
| `--purge-dashboard` | Permanently delete all downloads in the TorBox dashboard after confirmation |
| `-u, --update` | Pull the latest version and reinstall the executable; ignores all other options |
| `-g, --grep PATTERN` | Only include torrents whose name matches a case-insensitive regular expression |
| `-c, --config FILE` | Path to the config file |
| `-e, --env-file FILE` | Path to a legacy `.env.local` file |
| `-b, --base-url URL` | TorBox API base URL (default: `https://api.torbox.app/v1`) |
| `-d, --delay SECONDS` | Delay between download-link requests (default: `0.25`) |
| `-v, --verbose` | Show each torrent/file as it is processed; with `--list`, show files within each torrent |

The default delay keeps request rates comfortably below TorBox's documented limit of 300 requests per minute. Increase it if you still encounter rate limiting.

### Listing torrents

Use `-l`/`--list` to print a table of the torrents currently in your TorBox dashboard without fetching any download links:

```bash
tbcli --list
tbcli -l --grep "ubuntu"
tbcli -l -v
```

The table shows the torrent ID, title, download state, whether the files are present, and the number of files. Add `-v` to list every file within each torrent, including its file ID and size.

`--list` implies `-s`/`--stdout`: the table always goes to stdout, never to a file. It cannot be combined with an output file. The `--grep` filter applies to listing as well.

### Purging the dashboard

Use `--purge-dashboard` to permanently delete all torrents, usenet downloads, web downloads, and queued downloads from your TorBox dashboard:

```bash
tbcli --purge-dashboard
```

This is destructive and cannot be undone. The command asks you to type `purge` before sending any delete requests. It cannot be combined with an output file, `--list`, `--stdout`, or `--grep`; use the normal `-c`, `-e`, and `-b` options to select the API configuration.

TorBox's documented delete endpoints do not accept a confirmation code. They delete everything when called with `{"operation": "delete", "all": true}`, so tbcli performs the confirmation locally. The confirmation shown by the TorBox dashboard is a UI safeguard, not an API requirement. The account-level `GET /api/user/getconfirmation` endpoint is separate and is not used here.

### Filtering torrents

Use `-g`/`--grep` to limit the run to torrents whose name matches a case-insensitive regular expression. This is useful when old torrents are still in the dashboard but you only want a specific set of files:

```bash
tbcli --grep "ubuntu" links.txt
tbcli -g "S[0-9]{2}E[0-9]{2}" --stdout | aria2c -i -
```

Matching applies to torrent names only. Quote patterns that contain shell metacharacters. Non-matching torrents are counted in the summary as filtered.

### Piping to a download client

Use `-s`/`--stdout` to write only the links to stdout, leaving progress and the summary on stderr so the output can be piped directly. For example, [aria2c](https://aria2.github.io/) accepts newline-separated URLs on stdin with `-i -`:

```bash
tbcli -s | aria2c -i -
```

aria2c is an external dependency and is not installed by `install.sh`. Install it separately with your package manager, for example on Debian/Ubuntu:

```bash
sudo apt install aria2
```

The same pattern works with any download client that accepts newline-separated URLs from stdin or a file. TorBox links expire after roughly 3 hours, so start the download promptly.

## Output

- One download URL per line.
- Only files from torrents with `download_present` set to `true` (or, as a fallback, `download_finished`/a completed state) are requested.
- TorBox CDN links expire after roughly 3 hours and must be started before then. Generate them shortly before handing the file to your download client.
- Failed file requests are reported on stderr and do not stop the rest of the run.

## Exit status

| Code | Meaning |
| --- | --- |
| `0` | All requested links were written, or the purge completed successfully |
| `1` | One or more file or purge requests failed, the output file could not be written, or the purge was cancelled |
| `2` | Configuration error (missing `TB_API`, missing explicit config/env file) |
| `130` | Interrupted with Ctrl-C |

## Development

```bash
python3 -m py_compile tbcli.py
ruff check tbcli.py
```

## License

No license has been specified yet.
