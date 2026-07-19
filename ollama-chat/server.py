#!/usr/bin/env python3
"""Tiny web server for the Ollama chatbot UI.

Serves the static chat page and proxies /api/* requests to a local
Ollama instance, streaming responses through unchanged. Using a proxy
keeps the browser same-origin, so no CORS configuration is needed on
the Ollama side.

Usage:
    python3 server.py [--port 8080] [--ollama http://localhost:11434]

Requires only the Python standard library.
"""

import argparse
import http.client
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

STATIC_DIR = Path(__file__).resolve().parent / "static"

# Ollama endpoints the proxy is willing to forward.
ALLOWED_API_PATHS = {"/api/tags", "/api/chat", "/api/show", "/api/version"}


class ChatHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    ollama_host = "localhost"
    ollama_port = 11434

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ALLOWED_API_PATHS:
            self._proxy("GET", path)
        elif path in ("/", "/index.html"):
            self._serve_file("index.html", "text/html; charset=utf-8")
        elif path == "/cjk-pixel.woff2":
            self._serve_file("cjk-pixel.woff2", "font/woff2")
        else:
            self._send_error(404, "not found")

    def do_POST(self):
        path = urlparse(self.path).path
        if path in ALLOWED_API_PATHS:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b""
            self._proxy("POST", path, body)
        else:
            self._send_error(404, "not found")

    def _serve_file(self, name, content_type):
        try:
            data = (STATIC_DIR / name).read_bytes()
        except OSError:
            self._send_error(404, "not found")
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _proxy(self, method, path, body=b""):
        conn = http.client.HTTPConnection(self.ollama_host, self.ollama_port, timeout=300)
        try:
            headers = {"Content-Type": "application/json"} if body else {}
            conn.request(method, path, body=body or None, headers=headers)
            upstream = conn.getresponse()

            self.send_response(upstream.status)
            self.send_header(
                "Content-Type", upstream.getheader("Content-Type", "application/json")
            )
            # Ollama streams chat responses as newline-delimited JSON with
            # chunked encoding; relay chunks as they arrive.
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

            while True:
                chunk = upstream.read1(65536)
                if not chunk:
                    break
                self._write_chunk(chunk)
            self._write_chunk(b"")
        except (ConnectionRefusedError, OSError) as exc:
            self._send_error(
                502,
                "Could not reach Ollama at "
                f"{self.ollama_host}:{self.ollama_port} — is it running? ({exc})",
            )
        finally:
            conn.close()

    def _write_chunk(self, data):
        self.wfile.write(f"{len(data):X}\r\n".encode() + data + b"\r\n")
        self.wfile.flush()

    def _send_error(self, status, message):
        payload = json.dumps({"error": message}).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def main():
    parser = argparse.ArgumentParser(description="Ollama chatbot web UI")
    parser.add_argument("--port", type=int, default=8080, help="port to serve the UI on")
    parser.add_argument(
        "--ollama",
        default="http://localhost:11434",
        help="base URL of the Ollama server",
    )
    args = parser.parse_args()

    ollama = urlparse(args.ollama)
    ChatHandler.ollama_host = ollama.hostname or "localhost"
    ChatHandler.ollama_port = ollama.port or 11434

    server = ThreadingHTTPServer(("127.0.0.1", args.port), ChatHandler)
    print(f"Chat UI:  http://localhost:{args.port}")
    print(f"Ollama:   http://{ChatHandler.ollama_host}:{ChatHandler.ollama_port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBye!")


if __name__ == "__main__":
    main()
