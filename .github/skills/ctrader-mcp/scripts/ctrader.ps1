# Dot-source this file, then call: Invoke-CTrader <tool> @{ arg = value }
# Returns the parsed JSON payload of the tool result. Trading tools are refused.

$script:CTraderEndpoint = 'http://127.0.0.1:9876/mcp/'
$script:CTraderSession = $null
$script:CTraderRequestId = 0
$script:CTraderBlocked = @(
    'place_market_order', 'place_limit_order', 'place_stop_order', 'place_stop_limit_order',
    'amend_order', 'amend_position', 'cancel_order', 'cancel_all_pending_orders',
    'close_position', 'close_position_partial', 'close_all_positions',
    'startPlugin', 'stopPlugin', 'clear_chart_objects'
)

function Connect-CTrader {
    $body = @{ jsonrpc = '2.0'; id = 0; method = 'initialize'; params = @{ protocolVersion = '2025-06-18'; capabilities = @{}; clientInfo = @{ name = 'copilot-ctrader-skill'; version = '1.0.0' } } } | ConvertTo-Json -Depth 10 -Compress
    $response = Invoke-WebRequest -Uri $script:CTraderEndpoint -Method Post -Headers @{ Accept = 'application/json, text/event-stream' } -ContentType 'application/json' -Body $body -TimeoutSec 20 -UseBasicParsing
    $script:CTraderSession = [string]($response.Headers['Mcp-Session-Id'] -join '')
    if (-not $script:CTraderSession) { throw 'cTrader MCP did not return a session id. Is "Enable MCP server" on (port 9876)?' }
    $note = @{ jsonrpc = '2.0'; method = 'notifications/initialized' } | ConvertTo-Json -Compress
    $null = Invoke-WebRequest -Uri $script:CTraderEndpoint -Method Post -Headers (Get-CTraderHeaders) -ContentType 'application/json' -Body $note -TimeoutSec 20 -UseBasicParsing
}

function Get-CTraderHeaders {
    @{ Accept = 'application/json, text/event-stream'; 'Mcp-Session-Id' = $script:CTraderSession; 'MCP-Protocol-Version' = '2025-06-18' }
}

function Invoke-CTrader {
    param(
        [Parameter(Mandatory)][string]$Tool,
        [hashtable]$Arguments = @{}
    )
    if ($Tool -in $script:CTraderBlocked) { throw "Refused: '$Tool' is a trading/destructive tool. Stop and tell the user." }
    if (-not $script:CTraderSession) { Connect-CTrader }
    $script:CTraderRequestId += 1
    $body = @{ jsonrpc = '2.0'; id = $script:CTraderRequestId; method = 'tools/call'; params = @{ name = $Tool; arguments = $Arguments } } | ConvertTo-Json -Depth 20 -Compress
    $r = Invoke-RestMethod -Uri $script:CTraderEndpoint -Method Post -Headers (Get-CTraderHeaders) -ContentType 'application/json' -Body $body -TimeoutSec 60
    if ($r.error) { throw ($r.error | ConvertTo-Json -Depth 10 -Compress) }
    if ($r.result.isError) { throw ($r.result | ConvertTo-Json -Depth 10 -Compress) }
    $text = @($r.result.content | Where-Object { $_.type -eq 'text' })
    if ($text.Count -ne 1) { throw "Unexpected result shape from $Tool." }
    # -DateKind String keeps ISO timestamps as strings; default parsing silently converts them to DateTime.
    $data = $text[0].text | ConvertFrom-Json -DateKind String
    if ($data.error) { throw ($data.error | ConvertTo-Json -Depth 10 -Compress) }
    $data
}

function ConvertTo-NewYorkTime {
    param([Parameter(Mandatory)][string]$UtcIso)
    $zone = [TimeZoneInfo]::FindSystemTimeZoneById('Eastern Standard Time')
    [TimeZoneInfo]::ConvertTime([DateTimeOffset]::Parse($UtcIso, [Globalization.CultureInfo]::InvariantCulture), $zone)
}
