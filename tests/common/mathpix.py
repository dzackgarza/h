"""A local stand-in for the Mathpix v3/text API that replays recorded responses.

The OCR is a paid third-party call, so the suite must not make it. The alternative that
was in place -- replacing ``ocr_latex`` with a lambda -- removed the whole request from
the test: the endpoint, the payload, the auth header and the response parsing were never
executed, and a mutation survey found 35 changes to that code that nothing objected to,
including replacing the endpoint with ``None``.

So this serves the contract instead of hiding it. It is a real HTTP server; ``ocr_latex``
runs unmodified against it and has to produce a request Mathpix would accept:

  * ``POST /v3/text``, anything else is a 404
  * a non-empty ``app_key`` header, or 401 -- as Mathpix answers an unauthenticated call
  * a JSON body carrying ``src`` as a ``data:image/png;base64,`` URI of a real PNG and
    ``formats`` including ``text``, or 400

The reply is a response recorded from the real API, keyed by the SHA-256 of the PNG that
was sent. That key is the point: it holds the *image* fixed as well as the answer, so a
crop that moves cannot keep returning the OCR of the crop it used to make. An image with
no recording is not guessed at -- it is written to ``unrecorded/`` and answered with a
409 naming it, and ``bin/record_mathpix_fixtures.py`` turns those into fixtures with one
real call each.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import threading
from dataclasses import dataclass, field
from pathlib import Path

#: Where recorded calls live: ``<sha>.json`` beside the ``<sha>.png`` it answers.
CORPUS = Path(__file__).parent.parent / "corpus" / "mathpix"
UNRECORDED = CORPUS / "unrecorded"

PNG_DATA_URI = "data:image/png;base64,"


def digest(png: bytes) -> str:
    return hashlib.sha256(png).hexdigest()


@dataclass
class Call:
    """One request the emulator answered, for tests that assert what was sent."""

    path: str
    headers: dict[str, str]
    body: dict
    png: bytes


def _rejection(path: str, app_key: str, body: bytes) -> tuple[int, dict] | None:
    """Answer as the real API would if it would refuse this request.

    ``None`` means the request is one Mathpix accepts, and the caller goes on to the
    recorded answer. Every branch here is a way the recovery could send something the
    paid API rejects -- which, while ``ocr_latex`` was replaced wholesale in the tests,
    nothing could detect.
    """
    if path != "/v3/text":
        return 404, {"error": f"no such endpoint: {path}"}
    if not app_key.strip():
        return 401, {"error": "Unauthorized", "error_info": {}}

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return 400, {"error": "request body is not JSON"}

    return _bad_payload(payload)


def _bad_payload(payload: dict) -> tuple[int, dict] | None:
    """Answer 400 as the real API does when the body is not a readable OCR request."""
    src = payload.get("src", "")
    if not src.startswith(PNG_DATA_URI):
        return 400, {"error": "src is not a base64 PNG data URI"}
    try:
        png = base64.b64decode(src[len(PNG_DATA_URI) :], validate=True)
    except ValueError:
        return 400, {"error": "src is not valid base64"}
    if not png.startswith(b"\x89PNG"):
        return 400, {"error": "src does not decode to a PNG"}
    if "text" not in payload.get("formats", []):
        return 400, {"error": f"unsupported formats: {payload.get('formats')!r}"}
    return None


def _png_of(payload: dict) -> bytes:
    return base64.b64decode(payload["src"][len(PNG_DATA_URI) :])


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):  # BaseHTTPRequestHandler's spelling, not ours
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        refused = _rejection(self.path, self.headers.get("app_key") or "", body)
        if refused:
            return self._json(*refused)

        payload = json.loads(body)
        png = _png_of(payload)
        self.server.calls.append(
            Call(self.path, dict(self.headers), payload, png)  # type: ignore[attr-defined]
        )

        sha = digest(png)
        recorded = CORPUS / f"{sha}.json"
        if not recorded.exists():
            if self.server.capture_unrecorded:  # type: ignore[attr-defined]
                UNRECORDED.mkdir(parents=True, exist_ok=True)
                (UNRECORDED / f"{sha}.png").write_bytes(png)
            return self._json(
                409,
                {
                    "error": (
                        f"no recorded Mathpix response for image {sha}. It has been "
                        f"written to {UNRECORDED / (sha + '.png')}; record the real "
                        "answer with `python bin/record_mathpix_fixtures.py`."
                    )
                },
            )
        return self._json(200, json.loads(recorded.read_text(encoding="utf-8")))

    def _json(self, status: int, payload: dict):
        encoded = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *_args):
        """Quiet: the assertions are the output."""


@dataclass
class Emulator:
    """A running emulator: its ``url`` is what ``MATHPIX_API_URL`` should be set to."""

    url: str
    server: _Server
    calls: list[Call] = field(default_factory=list)

    def last(self) -> Call:
        assert self.calls, "the recovery made no Mathpix request"
        return self.calls[-1]

    def expect_no_recording(self) -> None:
        """Answer the next unknown image without filing it for recording.

        For the test that proves a non-2xx round trip fails the recovery: its image is
        meant to have no answer, so leaving it in ``unrecorded/`` would spend a real API
        call recording the very thing the test needs to stay unrecorded.
        """
        self.server.capture_unrecorded = False


class _Server(http.server.ThreadingHTTPServer):
    calls: list[Call]
    capture_unrecorded: bool


def serving() -> tuple[Emulator, _Server, threading.Thread]:
    server = _Server(("127.0.0.1", 0), _Handler)
    server.calls = []
    server.capture_unrecorded = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/v3/text"
    return Emulator(url=url, server=server, calls=server.calls), server, thread
