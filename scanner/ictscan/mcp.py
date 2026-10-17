"""Minimal client for the cTrader desktop MCP server (read-only use; trading tools are refused)."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

BLOCKED = {
    "place_market_order", "place_limit_order", "place_stop_order", "place_stop_limit_order",
    "amend_order", "amend_position", "cancel_order", "cancel_all_pending_orders",
    "close_position", "close_position_partial", "close_all_positions",
    "startPlugin", "stopPlugin", "clear_chart_objects",
}


class MCPError(RuntimeError):
    pass


class MCPUnavailable(MCPError):
    pass


class MCPClient:
    def __init__(self, endpoint, timeout=60):
        self.endpoint = endpoint
        self.timeout = timeout
        self.session = None
        self.req_id = 0

    def _post(self, payload, with_session=True):
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if with_session and self.session:
            headers["Mcp-Session-Id"] = self.session
            headers["MCP-Protocol-Version"] = "2025-06-18"
        req = urllib.request.Request(self.endpoint, data=json.dumps(payload).encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                body = r.read().decode("utf-8")
                return r.headers, body
        except urllib.error.HTTPError as e:
            raise MCPError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}") from e
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
            raise MCPUnavailable(f"cTrader MCP not reachable at {self.endpoint}: {e}") from e

    @staticmethod
    def _parse(body):
        body = body.strip()
        if not body:
            return None
        if body.startswith("{"):
            return json.loads(body)
        for line in body.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        raise MCPError(f"Unexpected response: {body[:200]}")

    def connect(self):
        self.session = None
        try:
            headers, body = self._post({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "ictscan", "version": "1.0"}}}, with_session=False)
        except MCPError as e:
            if not isinstance(e, MCPUnavailable) and str(e).startswith("HTTP 404"):
                raise MCPUnavailable("cTrader's MCP server is not serving requests (HTTP 404). In cTrader: Settings → MCP server → "
                                     "untick and re-tick 'Enable MCP server', or restart cTrader.") from e
            raise
        sid = headers.get("Mcp-Session-Id")
        if not sid:
            raise MCPError("No Mcp-Session-Id returned; is 'Enable MCP server' on in cTrader?")
        self.session = sid
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def call(self, tool, args=None, _retry=True):
        if tool in BLOCKED:
            raise MCPError(f"Refused: {tool} is a trading/destructive tool")
        if not self.session:
            self.connect()
        self.req_id += 1
        try:
            _, body = self._post({"jsonrpc": "2.0", "id": self.req_id, "method": "tools/call",
                                  "params": {"name": tool, "arguments": args or {}}})
        except MCPUnavailable:
            self.session = None
            raise
        except MCPError:
            if _retry:
                self.session = None
                return self.call(tool, args, _retry=False)
            raise
        msg = self._parse(body)
        if msg is None:
            raise MCPError("Empty response")
        if "error" in msg:
            if _retry:
                self.session = None
                return self.call(tool, args, _retry=False)
            raise MCPError(json.dumps(msg["error"])[:300])
        res = msg.get("result") or {}
        texts = [c.get("text") for c in res.get("content", []) if c.get("type") == "text"]
        if res.get("isError"):
            raise MCPError(f"{tool} failed: {(texts or [''])[0][:300]}")
        if len(texts) != 1:
            raise MCPError(f"Unexpected result shape from {tool}")
        data = json.loads(texts[0])
        if isinstance(data, dict) and data.get("error"):
            raise MCPError(f"{tool}: {data['error']}")
        return data
