# tbcli

A small command-line utility that fetches [TorBox](https://torbox.app) download links for every file in your torrent dashboard and writes them to a text file, one URL per line, for use with a download client.

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

The script copies `tbcli.py` to `~/.local/bin/tbcli` and creates an example config at `~/.config/tbcli/config` (or under `$XDG_CONFIG_HOME` if set). Existing config files are never overwritten. It also warns if the install directory is not on your `PATH`.

Set `TB_BIN_DIR` to install somewhere other than `~/.local/bin`.

Then edit the generated config and set `TB_API` to your TorBox API key. See [Where to find your TorBox API key](#where-to-find-your-torbox-api-key).

### Manual installation

```bash
chmod +x tbcli.py
mkdir -p ~/.local/bin
cp tbcli.py ~/.local/bin/tbcli
```

Make sure `~/.local/bin` is on your `PATH`.

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

Use `--config FILE` to point at a different file.

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

`OUTPUT` is the file to write links to. If omitted, it defaults to `torbox_links.txt`. You can also use `-o FILE`; specifying both is an error.

Examples:

```bash
tbcli links.txt
tbcli -o links.txt
tbcli --config /path/to/config links.txt
tbcli -v links.txt
```

Options:

| Option | Description |
| --- | --- |
| `-o, --output FILE` | Output file (same as the positional argument) |
| `--config FILE` | Path to the config file |
| `--env-file FILE` | Path to a legacy `.env.local` file |
| `--base-url URL` | TorBox API base URL (default: `https://api.torbox.app/v1`) |
| `--limit N` | Torrents requested per API page (default: `1000`) |
| `--delay SECONDS` | Delay between download-link requests (default: `0.25`) |
| `-v, --verbose` | Show each torrent/file as it is processed |

The default delay keeps request rates comfortably below TorBox's documented limit of 300 requests per minute. Increase it if you still encounter rate limiting.

## Output

- One download URL per line.
- Only files from torrents with `download_present` set to `true` (or, as a fallback, `download_finished`/a completed state) are requested.
- TorBox CDN links expire after roughly 3 hours and must be started before then. Generate them shortly before handing the file to your download client.
- Failed file requests are reported on stderr and do not stop the rest of the run.

## Exit status

| Code | Meaning |
| --- | --- |
| `0` | All requested links were written |
| `1` | One or more file requests failed, or the output file could not be written |
| `2` | Configuration error (missing `TB_API`, missing explicit config/env file) |
| `130` | Interrupted with Ctrl-C |

## Development

```bash
python3 -m py_compile tbcli.py
ruff check tbcli.py
```

## License

No license has been specified yet.
