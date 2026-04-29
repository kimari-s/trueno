# Trueno — minimal CGI file host

> Live deployment: <https://a.6umi.net/> renders this repo's `openapi.json`
> through the shipped LP and Swagger UI — useful as a working sample
> before you stand up your own.

A small file host for shared hosting environments where Apache CGI is your
only execution surface and a pip install is awkward. POST a multipart file
with a Bearer token, get back a short URL. Each upload picks a **TTL bucket**
(`1h` / `1d` / `1w` / `keep`); the time-limited buckets are swept by cron,
`keep` is exempt and earns a privileged short URL form. Inspired by
[LitterBox](https://litterbox.catbox.moe/) / [catbox](https://catbox.moe/);
positioned as the cgi-bin counterpart to [0x0.st](https://0x0.st/) (which
assumes a long-running daemon).

```
client → POST host/upload  (file=@..., time=1h|1d|1w|keep)
            ├─ files/{bucket}/{id}.{ext}    (4-char base62 for 1h/1d/1w, 6-char for keep)
            └─ JSON {url, size, time, expires_at}
client ← GET  host/{bucket}/{id}.{ext}      (1h | 1d | 1w; 4-char id; Apache static rewrite)
client ← GET  host/{id}.{ext}               (keep; 6-char id; privileged short URL)
cron   →     find files/{1h,1d,1w}/ -mmin +TTL -delete   (every 15min)
```

URL length itself signals the bucket category — short URLs to `/<id>` are
the `keep` namespace (long, stable), bucketed `/{1h|1d|1w}/<id>` URLs are
explicitly time-coded.

Stdlib-only — no Flask, no werkzeug, no pip install. Tested on Python 3.6.8
through 3.13. Designed for `mod_cgi`-style shared hosts where you upload a
script via SFTP and Apache executes it on demand; battle-tested as a
LitterBox replacement when you already pay for shared hosting and would
rather not also pay for an object store.

## Layout

```
docroot/           deploys to your Apache document root
├── upload.cgi     POST /upload handler
├── info.cgi       GET /info — runtime limits as JSON
├── _trueno.py     shared constants (denied to clients via .htaccess)
├── .htaccess      ExecCGI + rewrites + Indexes off
├── index.html     LP that loads Swagger UI from unpkg
├── openapi.json   API spec (rendered by index.html)
└── files/         created on first upload (gitignored)
cron/
└── expire.sh      per-bucket sweep (delete by default; supports archive-mode)
tests/
├── test_upload.py end-to-end via subprocess + synthesized CGI env
└── test_info.py   info endpoint smoke test
```

## Deploy

### 1. Provision the host

You need:

- An Apache vhost serving HTTPS (a subdomain or path is fine).
- Document root mapped to a writable directory; mod_cgi enabled so `.cgi`
  files execute. On most shared hosting providers `.htaccess` overrides
  for `Options +ExecCGI` and `AddHandler cgi-script .cgi` are permitted by
  default — verify in your host's docs.
- SSH/SFTP access for `rsync` deploys.
- Python 3.6+ on the cgi PATH. The shebang line is `#!/usr/local/bin/python3`;
  edit it once if your host installs Python elsewhere.

Add a host alias to `~/.ssh/config` so deploys stay terse:

```
Host trueno-host
    HostName your.host.example.com
    User you
    ServerAliveInterval 60
```

### 2. Generate the bearer token(s)

Single key (simplest):

```
KEY=$(openssl rand -hex 32)
echo "$KEY" | ssh trueno-host 'cat > ~/.trueno-key && chmod 600 ~/.trueno-key'
echo "ASSET_API_KEY=$KEY"   # save in your password manager
```

The CGI reads keys from `~/.trueno-key` (chmod 600, outside docroot) or,
for local testing, from the `ASSET_API_KEY` env var. Override the file
location with `ASSET_KEY_FILE` if needed.

**Multiple keys** are also supported — one token per line, with optional
`# comments`. Useful for handing distinct tokens to a small group of users
so any one can be revoked without disrupting the others:

```
# server-to-server (full access)
f685414bedf09f4dc...

# personal — sharex (full access)
b7c2e9a1f3d8...

# friend-A (full access)
def123abc...

# public board key — time-limited buckets only, can't claim keep
ghi789... no-keep

# embed-only key — explicit allow-list (no keep, no day, no week)
mno456... buckets:1h
```

Each line: `<token> [no-keep] [buckets:<csv>]`.

- No flags → full access (backward compatible).
- `no-keep` → all buckets except `keep`.
- `buckets:<csv>` → explicit allow-list (e.g., `buckets:1h,1d`); overrides
  `no-keep` if both are present.

Requests for a bucket the key isn't allowed to write get **403** with a
detail message listing the allowed buckets. Add a key by appending a
line; revoke by deleting it. No redeploy required — the CGI reads the
file every request.

### 3. Push the docroot

```
make deploy-dry DEPLOY_HOST=trueno-host DEPLOY_PATH=public_html/your-domain
make deploy     DEPLOY_HOST=trueno-host DEPLOY_PATH=public_html/your-domain
```

Internally that runs:

```
rsync -avz --delete --exclude 'files/' docroot/ trueno-host:public_html/your-domain/
ssh trueno-host 'chmod 755 public_html/your-domain/upload.cgi && mkdir -p public_html/your-domain/files'
```

`--exclude 'files/'` is critical — never wipe the live storage dir.

### 4. Configure the public URL base

`docroot/.htaccess` ships with `SetEnv ASSET_PUBLIC_URL "https://a.example.test"`;
edit it to your real public origin so the JSON response carries the right
absolute URL.

### 5. Install the cron sweep

```
ssh trueno-host 'mkdir -p ~/cron'
scp cron/expire.sh trueno-host:~/cron/expire.sh
ssh trueno-host 'chmod +x ~/cron/expire.sh'
ssh trueno-host '(crontab -l 2>/dev/null; echo "*/15 * * * * \$HOME/cron/expire.sh >> \$HOME/cron/expire.log 2>&1") | crontab -'
ssh trueno-host 'crontab -l'   # verify
```

If your docroot is not at `~/public_html/<your-domain>`, override `DOCROOT`
in `expire.sh` or in the cron line.

### 6. Smoke test

```
KEY=$(ssh trueno-host cat ~/.trueno-key)
echo hello > /tmp/x.txt

# default → 1h bucket; 4-char id under /1h/
curl -s -H "Authorization: Bearer $KEY" -F file=@/tmp/x.txt https://your.host/upload
# → {"url": "https://your.host/1h/AbCd.txt", "size": 6, "time": "1h", "expires_at": 1234567890}

# explicit time=keep → 6-char id, no /<bucket>/ prefix, expires_at is null
curl -s -H "Authorization: Bearer $KEY" -F file=@/tmp/x.txt -F time=keep https://your.host/upload
# → {"url": "https://your.host/AbCdEf.txt", "size": 6, "time": "keep", "expires_at": null}
```

## Operating notes

| Task | Command |
|---|---|
| List uploaded files (per bucket) | `ssh trueno-host 'ls -lh public_html/your-domain/files/keep/ \| tail -20'` |
| Download one (keep bucket) | `scp trueno-host:public_html/your-domain/files/keep/AbCd.jpg ~/Downloads/` |
| Backup all | `rsync -avz trueno-host:public_html/your-domain/files/ ./backup/$(date +%F)/` |
| Force delete one | `ssh trueno-host rm public_html/your-domain/files/keep/AbCd.jpg` |
| Wipe expired now | `ssh trueno-host '~/cron/expire.sh'` |
| Tail expire log | `ssh trueno-host tail -f cron/expire.log` |

### Archive mode + restore

Set `EXPIRE_ACTION=move:<dir>` in the crontab line to relocate expired files
into `<dir>/<bucket>/` instead of deleting — useful for accidental-delete
recovery:

```
*/15 * * * * EXPIRE_ACTION=move:/home/me/trueno-archive $HOME/cron/expire.sh >> $HOME/cron/expire.log 2>&1
```

To restore a single file from the archive, move it back and `touch` the mtime
so the next cron sweep doesn't immediately re-archive it:

```
# same bucket → URL is preserved (TTL clock resets)
ssh trueno-host 'mv ~/trueno-archive/1h/AbCd.jpg ~/public_html/your-domain/files/1h/ && touch $_'

# promote to keep → URL becomes the short form (/AbCd.jpg), no touch needed
ssh trueno-host 'mv ~/trueno-archive/1h/AbCd.jpg ~/public_html/your-domain/files/keep/'
```

## Knobs

| Setting | Default | Where |
|---|---|---|
| Bearer token(s) | (required) | `~/.trueno-key` (chmod 600, one per line, `# comments` ok) or `ASSET_API_KEY` env |
| Public URL base | `https://a.example.test` | `SetEnv ASSET_PUBLIC_URL` in `.htaccess`; `make deploy` substitutes via `PUBLIC_URL_BASE` |
| Site title (browser tab + OpenAPI `info.title`) | `🔱Trueno` | `SITE_TITLE` in `make.local`; `make deploy` substitutes into `index.html` and `openapi.json` |
| Web UI URL (surfaced in `/info` and rendered into the LP's Endpoints list) | `https://ui.example.test` (placeholder) | `UI_URL` in `make.local`; `make deploy` substitutes the placeholder in `_trueno.py`. Set this to your front-end URL so visitors of the API page can find the UI; leave default if you don't host a separate UI |
| Allowed extensions | common image / document / video / audio / archive (jpg, png, webp, svg, pdf, txt, md, json, mp4, mov, mp3, wav, zip, tar.gz, 7z, …) | `ALLOWED_EXTS` in `_trueno.py` |
| Max body | 50 MiB | `MAX_BYTES` in `_trueno.py` (Apache `LimitRequestBody` must permit it) |
| ID length | `keep` = 6 chars (62^6 ≈ 5.7e10), `1h`/`1d`/`1w` = 4 chars (62^4 ≈ 14.8M each, per-bucket namespace) | `_ID_LEN_BY_BUCKET` in `_trueno.py` |
| TTL buckets | `1h=3600` / `1d=86400` / `1w=604800` / `keep=null` | `BUCKETS` in `_trueno.py` (must mirror minutes in `cron/expire.sh`) |
| Default TTL on unspecified `time` | `1h` | `DEFAULT_TIME` in `_trueno.py` |
| Cron action | `delete` | `EXPIRE_ACTION` env on the cron line — `delete` or `move:<archive_dir>` |
| Rate limit window | 600s (10 min) | `ASSET_RATE_WINDOW_SEC` env |
| Rate limit max | 30 uploads / window (host-wide, summed across buckets) | `ASSET_RATE_MAX_ITEMS` env |

## TTL buckets

Each upload chooses a bucket via the `time` form field; the value also
shapes the public URL:

| Bucket | TTL | URL form | Notes |
|---|---|---|---|
| `1h`   | 1 hour    | `<host>/1h/<id>.<ext>` | Default if `time` is unspecified |
| `1d`   | 1 day     | `<host>/1d/<id>.<ext>` | |
| `1w`   | 1 week    | `<host>/1w/<id>.<ext>` | |
| `keep` | no expiry | `<host>/<id>.<ext>`    | Privileged short URL form; exempt from cron |

URL re-use after a TTL'd bucket sweep is part of the contract — once
`expires_at` passes, the slot may be reassigned to a different upload, so
recipients shouldn't bookmark short-lived URLs. Long-lived references
should always use `keep` (or be re-uploaded into `keep` explicitly).

Each bucket has its own ID namespace, and `keep` — the bucket where URL
stability matters long-term — uses a longer **6-char** ID (62^6 ≈ 5.7×10¹⁰
slots) while the short-lived buckets use 4-char (62^4 ≈ 1.5×10⁷). The URL
length thus encodes the category at a glance.

## Runtime info endpoint

`GET /info` returns the live configuration as JSON — useful for
programmatic clients that want to discover the actual limits without
parsing the OpenAPI spec. The LP itself just renders the static
`openapi.json` via Swagger UI; runtime values that need to surface in the
spec (like `servers[].url`) are baked at deploy time via `make stage`.

```json
{
  "public_url": "https://your.host.example",
  "max_bytes": 52428800,
  "allowed_exts": [".7z", ".avif", ".bmp", "..."],
  "ttl_choices": {"1h": 3600, "1d": 86400, "1w": 604800, "keep": null},
  "default_time": "1h",
  "rate_window_sec": 600,
  "rate_max_items": 30,
  "id_len_by_bucket": {"1h": 4, "1d": 4, "1w": 4, "keep": 6}
}
```

The endpoint is unauthenticated; only configured limits are exposed, never
key material or upload state.

## Rate limiting

Trueno keeps no counter file. The rate limit just counts entries across all
bucket dirs (`files/{1h,1d,1w,keep}/`) whose mtime falls inside
`ASSET_RATE_WINDOW_SEC`; once `ASSET_RATE_MAX_ITEMS` is reached, further
requests return `429`. Cron's expire sweep drains the time-limited buckets
naturally, so the limiter is self-resetting (the `keep` bucket also counts,
but its share grows much more slowly in practice).

It's **host-wide, not per-key** — fine for small private deployments where
abuse would mean a leaked token. If you need per-key quotas, add an SQLite
counter alongside the file write; the existing structure leaves room.

## ShareX

Trueno's POST contract is a drop-in for [ShareX](https://getsharex.com/)'s
custom uploader format. See [`examples/sharex/`](examples/sharex/) for an
importable `.sxcu` template and setup notes.

## Local dev

```
uv venv && source .venv/bin/activate
uv pip install -e '.[dev]' --group dev
make test     # pytest, runs upload.cgi as a subprocess
make lint     # ruff + pyright
make fmt      # ruff format
```

The test harness invokes `upload.cgi` as a subprocess with synthesized CGI
env vars; no Apache, Flask, or werkzeug needed locally.

## License

MIT — see [`LICENSE`](LICENSE).
