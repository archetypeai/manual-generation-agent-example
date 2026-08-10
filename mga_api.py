#!/usr/bin/env python3
"""Minimal stdlib-only client for the Archetype AI Agent API.

Shared by inspect_bundle.py (discovery) and run_mga_example.py (inference).
Auth and endpoint come from the environment, with a local .env loaded if
present: ATAI_API_KEY, ATAI_API_ENDPOINT.

Adapted from the RED example's red_api.py. Two things differ, both because MGA
takes video rather than a sensor CSV:

  * uploads infer their Content-Type from the file extension, and stream from
    disk rather than buffering — a 200 MB video read into a bytes literal is a
    400 MB resident set once the multipart body is assembled.
  * there is no fitted-artifact path. MGA V1 is zero-shot: the design doc states
    there are no build-time optimization steps, so nothing is ever fitted and no
    s3:// artifact is ever pinned into a bundle.
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid


def load_dotenv(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    for line in open(path):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def api_base() -> str:
    load_dotenv()
    endpoint = os.environ.get("ATAI_API_ENDPOINT", "").rstrip("/")
    if not endpoint:
        sys.exit("ATAI_API_ENDPOINT is not set (put it in .env)")
    return endpoint


def require_key() -> str:
    load_dotenv()
    key = os.environ.get("ATAI_API_KEY")
    if not key:
        sys.exit("ATAI_API_KEY is not set (put it in .env)")
    return key


def agents_base() -> str:
    return f"{api_base()}/agents"


def request(method: str, url: str, body=None, headers=None, raw: bool = False,
            retries: int = 4, allow_status: bool = False):
    """Call the API, retrying transient network failures.

    Polls run for a long time, and a single DNS hiccup or socket timeout should
    not kill the client while the platform job carries on running unattended.
    Only *network* errors retry; an HTTP status is a real answer from the server.

    allow_status=True returns (status_code, body_text) instead of exiting on an
    HTTP error, which is what discovery needs — a 404 is a finding, not a crash.
    """
    require_key()
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {os.environ['ATAI_API_KEY']}")
        if body is not None:
            req.add_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                payload = resp.read()
                if allow_status:
                    return resp.status, payload if raw else json.loads(payload or b"null")
                return payload if raw else json.loads(payload or b"null")
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            if allow_status:
                return e.code, detail
            sys.exit(f"{method} {url} failed ({e.code}): {detail}")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt == retries:
                if allow_status:
                    return 0, str(e)
                sys.exit(f"{method} {url} failed after {retries + 1} attempts: {e}")
            wait = 5 * (2 ** attempt)
            print(f"  network error ({e}); retrying in {wait}s "
                  f"[{attempt + 1}/{retries}]")
            time.sleep(wait)


def guess_content_type(path: str) -> str:
    """Infer the multipart Content-Type from the extension.

    The files API enforces a MIME allowlist, and it is the *declared* type that
    is checked — not the bytes. An .mp4 sent as text/csv is rejected, and a video
    sent with no type at all is rejected too, so this is not cosmetic.
    """
    ctype, _ = mimetypes.guess_type(path)
    return ctype or "application/octet-stream"


def upload_file(path: str, rename: str | None = None) -> dict:
    """POST a file to /v0.5/files as multipart/form-data.

    The body is streamed from disk: preamble + file handle + epilogue, with an
    explicit Content-Length. Videos here run to hundreds of megabytes, and
    reading one into memory to build a single bytes object doubles that.
    """
    if not os.path.exists(path):
        sys.exit(f"no such file: {path}")
    boundary = uuid.uuid4().hex
    filename = rename or os.path.basename(path)
    preamble = b"".join([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode(),
        f"Content-Type: {guess_content_type(path)}\r\n\r\n".encode(),
    ])
    epilogue = f"\r\n--{boundary}--\r\n".encode()
    size = os.path.getsize(path)

    with open(path, "rb") as fh:
        body = _ChainedReader([preamble, fh, epilogue])
        req = urllib.request.Request(f"{api_base()}/v0.5/files", data=body,
                                     method="POST")
        req.add_header("Authorization", f"Bearer {os.environ['ATAI_API_KEY']}")
        req.add_header("Content-Type",
                       f"multipart/form-data; boundary={boundary}")
        req.add_header("Content-Length",
                       str(len(preamble) + size + len(epilogue)))
        try:
            with urllib.request.urlopen(req, timeout=900) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            sys.exit(f"upload of {path} failed ({e.code}): "
                     f"{e.read().decode(errors='replace')}")


class _ChainedReader:
    """A read()-able that concatenates bytes chunks and file objects in order."""

    def __init__(self, parts):
        self._parts = list(parts)

    def read(self, amt: int = -1) -> bytes:
        out = b""
        while self._parts and (amt < 0 or len(out) < amt):
            want = -1 if amt < 0 else amt - len(out)
            head = self._parts[0]
            chunk = head[:want] if isinstance(head, (bytes, bytearray)) else head.read(want)
            if isinstance(head, (bytes, bytearray)):
                self._parts[0] = head[len(chunk):]
                if not self._parts[0]:
                    self._parts.pop(0)
            elif not chunk:
                self._parts.pop(0)
            out += chunk
        return out


def blueprint_must_exist(key: str) -> dict:
    """Fetch a blueprint, exiting with guidance if it is not in the org."""
    status, payload = request("GET", f"{agents_base()}/blueprints/{key}",
                              allow_status=True)
    if status == 404:
        sys.exit(f"blueprint '{key}' is not registered in your org — ask an org "
                 f"admin to register it, or run inspect_bundle.py to list what is.")
    if status != 200:
        sys.exit(f"GET blueprints/{key} failed ({status}): {payload}")
    return payload


def poll_agent(agent_id: str, timeout_s: int, interval_s: int = 15) -> str:
    """Poll until terminal, streaming new audit events as they appear."""
    agents = agents_base()
    deadline = time.time() + timeout_s
    seen: set[str] = set()
    status = "running"
    while status not in ("completed", "failed", "canceled") and time.time() < deadline:
        time.sleep(interval_s)
        status = request("GET", f"{agents}/instances/{agent_id}").get("status")
        for ev in request("GET", f"{agents}/instances/{agent_id}/events").get("data", []):
            marker = f"{ev.get('created_at')}{ev.get('message')}"
            if marker not in seen:
                seen.add(marker)
                print(f"  [{ev.get('level', 'info')}] {ev.get('created_at', '')}  "
                      f"{ev.get('message', '')}")
        print(f"  {time.strftime('%H:%M:%S')} status={status}")
    return status


def jos_job_id(agent_id: str) -> str | None:
    """The JOS job id is announced in the agent's events at dispatch."""
    events = request("GET", f"{agents_base()}/instances/{agent_id}/events")
    job_id = None
    for ev in events.get("data", []):
        m = re.search(r"dispatched to JOS as (job_\w+)", str(ev.get("message", "")))
        if m:
            job_id = m.group(1)
    return job_id


