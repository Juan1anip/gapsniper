# ⚡ GapSniper

> **Hunt the gap. Keep the edge.**

GapSniper is a **zero-risk Polymarket arbitrage simulator** that scans live order books for YES + NO ask prices summing to less than $1.00 — a risk-free profit condition — and paper-trades them with realistic fill logic, fees, and slippage.

No wallet. No capital. No risk. Just real market data and simulated execution.

---

## The Strategy

On Polymarket, every binary market has two outcomes: YES and NO. At settlement, exactly one pays $1.00 per share. Therefore:

```
YES_ask + NO_ask  <  $1.00   →   guaranteed profit
```

If you can buy YES for $0.48 and NO for $0.47, your combined cost is $0.95. Regardless of outcome, you redeem $1.00 — a **5.26% risk-free return** per share.

GapSniper hunts these gaps continuously across the top markets by volume.

---

## Features

- ✅ **Live order book scanning** via Polymarket CLOB (public, no auth)
- ✅ **Paper trading engine** with configurable balance, fees, and slippage
- ✅ **Real-time terminal dashboard** (rich)
- ✅ **Fee-aware** — models Polymarket's per-match taker fee curve
- ✅ **Opportunity deduplication** — won't re-trade the same market
- ✅ **Zero dependencies on private keys** — runs entirely on public data

---

## Quick Start

```bash
git clone https://github.com/Juan1anip/gapsniper.git
cd gapsniper
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python gapsniper.py --balance 1000 --min-edge 0.02
```

### Termux (Android)

```bash
pkg install python git
pip install py-clob-client-v2 requests rich
termux-wake-lock
python gapsniper.py --balance 500 --poll 5
```

---

## CLI Options

| Flag | Default | Description |
|---|---|---|
| `--balance` | `1000` | Starting paper balance |
| `--min-edge` | `0.02` | Minimum edge (2%) to trigger a trade |
| `--poll` | `3.0` | Seconds between scan cycles |
| `--markets` | `50` | Markets to scan per cycle |
| `--fee-rate` | `0.0` | Taker fee rate (0.0156 for 15-min crypto) |

---

## How It Works

```
┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
│ Gamma API   │────▶│ CLOB /book   │────▶│ Edge Calculator │
│ (markets)   │     │ (order books)│     │ 1.0 - (Y+N)     │
└─────────────┘     └──────────────┘     └────────┬────────┘
                                                   │
                                          edge > threshold?
                                                   │
                                          ┌────────▼────────┐
                                          │ Paper Engine    │
                                          │ • fee model     │
                                          │ • slippage      │
                                          │ • balance check │
                                          └────────┬────────┘
                                                   │
                                          ┌────────▼────────┐
                                          │ Terminal Dash   │
                                          │ • P&L           │
                                          │ • trade log     │
                                          └─────────────────┘
```

---

## Roadmap

- [ ] WebSocket streaming for sub-second updates
- [ ] Multi-outcome (NegRisk) arbitrage
- [ ] SQLite trade persistence
- [ ] Exportable HTML report
- [ ] Cross-platform (Kalshi) spread detection

---

## Disclaimer

GapSniper is a **paper-trading simulator for educational purposes**. It does not place real orders. Polymarket's dynamic fee structure and latency make real-world arbitrage extremely competitive. Always validate strategies thoroughly before deploying capital.

---

## License

MIT
