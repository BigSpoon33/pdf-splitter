"""deploy/smoke.sh's upload client, run inside the app image on the stack's own networks (stdlib only).

Two things curl on the host can't do here: reach the api on the `internal` backend network (nothing publishes it,
and Docker creates no port binding on an internal network), and be a client with a chosen address family and a
fixed address on one of the stack's networks. Each upload is a multipart POST whose file part
is a real file or `--zeros N` streamed without ever holding N bytes; the response is read WHILE the body is sent,
so a refusal the guard sends after the headers is seen at once and the send stops — which is the evidence: how
many bytes went out before the api answered. `--raw` sends the zeros as a bare JSON body instead (any `--method`),
`--chunk` + `--rate` make a real trickle, and `--declare N` promises N bytes in Content-Length while sending only
`--zeros` — a body that never finishes (gate r2: the slow-body checks).

  python smoke_client.py URL [--file P | --zeros N] [--header 'K: V']... [--n N] [--rate BYTES/S] [--family 4|6]
                            [--sni HOST] [--method PUT] [--raw] [--chunk BYTES] [--declare N]

Prints one line per upload: `<status> sent=<bytes> t=<seconds> local=<address> <body>` (`t` from the first byte
sent to the end of the exchange — the evidence of WHEN a proxy cut a body); exits 0 whatever the statuses were.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import select
import socket
import ssl
import sys
import time
import urllib.parse

BOUNDARY = b"smoke-client-boundary"
CHUNK = 64 * 1024


def multipart(name: str, size: int) -> tuple[bytes, bytes]:
    head = (
        b"--" + BOUNDARY + b"\r\n"
        b'Content-Disposition: form-data; name="file"; filename="' + name.encode() + b'"\r\n'
        b"Content-Type: application/pdf\r\n\r\n"
    )
    tail = b"\r\n--" + BOUNDARY + b"--\r\n"
    return head, tail


def body_chunks(head: bytes, tail: bytes, path: str | None, zeros: int, chunk: int):
    if head:
        yield head
    if path is not None:
        with open(path, "rb") as f:
            while piece := f.read(chunk):
                yield piece
    else:
        left = zeros
        while left > 0:
            n = min(chunk, left)
            yield b"\0" * n
            left -= n
    if tail:
        yield tail


def connect(url: urllib.parse.SplitResult, family: int, sni: str | None) -> socket.socket:
    host, port = url.hostname, url.port or (443 if url.scheme == "https" else 80)
    family, kind, proto, _, address = socket.getaddrinfo(host, port, family, socket.SOCK_STREAM)[0]
    sock = socket.socket(family, kind, proto)
    sock.settimeout(60)
    sock.connect(address)
    if url.scheme == "https":
        ctx = ssl.create_default_context()
        # The local stack's certificate is caddy's own CA's; the smoke checks behaviour, not trust.
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
        sock = ctx.wrap_socket(sock, server_hostname=sni or host)
    return sock


def upload(args: argparse.Namespace) -> str:
    url = urllib.parse.urlsplit(args.url)
    if args.raw:
        head, tail, content_type = b"", b"", "application/json"
    else:
        head, tail = multipart(args.filename, 0)
        content_type = f"multipart/form-data; boundary={BOUNDARY.decode()}"
    size = args.zeros if args.file is None else __import__("os").path.getsize(args.file)
    length = args.declare or len(head) + size + len(tail)
    headers = [
        f"{args.method} {url.path or '/'} HTTP/1.1",
        # Caddy picks the site block by Host: reached as `caddy` on the compose network, it must still be asked
        # for `localhost` (an unmatched host gets an empty 200, not the SPA's api).
        f"Host: {args.sni or url.hostname}",
        f"Content-Type: {content_type}",
        f"Content-Length: {length}",
        "Connection: close",
        *args.header,
    ]
    family = {"4": socket.AF_INET, "6": socket.AF_INET6}.get(args.family, socket.AF_UNSPEC)
    sock = connect(url, family, args.sni)
    local = sock.getsockname()[0]
    sock.sendall(("\r\n".join(headers) + "\r\n\r\n").encode())
    sock.setblocking(False)
    chunks = body_chunks(head, tail, args.file, args.zeros, args.chunk)
    pending, sent, received, started = b"", 0, b"", time.monotonic()
    done_sending, eof = False, False
    # A body the server is left waiting for (--declare) ends only with the server's answer; never wait forever.
    while not eof and time.monotonic() - started < args.wait:
        want_write = not done_sending
        readable, writable, _ = select.select([sock], [sock] if want_write else [], [], 5)
        if readable:
            try:
                data = sock.recv(CHUNK)
            except ssl.SSLWantReadError:
                data = None
            except (ConnectionResetError, ssl.SSLError):
                data = b""
            if data == b"":
                eof = True
            elif data:
                received += data
                # Once the whole response is in (Connection: close → until EOF) nothing more needs sending.
        if writable and not done_sending:
            if not pending:
                try:
                    pending = next(chunks)
                except StopIteration:
                    done_sending = True
                    continue
            # A rate cap keeps a body in flight long enough for a concurrent request to meet the in-flight cap.
            if args.rate and sent / max(time.monotonic() - started, 1e-6) > args.rate:
                time.sleep(0.01)
                continue
            try:
                n = sock.send(pending)
            except (ssl.SSLWantWriteError, BlockingIOError):
                continue
            except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
                # The api answered and closed; whatever it said is in `received` or on its way.
                done_sending = True
                continue
            sent += n
            pending = pending[n:]
        if b"\r\n\r\n" in received and done_sending is False and _complete(received):
            done_sending = True
    elapsed = time.monotonic() - started
    status_line, _, rest = received.partition(b"\r\n")
    status = status_line.split(b" ")[1].decode() if b" " in status_line else "000"
    body = rest.partition(b"\r\n\r\n")[2].decode(errors="replace").strip().replace("\n", " ")
    if args.verbose:
        print(received[:600].decode(errors="replace"), file=sys.stderr)
    # `t=` is when the exchange ended, from the first byte of the request: how long a body was allowed to
    # stall before the server cut it is the evidence of the proxy's read_body bound (gate r3).
    return f"{status} sent={sent} t={elapsed:.1f} local={local} {body[:200]}"


def _complete(received: bytes) -> bool:
    head, _, body = received.partition(b"\r\n\r\n")
    for line in head.split(b"\r\n"):
        if line.lower().startswith(b"content-length:"):
            return len(body) >= int(line.split(b":")[1])
    return False


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("url")
    p.add_argument("--file")
    p.add_argument("--zeros", type=int, default=0)
    p.add_argument("--filename", default="book.pdf")
    p.add_argument("--header", action="append", default=[])
    p.add_argument("--n", type=int, default=1)
    p.add_argument("--rate", type=int, default=0)
    p.add_argument("--family", default="")
    p.add_argument("--sni")
    p.add_argument("--method", default="POST")
    p.add_argument("--raw", action="store_true")
    p.add_argument("--chunk", type=int, default=CHUNK)
    p.add_argument("--declare", type=int, default=0)
    p.add_argument("--wait", type=float, default=120)
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.n) as pool:
        for line in pool.map(lambda _: upload(args), range(args.n)):
            print(line)
            sys.stdout.flush()


if __name__ == "__main__":
    main()
