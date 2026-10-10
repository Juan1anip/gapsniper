#!/usr/bin/env python3
"""
GapSniper — Polymarket Arbitrage Paper-Trading Bot
Hunt the gap. Keep the edge.

Scans live Polymarket order books for YES + NO ask prices < $1.00
and simulates risk-free arbitrage fills with realistic fee/slippage.

Usage:
    python gapsniper.py --balance 1000 --min-edge 0.02 --poll 2
"""

import os
import time
import json
import argparse
from dataclasses import dataclass, field
from typing import Optional

import requests
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.live import Live
from rich.layout import Layout
from rich import box

# ── Configuration ──────────────────────────────────────────────
CLOB_BASE = "https://clob.polymarket.com"
GAMMA_BASE = "https://gamma-api.polymarket.com"

# Polymarket taker fee model (per-match curve)
# fee = C × rate × p × (1-p), makers exempt
# For most markets the rate is 0 (0% fee).
# Short-duration crypto markets have dynamic fees up to ~3.15%.
DEFAULT_FEE_RATE = 0.0  # set to 0.0156 for 15-min crypto markets

console = Console()

# ── Data Models ────────────────────────────────────────────────
@dataclass
class OrderBook:
    token_id: str
    best_bid: float = 0.0
    best_ask: float = 1.0
    ask_size: float = 0.0
    bid_size: float = 0.0
    tick_size: float = 0.01

@dataclass
class Opportunity:
    market_question: str
    condition_id: str
    yes_token: str
    no_token: str
    yes_ask: float
    no_ask: float
    combined: float
    edge: float          # 1.00 - combined
    yes_ask_size: float
    no_ask_size: float
    max_shares: float
    timestamp: float = field(default_factory=time.time)

@dataclass
class PaperTrade:
    opp: Opportunity
    shares: float
    entry_cost: float
    fee_paid: float
    timestamp: float = field(default_factory=time.time)
    status: str = "open"   # open | resolved_yes | resolved_no

# ── Paper Trading Engine ───────────────────────────────────────
class PaperEngine:
    def __init__(self, balance: float = 1000.0, fee_rate: float = DEFAULT_FEE_RATE):
        self.initial_balance = balance
        self.balance = balance
        self.fee_rate = fee_rate
        self.trades: list[PaperTrade] = []
        self.total_fees = 0.0
        self.total_opportunities_seen = 0

    def _fee(self, shares: float, price: float) -> float:
        """Polymarket-style per-match fee."""
        return shares * self.fee_rate * price * (1 - price)

    def execute(self, opp: Opportunity) -> Optional[PaperTrade]:
        """Simulate buying YES + NO at the observed asks."""
        cost_per_share = opp.yes_ask + opp.no_ask
        # Apply slippage: assume we fill at ask + 1 tick for realism
        slippage = 0.01
        effective_cost = cost_per_share + slippage

        if effective_cost >= 1.0:
            return None  # edge disappeared after slippage

        # Max shares limited by liquidity and balance
        max_by_liq = min(opp.yes_ask_size, opp.no_ask_size)
        max_by_bal = self.balance / effective_cost
        shares = min(max_by_liq, max_by_bal, 50.0)  # cap per trade

        if shares < 1.0:
            return None

        total_cost = shares * effective_cost
        fee = self._fee(shares, opp.yes_ask) + self._fee(shares, opp.no_ask)

        if total_cost + fee > self.balance:
            return None

        self.balance -= (total_cost + fee)
        self.total_fees += fee

        trade = PaperTrade(opp=opp, shares=shares,
                           entry_cost=total_cost, fee_paid=fee)
        self.trades.append(trade)
        return trade

    def resolve(self, trade: PaperTrade, outcome: str):
        """Resolve a trade: winners pay $1/share."""
        if trade.status != "open":
            return
        payout = trade.shares * 1.0  # one side always pays $1
        self.balance += payout
        trade.status = f"resolved_{outcome}"

    @property
    def pnl(self) -> float:
        return self.balance - self.initial_balance

    @property
    def open_trades(self) -> list[PaperTrade]:
        return [t for t in self.trades if t.status == "open"]

    @property
    def total_trades(self) -> int:
        return len(self.trades)

    def summary(self) -> dict:
        return {
            "initial_balance": self.initial_balance,
            "current_balance": round(self.balance, 4),
            "pnl": round(self.pnl, 4),
            "pnl_pct": round(self.pnl / self.initial_balance * 100, 2),
            "total_trades": self.total_trades,
            "open_trades": len(self.open_trades),
            "total_fees_paid": round(self.total_fees, 4),
            "opportunities_scanned": self.total_opportunities_seen,
        }