def fetch_ref(ref: str) -> bytes:
    """Download a /results ref, which comes in two shapes.

    A run output's ref is a *relative* platform path ("/files/download/<name>")
    that resolves under /v0.5 and needs the bearer token. An absolute ref is a
    presigned S3 URL carrying its own auth, and adding the header breaks it.
    """
    if ref.startswith("http"):
        with urllib.request.urlopen(ref, timeout=300) as resp:
            return resp.read()
    return request("GET", f"{api_base()}/v0.5{ref}", raw=True)


def download_results(agent_id: str, out_path: str) -> str | None:
    """Fetch /results and save the first output to out_path."""
    results = request("GET", f"{agents_base()}/instances/{agent_id}/results")
    items = results.get("data", results if isinstance(results, list) else [])
    if not items:
        print("  no results returned")
        return None
    print(f"results ({len(items)}):")
    for item in items:
        inner = item.get("data") or {}
        print(f"  {inner.get('filename') or item.get('id')}  "
              f"({inner.get('num_bytes', '?')} bytes)")
    inner = items[0].get("data") or {}
    ref = inner.get("ref") or items[0].get("ref") or items[0].get("url")
    if not ref:
        print("  results carried no download ref")
        return None
    payload = fetch_ref(ref)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    open(out_path, "wb").write(payload)
    print(f"saved output to {out_path}")
    return out_path
