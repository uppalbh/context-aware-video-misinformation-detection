# Supported public video URLs

`POST /api/analyses/url` accepts JSON `{"url":"https://media.w3.org/2010/05/sintel/trailer.mp4"}` with the existing private session cookie. The JSON body is limited to 4096 bytes. URL-shape rejection returns HTTP 422 `{detail:{code,message}}`; a valid submission returns HTTP 202 `{analysis_id,status:"queued"}` immediately. DNS, download and actual-media validation run in the existing background worker, never in the submission request.

Only direct ASCII HTTPS `.mp4`/`.mov` paths on exact `VIDEO_URL_HOSTS` are supported. Default: `media.w3.org`. Configure a comma-separated list of exact trusted public media hosts; an empty value disables URL ingestion. There are no wildcard/subdomain exemptions. IP literals, non-443 ports, credentials, query strings, fragments, authenticated sources, signed URLs, YouTube/watch pages, playlists and general platform scraping are unsupported. Every redirect must also be a direct supported path on an allowed host. A configured host must still pass public-address checks.

## Reusable boundary and record fields

`app/url_media.py` exposes `validate_url(url, allowed_hosts) -> canonical_url` and `download(url, private_path, Settings) -> {sha256,size_bytes,final_url}`. The module has no provider, database or UI dependency. `worker.process` downloads URL inputs before reusing media inspection, audio extraction, transcription and pure word indexing.

Records add `ingestion:{kind:"upload"}` or `ingestion:{kind:"url",url:"...",final_url:"..."}`. The final URL is added after successful download. URL records begin with `sha256:""`, `size_bytes:0` and status `queued`; they progress through `downloading`, `extracting`, `transcribing`, then `transcribed` or `failed`. Existing records lacking ingestion data are interpreted as uploads. All per-second/word/segment contracts are unchanged and use uploaded-media time. Session-filtered status/history/retry/deletion endpoints apply equally to both inputs; no public file endpoint exists.

**Supabase setup:** apply `001_analyses.sql`, then `002_url_ingestion.sql`. Existing installations apply migration 002 to add ingestion JSONB and the downloading status. No migration was applied remotely during development. SQLite requires no schema change because it stores JSON records.

## Destination validation and connection pinning

For each initial/redirect connection, dnspython resolves A and AAAA with bounded five-second lifetimes and DNS search disabled. Every returned address must be globally routable and not private/reserved/loopback/link-local/multicast/unspecified. IPv4-mapped IPv6 and NAT64/6to4/Teredo transition ranges are rejected. Mixed public/private responses fail closed.

The downloader pins the first checked numeric address in a direct IPv4/IPv6 socket connection. It never resolves the hostname again at connect time. TLS certificate/hostname verification and SNI use the original hostname. Every redirect resolves/checks/pins again. A DNS change between preflight and connect therefore cannot redirect that connection to an unchecked address. Failure of the first address returns an error; no unbounded fallback chain exists.

No cookies, authentication, referer, user headers, environment proxies or automatic redirects are forwarded. Only fixed safe HTTP headers and the allowlisted host/path are sent. The code does not shell-interpolate URLs or use external download tools. TLS uses the system's trusted certificates and does not disable verification.

## Resource and failure handling

Up to three redirects are followed manually. Ten-second socket I/O timeouts and a 120-second total transport watchdog cover slow headers, bodies, connection and TLS. DNS queries have explicit bounded lifetimes and any expired deadline prevents another connection. The watchdog shuts down the pinned socket to interrupt slow streams rather than relying only on an inactivity timeout. Length is checked both when declared and while streaming, using the same `MAX_UPLOAD_MB` as uploads.

Only complete HTTP 200 identity-encoded downloads are accepted. Compressed/ambiguous framing, missing redirect locations, excessive redirects, invalid Content-Length, empty/truncated bodies, rejected TLS and oversized streams have meaningful safe error codes. Content-Type is not trusted: `.mp4`/`.mov` is an admission requirement, while ffprobe/FFmpeg check actual bytes, container, duration, dimensions and audio with the existing limits. HTML or corrupt content fails media processing and cannot produce a transcript.

Downloads write a generated private `clip.part`, then atomically rename to `clip`. Failure/cancellation removes partial files; restart removes leftovers and marks interrupted download jobs failed. Explicit retry can re-fetch failed URLs when no complete clip exists, subject to the same destination checks and three-attempt cap. Retained complete clips reuse the existing pipeline without fetching. Hash/size are recovered if the process stopped between atomic rename and persistence. Retention, private storage and session access are shared with uploads.

Use the documented one-worker/one-host architecture and a **480-second graceful shutdown timeout**, allowing bounded download plus media/provider processing. User-facing cancellation of active jobs is not implemented; deletion is available after terminal status. Hosts must provide a complete publicly accessible file without login, signed query or unsupported redirect; an allowlist entry does not guarantee availability or permission to process every file.

## Verification

Unit/integration checks cover IPv4/IPv6 private/reserved and transition addresses, encoded/credential/host confusion, all-address DNS checks, numeric connection pinning with hostname TLS, redirects and rebinding, byte limits with/without declared length, partial-file cleanup, worker/retry/recovery/session contracts, and shared transcription/index persistence. A live smoke check downloaded the public W3C-hosted Sintel trailer (4,372,373 bytes; probed 52.208333 seconds) through the pinned HTTPS downloader and extracted audio successfully; scratch audio/video were removed. This was **not** a live Whisper call or misinformation assessment.
