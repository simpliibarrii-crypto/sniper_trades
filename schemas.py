"""API contracts."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ResearchQuery(BaseModel):
    query: str = Field(..., min_length=1, max_length=4000, examples=["BTC 4h accumulation plan"])
    session_id: str = Field(default="default_session", max_length=128)
    reuse_session: bool = Field(
        default=True,
        description="Reuse warm J-Space for multi-turn continuity",
    )
    include_counterfactual: bool = Field(
        default=True,
        description="Attach a light counterfactual branch summary",
    )


class NodeOut(BaseModel):
    id: str
    label: str
    ignition: float
    confidence: float
    evidence: List[Any] = Field(default_factory=list)


class ResearchResponse(BaseModel):
    status: str
    session_id: str
    trader: Optional[str] = "Sniper Trades"
    result_text: Optional[str] = None
    jspace_thoughts: Optional[str] = None
    tv_analysis: Optional[str] = None
    strategy_position: Optional[Dict[str, Any]] = None
    verdict: Optional[Dict[str, Any]] = None
    trade_decision: Optional[Dict[str, Any]] = None
    summary: Optional[str] = None
    mtf_analyses: Optional[Dict[str, Any]] = None
    data_sources: Optional[List[str]] = None
    plan: Optional[Dict[str, Any]] = None
    jspace_active_nodes: List[NodeOut] = Field(default_factory=list)
    counterfactual: Optional[Dict[str, Any]] = None
    provenance: Dict[str, Any] = Field(default_factory=dict)
    metrics: Dict[str, Any] = Field(default_factory=dict)
    latency_ms: float = 0.0
    market_error: Optional[str] = None


class HealthOut(BaseModel):
    ok: bool
    service: str
    version: str


class ReadyOut(BaseModel):
    ready: bool
    sessions: int
    version: str


class SessionListOut(BaseModel):
    sessions: List[Dict[str, Any]]
    count: int


# ── Crypto.com market ──────────────────────────────────────────────────────


class TickerOut(BaseModel):
    source: str
    instrument: str
    last: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    change: Optional[float] = None
    volume: Optional[float] = None
    volume_value: Optional[float] = None
    ts: Optional[Any] = None


class CandleOut(BaseModel):
    t: Optional[Any] = None
    o: Optional[float] = None
    h: Optional[float] = None
    l: Optional[float] = None
    c: Optional[float] = None
    v: Optional[float] = None


class CandlesOut(BaseModel):
    source: str
    instrument: str
    timeframe: str
    count: int
    candles: List[CandleOut] = Field(default_factory=list)
    cached: Optional[bool] = None
    stale: Optional[bool] = None
    cache_age_s: Optional[float] = None
    block_hash: Optional[str] = None
    merkle_root: Optional[str] = None
    engine: Optional[str] = None


class BookLevel(BaseModel):
    price: float
    qty: float


class BookOut(BaseModel):
    source: str
    instrument: str
    bids: List[BookLevel] = Field(default_factory=list)
    asks: List[BookLevel] = Field(default_factory=list)


# ── Copy trade ─────────────────────────────────────────────────────────────


class LeaderCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    symbols: List[str] = Field(default_factory=lambda: ["BTC_USDT"])


class FollowerCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    leader_id: str
    size_multiplier: float = Field(default=1.0, ge=0.01, le=100.0)
    max_notional_usd: float = Field(default=100.0, ge=1.0, le=1_000_000.0)
    mode: str = Field(default="paper", description="paper | live")
    starting_cash: float = Field(default=10_000.0, ge=0.0)


class SignalCreate(BaseModel):
    leader_id: str
    instrument: str = "BTC_USDT"
    side: str = Field(..., description="BUY | SELL")
    order_type: str = Field(default="MARKET", description="MARKET | LIMIT")
    quantity: Optional[float] = Field(default=None, gt=0)
    notional_usd: Optional[float] = Field(default=25.0, gt=0)
    price: Optional[float] = Field(default=None, gt=0)
    note: str = ""
    auto_copy: bool = True
    confirm_live: bool = Field(
        default=False,
        description="If true, live followers submit real orders (default dry-run)",
    )
    confirmation_text: str = Field(
        default="",
        max_length=32,
        description="Must equal CONFIRM LIVE when confirm_live is true",
    )


class CopySignalIn(BaseModel):
    confirm_live: bool = False
    confirmation_text: str = Field(default="", max_length=32)


# ── Risk management ───────────────────────────────────────────────────────


class RiskCalculationIn(BaseModel):
    equity: float = Field(..., gt=0, le=1_000_000_000)
    risk_percent: float = Field(
        default=1.0,
        gt=0,
        le=2.0,
        description="Hard defense ceiling: maximum portfolio loss at stop is 2%",
    )
    entry: float = Field(..., gt=0)
    stop: float = Field(..., gt=0)
    target: Optional[float] = Field(default=None, gt=0)
    max_notional: Optional[float] = Field(default=None, gt=0)


class RiskCalculationOut(BaseModel):
    side: str
    equity: float
    risk_percent: float
    risk_amount: float
    entry: float
    stop: float
    target: Optional[float] = None
    quantity: float
    notional: float
    position_percent: float
    effective_leverage: float
    reward_amount: Optional[float] = None
    risk_reward: Optional[float] = None
    capped: bool = False
    warnings: List[str] = Field(default_factory=list)


class AlertCreate(BaseModel):
    instrument: str = "BTC_USDT"
    direction: str = Field(..., description="above | below")
    target: float = Field(..., gt=0)
    note: str = ""


class GrokCommentIn(BaseModel):
    instrument: str = "BTC_USDT"
    timeframe: str = "1m"
    query: str = "live sniper"
    include_news: bool = True
