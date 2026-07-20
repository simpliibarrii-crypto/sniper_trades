"""
Copy-trade engine for Sniper Trades.

- Leaders emit signals (BUY/SELL)
- Followers mirror with size multiplier
- Default mode is PAPER (local ledger)
- LIVE uses cdcx dry-run unless confirm_live=True (still requires env funds)

Safety: never withdraw; live market orders only when explicitly confirmed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.cdc_market import get_ticker, normalize_instrument

_LOCK = threading.RLock()
_STORE = Path.home() / ".local" / "share" / "sniper_trades" / "copy_trade.json"
_CDCX = shutil.which("cdcx") or os.path.expanduser("~/.local/bin/cdcx")


@dataclass
class Leader:
    leader_id: str
    name: str
    symbols: List[str] = field(default_factory=lambda: ["BTC_USDT"])
    active: bool = True
    created_at: float = field(default_factory=time.time)


@dataclass
class Follower:
    follower_id: str
    name: str
    leader_id: str
    size_multiplier: float = 1.0  # relative notional scale
    max_notional_usd: float = 100.0
    mode: str = "paper"  # paper | live
    active: bool = True
    created_at: float = field(default_factory=time.time)


@dataclass
class Signal:
    signal_id: str
    leader_id: str
    instrument: str
    side: str  # BUY | SELL
    order_type: str  # MARKET | LIMIT
    quantity: Optional[float] = None
    notional_usd: Optional[float] = None
    price: Optional[float] = None
    note: str = ""
    created_at: float = field(default_factory=time.time)


@dataclass
class Fill:
    fill_id: str
    signal_id: str
    follower_id: str
    instrument: str
    side: str
    quantity: float
    price: float
    notional_usd: float
    mode: str
    status: str
    detail: str = ""
    created_at: float = field(default_factory=time.time)


class CopyTradeEngine:
    def __init__(self) -> None:
        self.leaders: Dict[str, Leader] = {}
        self.followers: Dict[str, Follower] = {}
        self.signals: List[Signal] = []
        self.fills: List[Fill] = []
        self.paper_balances: Dict[str, float] = {}  # follower_id -> cash USD
        self.paper_positions: Dict[str, Dict[str, float]] = {}  # fid -> {inst: qty}
        self._load()

    # persistence ---------------------------------------------------------
    def _load(self) -> None:
        if not _STORE.is_file():
            return
        try:
            raw = json.loads(_STORE.read_text())
        except Exception:
            return
        for row in raw.get("leaders", []):
            L = Leader(**row)
            self.leaders[L.leader_id] = L
        for row in raw.get("followers", []):
            F = Follower(**row)
            self.followers[F.follower_id] = F
        for row in raw.get("signals", [])[-200:]:
            self.signals.append(Signal(**row))
        for row in raw.get("fills", [])[-500:]:
            self.fills.append(Fill(**row))
        self.paper_balances = raw.get("paper_balances", {})
        self.paper_positions = raw.get("paper_positions", {})

    def _save(self) -> None:
        _STORE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "leaders": [asdict(x) for x in self.leaders.values()],
            "followers": [asdict(x) for x in self.followers.values()],
            "signals": [asdict(x) for x in self.signals[-200:]],
            "fills": [asdict(x) for x in self.fills[-500:]],
            "paper_balances": self.paper_balances,
            "paper_positions": self.paper_positions,
        }
        _STORE.write_text(json.dumps(payload, indent=2))

    # leaders / followers -------------------------------------------------
    def register_leader(self, name: str, symbols: Optional[List[str]] = None) -> Leader:
        with _LOCK:
            lid = "L_" + uuid.uuid4().hex[:8]
            leader = Leader(
                leader_id=lid,
                name=name.strip() or lid,
                symbols=[normalize_instrument(s) for s in (symbols or ["BTC_USDT"])],
            )
            self.leaders[lid] = leader
            self._save()
            return leader

    def register_follower(
        self,
        name: str,
        leader_id: str,
        size_multiplier: float = 1.0,
        max_notional_usd: float = 100.0,
        mode: str = "paper",
        starting_cash: float = 10_000.0,
    ) -> Follower:
        with _LOCK:
            if leader_id not in self.leaders:
                raise ValueError(f"unknown leader_id: {leader_id}")
            mode = mode.lower().strip()
            if mode not in ("paper", "live"):
                raise ValueError("mode must be paper or live")
            fid = "F_" + uuid.uuid4().hex[:8]
            follower = Follower(
                follower_id=fid,
                name=name.strip() or fid,
                leader_id=leader_id,
                size_multiplier=max(0.01, float(size_multiplier)),
                max_notional_usd=max(1.0, float(max_notional_usd)),
                mode=mode,
            )
            self.followers[fid] = follower
            self.paper_balances.setdefault(fid, float(starting_cash))
            self.paper_positions.setdefault(fid, {})
            self._save()
            return follower

    def list_state(self) -> Dict[str, Any]:
        with _LOCK:
            return {
                "leaders": [asdict(x) for x in self.leaders.values()],
                "followers": [asdict(x) for x in self.followers.values()],
                "signals": [asdict(x) for x in self.signals[-50:]],
                "fills": [asdict(x) for x in self.fills[-50:]],
                "paper_balances": dict(self.paper_balances),
                "paper_positions": dict(self.paper_positions),
            }

    # signals -------------------------------------------------------------
    def emit_signal(
        self,
        leader_id: str,
        instrument: str,
        side: str,
        order_type: str = "MARKET",
        quantity: Optional[float] = None,
        notional_usd: Optional[float] = None,
        price: Optional[float] = None,
        note: str = "",
        auto_copy: bool = True,
        confirm_live: bool = False,
    ) -> Dict[str, Any]:
        side = side.upper().strip()
        order_type = order_type.upper().strip()
        if side not in ("BUY", "SELL"):
            raise ValueError("side must be BUY or SELL")
        if order_type not in ("MARKET", "LIMIT"):
            raise ValueError("order_type must be MARKET or LIMIT")
        inst = normalize_instrument(instrument)
        if not quantity and not notional_usd:
            notional_usd = 25.0

        with _LOCK:
            if leader_id not in self.leaders:
                raise ValueError(f"unknown leader_id: {leader_id}")
            sig = Signal(
                signal_id="S_" + uuid.uuid4().hex[:10],
                leader_id=leader_id,
                instrument=inst,
                side=side,
                order_type=order_type,
                quantity=quantity,
                notional_usd=notional_usd,
                price=price,
                note=note or "",
            )
            self.signals.append(sig)
            self._save()

        fills: List[Dict[str, Any]] = []
        if auto_copy:
            fills = self.copy_signal(sig.signal_id, confirm_live=confirm_live)
        return {"signal": asdict(sig), "fills": fills}

    def copy_signal(
        self, signal_id: str, confirm_live: bool = False
    ) -> List[Dict[str, Any]]:
        with _LOCK:
            sig = next((s for s in self.signals if s.signal_id == signal_id), None)
            if not sig:
                raise ValueError(f"unknown signal_id: {signal_id}")
            followers = [
                f
                for f in self.followers.values()
                if f.active and f.leader_id == sig.leader_id
            ]

        out: List[Dict[str, Any]] = []
        for f in followers:
            try:
                fill = self._execute_for_follower(sig, f, confirm_live=confirm_live)
                out.append(asdict(fill))
            except Exception as exc:  # noqa: BLE001
                err = Fill(
                    fill_id="X_" + uuid.uuid4().hex[:8],
                    signal_id=sig.signal_id,
                    follower_id=f.follower_id,
                    instrument=sig.instrument,
                    side=sig.side,
                    quantity=0.0,
                    price=0.0,
                    notional_usd=0.0,
                    mode=f.mode,
                    status="error",
                    detail=str(exc),
                )
                with _LOCK:
                    self.fills.append(err)
                    self._save()
                out.append(asdict(err))
        return out

    def _execute_for_follower(
        self, sig: Signal, follower: Follower, confirm_live: bool
    ) -> Fill:
        ticker = get_ticker(sig.instrument)
        px = sig.price or ticker.get("last") or ticker.get("ask") or ticker.get("bid")
        if not px or px <= 0:
            raise RuntimeError("no price available for sizing")

        # size
        if sig.quantity and sig.quantity > 0:
            qty = float(sig.quantity) * follower.size_multiplier
        else:
            notion = float(sig.notional_usd or 25.0) * follower.size_multiplier
            notion = min(notion, follower.max_notional_usd)
            qty = notion / float(px)
        qty = float(f"{qty:.8f}")
        notional = qty * float(px)

        if follower.mode == "paper":
            return self._paper_fill(sig, follower, qty, float(px), notional)

        # live path
        if not confirm_live:
            detail = self._cdcx_order(
                sig.side, sig.instrument, qty, sig.order_type, sig.price, dry_run=True
            )
            fill = Fill(
                fill_id="D_" + uuid.uuid4().hex[:8],
                signal_id=sig.signal_id,
                follower_id=follower.follower_id,
                instrument=sig.instrument,
                side=sig.side,
                quantity=qty,
                price=float(px),
                notional_usd=notional,
                mode="live",
                status="dry_run",
                detail=detail[:500],
            )
            with _LOCK:
                self.fills.append(fill)
                self._save()
            return fill

        detail = self._cdcx_order(
            sig.side, sig.instrument, qty, sig.order_type, sig.price, dry_run=False
        )
        fill = Fill(
            fill_id="L_" + uuid.uuid4().hex[:8],
            signal_id=sig.signal_id,
            follower_id=follower.follower_id,
            instrument=sig.instrument,
            side=sig.side,
            quantity=qty,
            price=float(px),
            notional_usd=notional,
            mode="live",
            status="submitted",
            detail=detail[:500],
        )
        with _LOCK:
            self.fills.append(fill)
            self._save()
        return fill

    def _paper_fill(
        self,
        sig: Signal,
        follower: Follower,
        qty: float,
        px: float,
        notional: float,
    ) -> Fill:
        with _LOCK:
            cash = float(self.paper_balances.get(follower.follower_id, 10_000.0))
            pos = self.paper_positions.setdefault(follower.follower_id, {})
            held = float(pos.get(sig.instrument, 0.0))
            if sig.side == "BUY":
                if cash < notional:
                    raise RuntimeError(
                        f"paper cash insufficient: need {notional:.2f}, have {cash:.2f}"
                    )
                cash -= notional
                pos[sig.instrument] = held + qty
            else:
                if held < qty:
                    # allow flat short paper as reduce-only fail → sell max held
                    if held <= 0:
                        raise RuntimeError("paper position empty; cannot SELL")
                    qty = held
                    notional = qty * px
                pos[sig.instrument] = held - qty
                cash += notional
            self.paper_balances[follower.follower_id] = cash
            fill = Fill(
                fill_id="P_" + uuid.uuid4().hex[:8],
                signal_id=sig.signal_id,
                follower_id=follower.follower_id,
                instrument=sig.instrument,
                side=sig.side,
                quantity=qty,
                price=px,
                notional_usd=notional,
                mode="paper",
                status="filled",
                detail=f"paper cash={cash:.2f}",
            )
            self.fills.append(fill)
            self._save()
            return fill

    def _cdcx_order(
        self,
        side: str,
        instrument: str,
        qty: float,
        order_type: str,
        price: Optional[float],
        dry_run: bool,
    ) -> str:
        if not _CDCX or not os.path.isfile(_CDCX):
            raise RuntimeError("cdcx not installed for live copy")
        args = [
            _CDCX,
            "trade",
            "order",
            side,
            instrument,
            f"{qty:.8f}",
            "--type",
            order_type,
            "-o",
            "json",
        ]
        if order_type == "LIMIT":
            if not price:
                raise RuntimeError("LIMIT requires price")
            args.extend(["--price", str(price)])
        if dry_run:
            args.append("--dry-run")
        env = os.environ.copy()
        cred = os.path.expanduser("~/.config/crypto-com/credentials.env")
        if os.path.isfile(cred):
            with open(cred) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    if line.startswith("export "):
                        line = line[len("export ") :]
                    k, _, v = line.partition("=")
                    env[k.strip()] = v.strip().strip('"').strip("'")
        proc = subprocess.run(
            args, capture_output=True, text=True, timeout=30, env=env
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode != 0:
            raise RuntimeError(out.strip() or "cdcx order failed")
        return out.strip()


_engine: Optional[CopyTradeEngine] = None


def get_engine() -> CopyTradeEngine:
    global _engine
    if _engine is None:
        _engine = CopyTradeEngine()
    return _engine