# ── Market Data Fetching ───────────────────────────────────────
def fetch_active_markets(limit: int = 50) -> list[dict]:
    """Fetch active markets from the Gamma API."""
    try:
        r = requests.get(
            f"{GAMMA_BASE}/markets",
            params={"closed": "false", "limit": limit,
                    "order": "volume", "ascending": "false"},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else data.get("data", [])
    except Exception as e:
        console.print(f"[red]Gamma API error: {e}[/red]")
        return []

def fetch_order_book(token_id: str) -> Optional[OrderBook]:
    """Fetch the full order book for a token (public, no auth)."""
    try:
        r = requests.get(
            f"{CLOB_BASE}/book",
            params={"token_id": token_id},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()

        bids = data.get("bids", [])
        asks = data.get("asks", [])

        ob = OrderBook(token_id=token_id)
        if bids:
            best = max(bids, key=lambda x: float(x["price"]))
            ob.best_bid = float(best["price"])
            ob.bid_size = float(best["size"])
        if asks:
            best = min(asks, key=lambda x: float(x["price"]))
            ob.best_ask = float(best["price"])
            ob.ask_size = float(best["size"])
        ob.tick_size = float(data.get("tick_size", 0.01))
        return ob
    except Exception as e:
        console.print(f"[dim red]Book fetch error ({token_id[:12]}...): {e}[/dim red]")
        return None

# ── Arbitrage Scanner ──────────────────────────────────────────
def scan_market(market: dict) -> Optional[Opportunity]:
    """Check a single market for a YES+NO < $1.00 opportunity."""
    tokens = market.get("tokens") or market.get("clobTokenIds")
    if isinstance(tokens, str):
        try:
            tokens = json.loads(tokens)
        except Exception:
            return None
    if not tokens or len(tokens) < 2:
        return None

    # Polymarket returns tokens as [{token_id, outcome}, ...] or a list of ids
    if isinstance(tokens[0], dict):
        yes_id = next((t["token_id"] for t in tokens
                       if t.get("outcome", "").upper() == "YES"), None)
        no_id = next((t["token_id"] for t in tokens
                      if t.get("outcome", "").upper() == "NO"), None)
    else:
        yes_id, no_id = tokens[0], tokens[1]

    if not yes_id or not no_id:
        return None

    yes_book = fetch_order_book(yes_id)
    if not yes_book:
        return None
    no_book = fetch_order_book(no_id)
    if not no_book:
        return None

    combined = yes_book.best_ask + no_book.best_ask
    edge = 1.0 - combined

    if edge <= 0:
        return None

    question = market.get("question") or market.get("title") or "Unknown"
    condition_id = market.get("condition_id") or market.get("conditionId", "")

    max_shares = min(yes_book.ask_size, no_book.ask_size)

    return Opportunity(
        market_question=question,
        condition_id=condition_id,
        yes_token=yes_id,
        no_token=no_id,
        yes_ask=yes_book.best_ask,
        no_ask=no_book.best_ask,
        combined=combined,
        edge=edge,
        yes_ask_size=yes_book.ask_size,
        no_ask_size=no_book.ask_size,
        max_shares=max_shares,
    )

# ── Dashboard ──────────────────────────────────────────────────
def build_dashboard(engine: PaperEngine, last_opp: Optional[Opportunity],
                    scan_count: int) -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=5),
        Layout(name="body"),
        Layout(name="footer", size=3),
    )
    layout["body"].split_row(
        Layout(name="stats", ratio=1),
        Layout(name="trades", ratio=2),
    )

    # Header
    header_text = Text()
    header_text.append("  ⚡ GAPSNIPER  ", style="bold white on #0D1117")
    header_text.append("  hunt the gap. keep the edge.", style="italic #8B949E")
    layout["header"].update(Panel(header_text, border_style="#3FB950", box=box.HEAVY))

    # Stats panel
    s = engine.summary()
    pnl_color = "#3FB950" if s["pnl"] >= 0 else "#F85149"
    stats_table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    stats_table.add_column("Key", style="#8B949E")
    stats_table.add_column("Value", style="white")
    stats_table.add_row("Balance", f"${s['current_balance']:.2f}")
    stats_table.add_row("P&L", f"[{pnl_color}]${s['pnl']:.2f} ({s['pnl_pct']}%)[/]")
    stats_table.add_row("Trades", str(s["total_trades"]))
    stats_table.add_row("Open", str(s["open_trades"]))
    stats_table.add_row("Fees", f"${s['total_fees_paid']:.4f}")
    stats_table.add_row("Scans", str(scan_count))
    stats_table.add_row("Opps", str(s["opportunities_scanned"]))
    layout["stats"].update(Panel(stats_table, title="[bold]Portfolio[/bold]",
                                 border_style="#D29922", box=box.ROUNDED))

    # Recent trades
    trade_table = Table(box=box.SIMPLE, show_header=True,
                        header_style="bold #D29922", padding=(0, 1))
    trade_table.add_column("Time", style="#8B949E", width=8)
    trade_table.add_column("Market", style="white", max_width=32)
    trade_table.add_column("Edge", style="#3FB950", width=8)
    trade_table.add_column("Shares", style="white", width=8)
    trade_table.add_column("Cost", style="white", width=8)
    trade_table.add_column("Status", width=10)

    for t in engine.trades[-10:][::-1]:
        ts = time.strftime("%H:%M:%S", time.localtime(t.timestamp))
        status_style = "#3FB950" if "resolved" in t.status else "#D29922"
        trade_table.add_row(
            ts,
            t.opp.market_question[:30] + ("…" if len(t.opp.market_question) > 30 else ""),
            f"{t.opp.edge*100:.2f}%",
            f"{t.shares:.1f}",
            f"${t.entry_cost:.2f}",
            f"[{status_style}]{t.status}[/]",
        )
    layout["trades"].update(Panel(trade_table, title="[bold]Recent Trades[/bold]",
                                  border_style="#3FB950", box=box.ROUNDED))

    # Footer
    if last_opp:
        footer = Text()
        footer.append("  Latest: ", style="#8B949E")
        footer.append(f"{last_opp.market_question[:60]}", style="white")
        footer.append(f"  edge={last_opp.edge*100:.2f}%", style="#D29922")
    else:
        footer = Text("  Scanning for opportunities…", style="#8B949E")
    layout["footer"].update(Panel(footer, border_style="#8B949E", box=box.HEAVY))

    return layout

