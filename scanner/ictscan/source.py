"""Picks the candle source: the cTrader desktop MCP server (default) or the cTrader Open API (ICTSCAN_SOURCE=openapi)."""
from __future__ import annotations

from .config import DATA_SOURCE, MCP_ENDPOINT
from .mcp import MCPClient


def make_client(timeout=60):
    if DATA_SOURCE == "openapi":
        from .openapi import OpenAPIClient
        return OpenAPIClient(timeout=min(timeout, 60))
    if DATA_SOURCE != "mcp":
        raise SystemExit(f"Unknown ICTSCAN_SOURCE '{DATA_SOURCE}' (use 'mcp' or 'openapi')")
    return MCPClient(MCP_ENDPOINT, timeout=timeout)


def offline_hint():
    if DATA_SOURCE == "openapi":
        return "Waiting for the cTrader Open API"
    return "Waiting for cTrader (open it and enable the MCP server)"
