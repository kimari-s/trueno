# ShareX integration

[ShareX](https://getsharex.com/) (Windows) imports custom uploader configs in
the `.sxcu` format. Trueno's POST contract matches the format directly — no
server-side change is needed.

## Setup

1. Open `trueno.sxcu` and replace:
   - `https://your.host.example/upload` → your real `/upload` URL
   - `YOUR_KEY` → a bearer token issued out-of-band
   - (optional) `Arguments.time` → `1h` / `1d` / `1w` / `keep` to change the
     default TTL bucket. The shipped value `1w` is a sensible middle ground
     for casual screenshot share-links; switch to `keep` if you want
     ShareX captures to persist (assuming the token has keep access).
2. In ShareX: **Destinations → Custom uploader settings → Import → from file**
   and select the edited `.sxcu`.
3. **Destinations → File / Image uploader → Custom file/image uploader**.

ShareX captures (screenshot, drag-drop, clipboard) will POST to Trueno; the
URL returned in the JSON `url` field is copied into the clipboard.

## Multi-user shared deployments

If you've added several entries to `~/.trueno-key`, hand a different token
to each ShareX user. Compromised tokens get retired by deleting their line
in the key file — no redeploy needed.
