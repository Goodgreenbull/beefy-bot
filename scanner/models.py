from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalise_address(value: str | None) -> str:
    value = (value or "").strip()
    return value.lower() if value.startswith("0x") else value


@dataclass(slots=True)
class Candidate:
    chain: str
    token_address: str
    source: str
    discovered_at: datetime = field(default_factory=utc_now)
    launch_at: datetime | None = None
    pair_address: str | None = None
    name: str | None = None
    symbol: str | None = None
    deployer: str | None = None
    chart_url: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.chain = self.chain.strip().lower()
        self.token_address = normalise_address(self.token_address)
        self.pair_address = normalise_address(self.pair_address)
        self.deployer = normalise_address(self.deployer)
        self.source = self.source.strip().lower()

    @property
    def key(self) -> str:
        return f"{self.chain}:{self.token_address}"

    def to_record(self) -> dict[str, Any]:
        value = asdict(self)
        value["discovered_at"] = self.discovered_at.isoformat()
        value["launch_at"] = self.launch_at.isoformat() if self.launch_at else None
        return value


@dataclass(slots=True)
class MarketSnapshot:
    chain: str
    token_address: str
    captured_at: datetime = field(default_factory=utc_now)
    pair_address: str | None = None
    price_usd: float | None = None
    liquidity_usd: float = 0.0
    market_cap_usd: float | None = None
    fdv_usd: float | None = None
    volume_5m_usd: float = 0.0
    volume_1h_usd: float = 0.0
    volume_24h_usd: float = 0.0
    buys_5m: int = 0
    sells_5m: int = 0
    buys_1h: int = 0
    sells_1h: int = 0
    price_change_5m: float = 0.0
    price_change_1h: float = 0.0
    price_change_24h: float = 0.0
    social_links: int = 0
    boost_score: float = 0.0
    social_velocity: float = 0.0
    smart_wallet_buys: int = 0
    smart_wallet_sells: int = 0
    smart_wallet_net_usd: float = 0.0
    source: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.chain = self.chain.strip().lower()
        self.token_address = normalise_address(self.token_address)
        self.pair_address = normalise_address(self.pair_address)

    def to_record(self) -> dict[str, Any]:
        value = asdict(self)
        value["captured_at"] = self.captured_at.isoformat()
        return value


@dataclass(slots=True)
class SecurityProfile:
    chain: str
    token_address: str
    checked_at: datetime = field(default_factory=utc_now)
    checked: bool = False
    admin_checks_complete: bool = False
    simulation_checked: bool = False
    providers: tuple[str, ...] = ()
    is_honeypot: bool = False
    cannot_buy: bool = False
    cannot_sell: bool = False
    hidden_owner: bool = False
    owner_change_balance: bool = False
    transfer_pausable: bool = False
    blacklist_function: bool = False
    mintable: bool = False
    proxy: bool = False
    open_source: bool | None = None
    buy_tax: float | None = None
    sell_tax: float | None = None
    owner_percent: float | None = None
    top_unlocked_eoa_percent: float | None = None
    lp_locked_percent: float | None = None
    lp_unlocked_percent: float | None = None
    holder_count: int | None = None
    fake_token: bool = False
    creator_percent: float | None = None
    creator_honeypot_count: int = 0
    can_take_back_ownership: bool = False
    selfdestruct: bool = False
    slippage_modifiable: bool = False
    personal_slippage_modifiable: bool = False
    trading_cooldown: bool = False
    risk_level: int | None = None
    risk_label: str | None = None
    flags: tuple[str, ...] = ()
    error: str | None = None

    def __post_init__(self) -> None:
        self.chain = self.chain.strip().lower()
        self.token_address = normalise_address(self.token_address)

    @property
    def key(self) -> str:
        return f"{self.chain}:{self.token_address}"

    def to_record(self) -> dict[str, Any]:
        value = asdict(self)
        value["checked_at"] = self.checked_at.isoformat()
        return value


@dataclass(slots=True)
class ScoreResult:
    score: float
    stage: str
    signal: str
    eligible: bool
    anti_late_penalty: float
    components: dict[str, float]
    drivers: list[str]
    blockers: list[str]
    invalidation: str
    target_multiple: float | None = None
    target_confidence: str = "LOW"
    target_basis: str = "live liquidity/flow structure; history building"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
