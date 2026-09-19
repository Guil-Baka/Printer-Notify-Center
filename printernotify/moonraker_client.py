"""Thin HTTP client for the Moonraker API."""
from __future__ import annotations

import requests


class MoonrakerClient:
    def __init__(self, base_url: str, api_key: str = "", timeout: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self) -> dict:
        headers = {}
        if self.api_key:
            headers["X-Api-Key"] = self.api_key
        return headers

    def query_objects(self, objects: list[str]) -> dict:
        """Query a set of Moonraker printer objects, returns the `status` dict."""
        if not objects:
            return {}
        query = "&".join(objects)
        url = f"{self.base_url}/printer/objects/query?{query}"
        resp = requests.get(url, headers=self._headers(), timeout=self.timeout)
        resp.raise_for_status()
        payload = resp.json()
        return payload.get("result", {}).get("status", {})
