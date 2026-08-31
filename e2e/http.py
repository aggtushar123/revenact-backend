"""Shared stdlib-urllib HTTP client for e2e tests — real requests over the
wire against a LiveServerTestCase server, not self.client's in-process
shortcut. No new dependency. See any e2e/test_*.py for usage."""

import json
import urllib.error
import urllib.request


def http_request(method, url, payload=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request) as response:
            body = response.read()
            return response.status, json.loads(body) if body else None
    except urllib.error.HTTPError as e:
        body = e.read()
        return e.code, json.loads(body) if body else None


def http_post(url, payload, token=None):
    return http_request("POST", url, payload, token)


def http_get(url, token=None):
    return http_request("GET", url, token=token)


def http_patch(url, payload, token=None):
    return http_request("PATCH", url, payload, token)
