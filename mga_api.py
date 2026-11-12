#!/usr/bin/env python3
"""Minimal stdlib-only client for the Archetype AI Agent API.

Shared by run_mga_example.py, sweep_serve.py and cleanup.py. Auth and endpoint come from the environment, with a local .env loaded if
present: ATAI_API_KEY, ATAI_API_ENDPOINT.

Uploads infer their Content-Type from the file extension and stream from disk, so
a large video is never held in memory. MGA is zero-shot: nothing is fitted, so
there is no optimization, promotion or artifact.

Agent Lifecycle 2.0: a copy of `mga` in a project, with this run's values
set on it, is packaged (its models resolved once), the package is deployed as
`batch` or `serving`, and work runs in sessions on the deployment. A batch
session is one run over one video; a serving session answers `/query` until it
is cancelled. mga_project.py keeps the ids.
"""
from __future__ import annotations

import datetime
import http.client
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

# A session's terminal statuses.
TERMINAL = {"completed", "failed", "cancelled"}


def load_dotenv(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    for line in open(path):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        # A quoted value would otherwise keep its quotes: `"https://…"` is not a URL.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def api_base() -> str:
    load_dotenv()
    endpoint = os.environ.get("ATAI_API_ENDPOINT", "").rstrip("/")
    if not endpoint:
        sys.exit("ATAI_API_ENDPOINT is not set (put it in .env)")
    return endpoint


def files_root() -> str:
    """Where the files API and run-output refs resolve.

    An endpoint ending in /api serves files at `<endpoint>/files`; an older one
    without it, at `<endpoint>/v0.5/files`.
    """
    endpoint = api_base()
    return endpoint if endpoint.endswith("/api") else f"{endpoint}/v0.5"


def require_key() -> str:
    load_dotenv()
    key = os.environ.get("ATAI_API_KEY")
    if not key:
        sys.exit("ATAI_API_KEY is not set (put it in .env)")
    return key


def agents_base() -> str:
    return f"{api_base()}/agents"


def request(method: str, url: str, body=None, headers=None, raw: bool = False,
            retries: int = 4, allow_status: bool = False, timeout: int = 120,
            idempotent: bool | None = None):
    """Call the API, retrying transient failures (network errors, 502/503/504).

    Polls run for a long time, and a single DNS hiccup or socket timeout should
    not kill the client while the platform job carries on running unattended.
    Only calls that are safe to repeat retry: GET, HEAD and DELETE, and POSTs
    marked idempotent (cancel, stop). A create POST never retries: a repeat could
    make a second object nothing records, such as a deployment holding a GPU.
    Any other HTTP status is a real answer from the server.

    allow_status=True returns (status_code, body_text) instead of exiting on an
    HTTP error, which is what discovery needs — a 404 is a finding, not a crash.
    """
    require_key()
    if not (method in ("GET", "HEAD", "DELETE") if idempotent is None else idempotent):
        retries = 0
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {os.environ['ATAI_API_KEY']}")
        if body is not None:
            req.add_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = resp.read()
                if allow_status:
                    return resp.status, payload if raw else json.loads(payload or b"null")
                return payload if raw else json.loads(payload or b"null")
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            if e.code in (502, 503, 504) and attempt < retries:
                wait = 5 * (2 ** attempt)
                print(f"  {method} {url}: HTTP {e.code}; retrying in {wait}s [{attempt + 1}/{retries}]")
                time.sleep(wait)
                continue
            if allow_status:
                return e.code, detail
            sys.exit(f"{method} {url} failed ({e.code}): {detail}")
        except (urllib.error.URLError, TimeoutError, ConnectionError,
                http.client.HTTPException) as e:          # incl. a body cut off mid-transfer
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
    """POST a file to the files API as multipart/form-data.

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
        req = urllib.request.Request(f"{files_root()}/files", data=body,
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


def get_blueprint(reference: str) -> dict:
    """Fetch a blueprint by key or `blp_` id, exiting with guidance if it is not visible."""
    status, payload = request("GET", f"{agents_base()}/blueprints/{reference}",
                              allow_status=True)
    if status == 404:
        sys.exit(f"blueprint '{reference}' is not visible to this API key — list what "
                 f"is with GET {agents_base()}/blueprints")
    if status != 200:
        sys.exit(f"GET blueprints/{reference} failed ({status}): {payload}")
    return payload


def packages_url(package_id: str = "") -> str:
    """A package pins one blueprint and the models it resolves to. It carries no
    values: it runs its blueprint exactly as stored, so max_frames, max_new_tokens
    and prompt are set on the blueprint copy it is built from."""
    base = f"{agents_base()}/packages"
    return f"{base}/{package_id}" if package_id else base


def deployments_url(deployment_id: str = "") -> str:
    base = f"{agents_base()}/deployments"
    return f"{base}/{deployment_id}" if deployment_id else base


def sessions_url(session_id: str = "") -> str:
    base = f"{agents_base()}/sessions"
    return f"{base}/{session_id}" if session_id else base


def _wait_for(url: str, status: str, waiting: str, timeout_s: int, every_s: int = 5,
              exit: bool = True) -> dict:
    """Poll while `waiting`; exit unless it then reaches `status` (exit=False: return it as is)."""
    obj = request("GET", url)
    t0 = time.time()
    while obj["status"] == waiting:
        if time.time() - t0 > timeout_s:
            if not exit:
                return obj
            sys.exit(f"{obj['id']} still {waiting} after {timeout_s} s")
        time.sleep(every_s)
        obj = request("GET", url)
    if obj["status"] != status and exit:
        sys.exit(f"{obj['id']} is {obj['status']}, not {status}: {obj.get('error')}")
    return obj


def copy_blueprint(source: str, project_id: str, key: str, values: dict,
                   name: str | None = None) -> dict:
    """POST /agents/blueprints/{source}/copy with `values` set in the copy.

    A copy takes `overrides.values`, each of which must be one the blueprint
    declares (an undeclared key is a 400). `mga` fits nothing, so any of its
    values may be overridden. The copy lands in `project_id` under `key`; a key
    already in use there is a 409.
    """
    body = {"project_id": project_id, "blueprint_key": key,
            "overrides": {"values": values}}
    if name:
        body["name"] = name
    return request("POST", f"{agents_base()}/blueprints/{source}/copy", body=body)


def create_package(blueprint_id: str, name: str) -> dict:
    """Package one blueprint for the platform; it starts `building`.

    Record its id before package_ready(), so a build that fails or is
    interrupted can still be deleted."""
    return request("POST", packages_url(), body={
        "blueprint_ids": [blueprint_id], "target": {"kind": "platform"}, "name": name[:128]})


def package_ready(package_id: str, timeout_s: int = 900) -> dict:
    return _wait_for(packages_url(package_id), "ready", "building", timeout_s)


def ready_package(rec: dict, blueprint_id: str, name: str, save, log=print) -> dict:
    """rec["package"] once ready, created (and saved) first if there is none. A failed
    package never becomes ready, so it is deleted and built again, once."""
    for attempt in range(2):
        if not rec.get("package"):
            rec["package"] = create_package(blueprint_id, name)["id"]
            for kind in ("batch", "serving"):
                rec.pop(kind, None)
            save()
            log(f"package {rec['package']} building (models resolved once) ...")
        pkg = _wait_for(packages_url(rec["package"]), "ready", "building", 900, exit=False)
        if pkg["status"] == "ready":
            return pkg
        if pkg["status"] != "failed" or attempt:
            sys.exit(f"{rec['package']} is {pkg['status']}, not ready: {pkg.get('error')}")
        log(f"{rec['package']} failed ({pkg.get('error')}): deleting it and building a new one")
        request("DELETE", packages_url(rec["package"]), raw=True, allow_status=True)
        rec.pop("package")
        save()


def list_all(url: str) -> list:
    """Every item of a paginated list; the cursor is opaque and passed back as is."""
    items, after = [], ""
    sep = "&" if "?" in url else "?"
    while True:
        page = request("GET", f"{url}{sep}limit=1000{after}")
        items += page.get("data", [])
        if not page.get("has_more"):
            return items
        after = f"&after={page['next_cursor']}"


def deploy(package_id: str, kind: str) -> dict:
    """Deploy a ready package as `batch` or `serving`; it starts `deploying`.

    A batch deployment is ready at once; a serving one is placed on a GPU worker
    first. Record its id before deployment_ready()."""
    return request("POST", deployments_url(), body={"package_id": package_id, "kind": kind})


def deployment_ready(deployment_id: str, timeout_s: int = 900) -> dict:
    return _wait_for(deployments_url(deployment_id), "ready", "deploying", timeout_s)


def open_session(deployment_id: str, video_file_id: str | None = None) -> dict:
    """A batch session over one video, or a serving session with none.

    The video goes in `connectors.source`. MGA takes no text input: the instruction is the copy's `prompt` value."""
    body = {"deployment_id": deployment_id}
    if video_file_id:
        body["connectors"] = {"source": [{"type": "file", "id": video_file_id, "format": "mp4"}]}
    return request("POST", sessions_url(), body=body)


def wait_running(session_id: str, timeout_s: int = 1800) -> dict:
    """A serving session takes queries once it is `running`."""
    return _wait_for(sessions_url(session_id), "running", "pending", timeout_s, every_s=10)


def poll_session(session_id: str, timeout_s: int = 3600, interval_s: int = 20) -> str:
    """Poll a batch session until it ends, printing its events as they appear.

    Returns the final status (`completed`, `failed`, `cancelled`), or `timeout`.

    A session has no /logs, only /events: what the platform records about the
    session (opened, dispatched, failed with a reason). A manual that came back
    empty still ends `completed`, so count its steps.
    """
    seen: set[str] = set()
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        ses = request("GET", sessions_url(session_id))
        events = request("GET", f"{sessions_url(session_id)}/events?limit=100").get("data", [])
        for ev in reversed(events):                    # newest first
            marker = f"{ev.get('created_at')}|{ev.get('message')}"
            if marker in seen:
                continue
            seen.add(marker)
            print(f"  {local_time(ev.get('created_at'))} "
                  f"{str(ev.get('level', 'info')):7} {ev.get('message')}")
        if ses["status"] in TERMINAL:
            if ses.get("error"):
                print(f"  {session_id}: {ses['error']}")
            return ses["status"]
        time.sleep(interval_s)
    return "timeout"


def local_time(stamp) -> str:
    """An event's UTC `created_at` as local HH:MM:SS, like the script's own lines."""
    try:
        return datetime.datetime.fromisoformat(str(stamp).replace("Z", "+00:00")) \
            .astimezone().strftime("%H:%M:%S")
    except ValueError:
        return str(stamp)[11:19]


def fetch_ref(ref: str) -> bytes:
    """Download a /results ref.

    An absolute ref is presigned and must NOT get the bearer header. A session's
    relative ref is a root path, `/api/files/<id>/content`, not one under the
    endpoint, and needs the token."""
    if ref.startswith("http"):
        with urllib.request.urlopen(ref, timeout=300) as resp:
            return resp.read()
    endpoint = api_base()
    root = endpoint.removesuffix("/api") if ref.startswith("/api/") else files_root()
    return request("GET", f"{root}{ref}", raw=True)


def fetch_results(session_id: str) -> bytes | None:
    """The session's manual: the first output it wrote."""
    items = request("GET", f"{sessions_url(session_id)}/results").get("data") or []
    if not items:
        print("  no results (a failed session leaves none)")
        return None
    inner = items[-1].get("data") or {}           # newest first: the last is the first written
    if not inner.get("ref"):
        print("  results carried no download ref")
        return None
    return fetch_ref(inner["ref"])


def cancel_session(session_id: str) -> None:
    """POST /cancel. Do NOT send DELETE expecting a no-op — it removes the session."""
    request("POST", f"{sessions_url(session_id)}/cancel", body={}, allow_status=True, idempotent=True)


def end_session(session_id: str) -> None:
    """Cancel a session that is still going, then delete it."""
    status, ses = request("GET", sessions_url(session_id), allow_status=True)
    if status == 200 and ses.get("status") not in TERMINAL:
        cancel_session(session_id)
    request("DELETE", sessions_url(session_id), raw=True, allow_status=True)


def end_deployment(deployment_id: str) -> None:
    """Stop a deployment that is still up, then delete it."""
    status, dpl = request("GET", deployments_url(deployment_id), allow_status=True)
    if status == 200 and dpl.get("status") not in ("stopped", "failed"):
        request("POST", f"{deployments_url(deployment_id)}/stop", body={}, allow_status=True,
                idempotent=True)
    request("DELETE", deployments_url(deployment_id), raw=True, allow_status=True)
