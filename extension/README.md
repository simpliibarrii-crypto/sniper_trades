# Raven Chrome bridge

This Manifest V3 extension reads the local Raven server and displays its latest
market snapshot, decision, plan levels, and tool evidence. It has no wallet,
exchange, private-key, arbitrary-site, or order-submission permission.

## Install

1. Start Sniper Trades at `http://127.0.0.1:8000`.
2. Open `chrome://extensions`.
3. Enable **Developer mode**.
4. Choose **Load unpacked** and select this `extension/` directory.
5. Pin **Raven Sniper Trades Bridge** and press **SAVE + SYNC**.

The badge is `L`, `S`, `X`, or `—` for Long, Short, Exit, or Hold. `!` means the
localhost server is unavailable. Chrome alarms refresh once per minute; the popup
can request an immediate refresh.

## Boundaries

- Host permissions are limited to `localhost` and `127.0.0.1`.
- The extension only calls `GET /extension/snapshot` and opens the full local deck.
- It never connects to an injected wallet and cannot submit a trade.
- Native MCPs run outside Chrome. Review `GET /mcp/catalog` before enabling one.