# ── Main Loop ──────────────────────────────────────────────────
def run(args):
    engine = PaperEngine(balance=args.balance, fee_rate=args.fee_rate)
    console.print(Panel.fit(
        "[bold #3FB950]GapSniper v1.0[/bold #3FB950]\n"
        "[#8B949E]Paper-trading mode — no real capital at risk[/#8B949E]\n"
        f"Balance: [white]${args.balance:.2f}[/white]  |  "
        f"Min edge: [white]{args.min_edge*100:.1f}%[/white]  |  "
        f"Poll: [white]{args.poll}s[/white]",
        border_style="#3FB950",
    ))

    scan_count = 0
    last_opp = None
    seen_conditions = set()  # avoid re-trading same market

    with Live(console=console, refresh_per_second=2, screen=True) as live:
        try:
            while True:
                markets = fetch_active_markets(limit=args.markets)
                scan_count += 1

                for m in markets:
                    opp = scan_market(m)
                    if not opp:
                        continue

                    engine.total_opportunities_seen += 1

                    if opp.edge < args.min_edge:
                        continue

                    if opp.condition_id in seen_conditions:
                        continue

                    last_opp = opp
                    trade = engine.execute(opp)
                    if trade:
                        seen_conditions.add(opp.condition_id)
                        console.log(
                            f"[#D29922]⚡ ARB[/#D29922] "
                            f"{opp.market_question[:40]}  "
                            f"edge=[#3FB950]{opp.edge*100:.2f}%[/#3FB950]  "
                            f"shares={trade.shares:.1f}  "
                            f"cost=${trade.entry_cost:.2f}"
                        )

                live.update(build_dashboard(engine, last_opp, scan_count))
                time.sleep(args.poll)

        except KeyboardInterrupt:
            pass

    # Final report
    console.print()
    s = engine.summary()
    table = Table(title="Final Paper Trading Report", box=box.ROUNDED,
                  border_style="#3FB950")
    table.add_column("Metric", style="#8B949E")
    table.add_column("Value", style="white")
    for k, v in s.items():
        table.add_row(k.replace("_", " ").title(), str(v))
    console.print(table)

# ── CLI ────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description="GapSniper — Polymarket Arbitrage Paper Bot")
    p.add_argument("--balance", type=float, default=1000.0,
                   help="Starting paper balance (default: 1000)")
    p.add_argument("--min-edge", type=float, default=0.02,
                   help="Minimum edge to trade, e.g. 0.02 = 2%% (default: 0.02)")
    p.add_argument("--poll", type=float, default=3.0,
                   help="Seconds between scans (default: 3)")
    p.add_argument("--markets", type=int, default=50,
                   help="Number of markets to scan per cycle (default: 50)")
    p.add_argument("--fee-rate", type=float, default=DEFAULT_FEE_RATE,
                   help="Taker fee rate (0 for most markets, ~0.0156 for 15-min crypto)")
    args = p.parse_args()
    run(args)

if __name__ == "__main__":
    main()
