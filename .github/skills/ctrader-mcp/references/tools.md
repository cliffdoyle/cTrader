# cTrader MCP tool catalog

Generated from `tools/list` on server `ctrader-desktop` 2.0.0 (78 tools, 2026-10-17). Full JSON schemas: [tools-schema.json](./tools-schema.json).

Safety: **BLOCKED** = never call (trading, cBots, or wipes user drawings). **ASK** = changes platform state; only when the task needs it, and only on objects/charts the agent created. Unmarked = read-only or safe to use as needed.

| Tool | Safety | Arguments (* = required) | Description |
|---|---|---|---|
| `get_balance` |  |  | Get current trading account information including balance, equity, margin, free margin, margin level, net profit, leverage, deposit asset, account type, brok... |
| `get_accounts_list` |  |  | List all available trading accounts (login, broker, balance, currency, type, online status). Excludes hidden accounts. |
| `get_account_statistics` |  |  | Get aggregated trading statistics: net profit, total trades, winning/losing trades, profit factor, and win rate. |
| `create_price_alert` | ASK | `symbolName*`:string, `price*`:number, `condition*`:string, `quoteType`:string, `message`:string | Create a price alert that triggers when a symbol's price crosses a threshold. |
| `delete_price_alert` | ASK | `alertId*`:integer | Delete a price alert by its ID. |
| `get_price_alerts` |  |  | Get all active price alerts with id, symbol, price, condition type, quote type, and message. |
| `add_chart_object` |  | `object_type*`:string, `price1`:number, `time1`:string, `price2`:number, `time2`:string, `price3`:number, `time3`:string, `color`:string, `fill`:boolean, `extend`:boolean, `text`:string, `side`:string | Add a drawing object to the active chart. Returns objectId for later deletion. |
| `apply_chart_template` | ASK | `templateId*`:string | Apply a chart template by ID to the currently active chart. |
| `change_chart_symbol` | ASK | `symbolName*`:string | Change the symbol on the currently active chart. |
| `change_chart_timeframe` | ASK | `timeframe*`:string | Change the timeframe on the currently active chart. |
| `clear_chart_objects` | **BLOCKED** |  | Delete all drawing objects from the active chart. |
| `close_chart` | ASK | `chartId*`:string | Close a chart by its chart ID (from list_charts). |
| `delete_chart_object` | ASK | `objectId*`:string | Delete a specific drawing object from the active chart by its objectId. |
| `delete_chart_template` | ASK | `templateId*`:string | Delete a saved chart template by its ID. |
| `focus_chart` |  | `chartId*`:string | Make a chart active/focused by its chart ID (from list_charts). |
| `get_active_chart` |  |  | Get information about the currently active chart (chartId, symbol and timeframe). |
| `get_chart_objects` |  |  | List all drawing objects on the active chart with their objectId, type, and name. |
| `get_chart_viewport` |  |  | Get the active chart's scroll position and zoom level (visible bars count). |
| `list_charts` |  |  | List all open charts with their IDs, symbols, timeframes, and which is currently active. |
| `list_chart_templates` |  |  | List available custom chart templates. |
| `open_chart` |  | `symbolName*`:string, `timeframe`:string | Open a new chart tab for the given symbol, optionally with a timeframe. |
| `save_chart_template` | ASK | `name*`:string | Save the current active chart's settings as a named template. |
| `scroll_chart` |  | `startIndex`:integer, `offsetBars`:integer | Scroll the active chart. Use startIndex for an absolute target or offsetBars for movement relative to the current start index.use maxStartIndex (from {McpCon... |
| `update_chart_object` | ASK | `objectName*`:string, `price1`:number, `time1`:string, `price2`:number, `time2`:string, `color`:string, `fill`:boolean, `text`:string, `lineThickness`:integer, `lineStyle`:string | Update an existing drawing object on the active chart. Only provided fields are changed; omitted fields keep their current value. |
| `zoom_chart` |  | `visibleBars*`:integer | Set the number of visible bars on the active chart (zoom level). Fewer bars = more zoomed in. |
| `get_server_time` |  |  | Get the current server time in UTC, local, and Unix milliseconds. |
| `addChartIndicator` | ASK | `typeName*`:string | Add an indicator to the active chart by its type name (e.g. 'Simple Moving Average'). Use listChartIndicators to see what is already attached. |
| `getIndicatorValues` |  | `indicatorId*`:string, `outputIndex`:integer, `count`:integer | Get the most recent computed values from an indicator output line on the active chart. |
| `listAvailableIndicators` |  |  | List all available indicator types that can be added to a chart via addChartIndicator. Returns title and whether the indicator is an overlay (drawn on the ma... |
| `listChartIndicators` |  |  | List all indicators currently attached to the active chart, with their indicatorId, typeName, and output names. |
| `removeChartIndicator` | ASK | `indicatorId*`:string | Remove an indicator from the active chart by its indicatorId (from listChartIndicators). |
| `update_indicator_parameters` | ASK | `indicatorId*`:string, `parameters*`:object | Update parameters of an indicator on the active chart by its indicatorId (from listChartIndicators). Pass parameters as a JSON object whose keys match the in... |
| `get_trendbars` |  | `symbolName*`:string, `timeframe*`:string, `from*`:string, `to*`:string, `limit`:integer | Get historical OHLC trend bars for a symbol and timeframe in a date range. Server caps each request at 1000 bars. |
| `get_symbol_details` |  | `symbolName*`:string | Get detailed information about a trading symbol: digits, pip size, lot size, min/max volume, category, and current bid/ask. |
| `get_symbols` |  | `filter`:string | Get a list of available trading symbols. Optionally filter by name or description. |
| `get_spot_prices` |  | `symbolName*`:string | Get the current quote (bid, ask, spread, high, low, open) for a specific trading symbol. |
| `get_symbol_sessions` |  | `symbolName*`:string | Get the trading sessions (open/close times) for a symbol along with its time zone. |
| `get_news` |  | `feed`:string, `limit`:integer | Get recent Fx news items (general or crypto) with title, summary, and publication date. |
| `listPlugins` |  |  | List all available cBot plugins with their pluginId, name, and running status. |
| `startPlugin` | **BLOCKED** | `pluginId*`:string | Start a cBot plugin by its pluginId (from listPlugins). |
| `stopPlugin` | **BLOCKED** | `pluginId*`:string | Stop a running cBot plugin by its pluginId. |
| `calculate_margin` |  | `symbolName*`:string, `volume*`:number, `volumeType*`:string | Calculate the expected margin required to open a position for a given symbol and volume. |
| `cancel_all_pending_orders` | **BLOCKED** |  | Cancel all pending orders. |
| `cancel_order` | **BLOCKED** | `orderId*`:integer | Cancel a pending order by its ID. |
| `close_all_positions` | **BLOCKED** | `symbolName`:string | Close all open positions, optionally filtered by symbol name. |
| `close_position` | **BLOCKED** | `positionId*`:integer | Close an open trading position by its ID. |
| `close_position_partial` | **BLOCKED** | `positionId*`:integer, `volume*`:number, `volumeType*`:string | Partially close an open trading position by its ID, reducing its volume. |
| `get_deals` |  | `symbolName`:string, `count`:integer | Get the most recent deals (executed orders), optionally filtered by symbol. Default count: 50, max: 200. |
| `get_pending_orders` |  |  | Get all pending orders with details: symbol, side, order type, volume (in units and lots), target price, current price, stop loss price (absolute) and pips, ... |
| `get_positions` |  |  | Get all open trading positions with details: symbol, side, volume (in units and lots), entry price, current price, pips, profit, swap, commission, stop loss ... |
| `get_order_history` |  |  | Get closed trade history including symbol, side, volume, entry/close price, pips, profit, and timestamps. Returns trades currently loaded in the repository. |
| `amend_order` | **BLOCKED** | `orderId*`:integer, `entryPrice`:number, `volume`:number, `volumeType`:string, `expiration`:string, `stopLossPips`:number, `takeProfitPips`:number | Modify a pending order's entry price, volume, expiration, stop loss, or take profit. |
| `amend_position` | **BLOCKED** | `positionId*`:integer, `stopLoss`:number, `takeProfit`:number | Modify the stop loss and/or take profit of an open position (prices, not pips). |
| `place_limit_order` | **BLOCKED** | `symbolName*`:string, `side*`:string, `volume*`:number, `volumeType*`:string, `limitPrice*`:number, `label`:string, `comment`:string, `stopLossPips`:number, `takeProfitPips`:number, `expiresAt`:string | Place a limit order that executes when the price reaches the specified level. |
| `place_market_order` | **BLOCKED** | `symbolName*`:string, `side*`:string, `volume*`:number, `volumeType*`:string, `label`:string, `comment`:string, `stopLossPips`:number, `takeProfitPips`:number | Place a market order to open a trading position immediately at the current market price. |
| `place_stop_limit_order` | **BLOCKED** | `symbolName*`:string, `side*`:string, `volume*`:number, `volumeType*`:string, `stopPrice*`:number, `limitPrice*`:number, `label`:string, `comment`:string, `stopLossPips`:number, `takeProfitPips`:number, `expiresAt`:string, `triggerMethod`:string | Place a stop-limit order that triggers at the stop price and then executes as a limit order at the limit price. |
| `place_stop_order` | **BLOCKED** | `symbolName*`:string, `side*`:string, `volume*`:number, `volumeType*`:string, `stopPrice*`:number, `label`:string, `comment`:string, `stopLossPips`:number, `takeProfitPips`:number, `expiresAt`:string, `triggerMethod`:string | Place a stop order that executes when the price crosses the specified level. |
| `delete_workspace` | ASK | `name*`:string | Delete a saved workspace by name. |
| `get_asp_tab` |  |  | Get the currently active tab in the Active Symbol Panel. |
| `get_current_app` |  |  | Get the currently active application section: trade, automate, analyze, copy, or invite. |
| `get_layout_mode` |  |  | Get the current main window layout mode: all, asp, trade_watch, or chart_only. |
| `get_market_watch_panel` |  |  | Get the currently active Market Watch panel: watchlists or all_symbols. |
| `get_trade_watch_tab` |  |  | Get the currently active tab in the TradeWatch panel. |
| `list_workspaces` |  |  | List all saved workspaces (layout presets) by name. |
| `load_workspace` | ASK | `name*`:string | Load a saved workspace by name, restoring the saved layout and charts. |
| `save_workspace` | ASK | `name*`:string | Save the current layout as a named workspace. Creates a new workspace or overwrites an existing one. |
| `set_asp_tab` | ASK | `tab*`:string | Set the active tab in the Active Symbol Panel. Tabs: symbol, dom, calendar, news, autochartist. |
| `set_layout_mode` | ASK | `mode*`:string | Set the main window layout mode to show/hide panels. Modes: 'all' (ASP + TradeWatch), 'asp' (ASP only), 'trade_watch' (TradeWatch only), 'chart_only' (charts... |
| `set_market_watch_panel` | ASK | `panel*`:string | Switch the active Market Watch panel to watchlists or all_symbols. |
| `set_trade_watch_tab` | ASK | `tabName*`:string | Switch the active tab in the TradeWatch panel by tab name. |
| `show_notification` | ASK | `caption*`:string, `description*`:string, `type`:string | Show an in-app popup notification with a caption, message, and type. |
| `switch_app` | ASK | `app*`:string | Switch to a different application section: trade, automate, analyze, copy, or invite. |
| `add_symbol_to_watchlist` | ASK | `watchlistName*`:string, `symbolName*`:string | Add a symbol to a watchlist by watchlist name. |
| `create_watchlist` | ASK | `name*`:string | Create a new (empty) dynamic watchlist with the given name. |
| `delete_watchlist` | ASK | `name*`:string | Delete a dynamic watchlist by its name. |
| `get_watchlists` |  |  | Get all watchlists with their names, keys, and the list of symbols in each watchlist. |
| `remove_symbol_from_watchlist` | ASK | `watchlistName*`:string, `symbolName*`:string | Remove a symbol from a watchlist by watchlist name. |
| `rename_watchlist` | ASK | `name*`:string, `newName*`:string | Rename a dynamic watchlist. |

