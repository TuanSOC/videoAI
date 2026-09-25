"""Minimal ComfyUI HTTP client. Workflows are API-format JSON (ComfyUI: "Export (API)") in comfy_workflows/,
with "{{TOKEN}}" placeholders that are substituted before queueing."""

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)
POLL_SECONDS = 2


class ComfyError(RuntimeError):
    pass


def fill_template(node: Any, values: dict[str, Any]) -> Any:
    """Replace "{{KEY}}" placeholders. An exact-match string takes the value's own type (ints stay ints)."""
    if isinstance(node, dict):
        return {k: fill_template(v, values) for k, v in node.items()}
    if isinstance(node, list):
        return [fill_template(v, values) for v in node]
    if isinstance(node, str) and "{{" in node:
        for key, val in values.items():
            token = "{{" + key + "}}"
            if node == token:
                return val
            node = node.replace(token, str(val))
    return node


class ComfyClient:
    def __init__(self, url: str, http: httpx.Client | None = None):
        self.url = url.rstrip("/")
        self.http = http or httpx.Client(timeout=60)

    def available(self) -> bool:
        try:
            return self.http.get(f"{self.url}/system_stats", timeout=3).status_code == 200
        except httpx.HTTPError:
            return False

    def upload_image(self, path: Path) -> str:
        with path.open("rb") as fh:
            resp = self.http.post(f"{self.url}/upload/image",
                                  files={"image": (path.name, fh, "image/png")}, data={"overwrite": "true"})
        resp.raise_for_status()
        info = resp.json()
        return f"{info['subfolder']}/{info['name']}" if info.get("subfolder") else info["name"]

    def run(self, workflow_file: Path, values: dict[str, Any], out: Path, timeout: float) -> Path:
        """Queue a workflow, wait for it, download its first output file to `out`."""
        workflow = fill_template(json.loads(workflow_file.read_text(encoding="utf-8")), values)
        resp = self.http.post(f"{self.url}/prompt", json={"prompt": workflow, "client_id": uuid.uuid4().hex})
        if resp.status_code != 200:
            raise ComfyError(f"ComfyUI rejected {workflow_file.name}: {resp.text[:1000]}")
        prompt_id = resp.json()["prompt_id"]

        deadline = time.time() + timeout
        while time.time() < deadline:
            hist = self.http.get(f"{self.url}/history/{prompt_id}").json().get(prompt_id)
            if hist:
                status = hist.get("status", {})
                if status.get("status_str") == "error":
                    raise ComfyError(f"{workflow_file.name} failed: {json.dumps(status.get('messages', []))[:1000]}")
                if status.get("completed"):
                    return self._download_first_output(hist.get("outputs", {}), out)
            time.sleep(POLL_SECONDS)
        raise ComfyError(f"{workflow_file.name} timed out after {timeout:.0f}s")

    def _download_first_output(self, outputs: dict, out: Path) -> Path:
        # SaveImage → "images"; SaveVideo → "images" (+ "animated"); VHS_VideoCombine → "gifs"
        for node_out in outputs.values():
            for files in node_out.values():
                if not isinstance(files, list):
                    continue
                for f in files:
                    if isinstance(f, dict) and f.get("filename") and f.get("type") == "output":
                        resp = self.http.get(f"{self.url}/view", params={
                            "filename": f["filename"], "subfolder": f.get("subfolder", ""), "type": "output"})
                        resp.raise_for_status()
                        out = out.with_suffix(Path(f["filename"]).suffix)
                        out.write_bytes(resp.content)
                        return out
        raise ComfyError("workflow finished but produced no output file")

    def free(self) -> None:
        """Unload models so the next stage (or other workflow) gets the VRAM back."""
        try:
            self.http.post(f"{self.url}/free", json={"unload_models": True, "free_memory": True})
        except httpx.HTTPError:
            pass
