from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .models import Candidate, MarketSnapshot, ScoreResult


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class SQLiteState:
    """Small durable state store; contains market observations, never credentials."""

    def __init__(self, path: str) -> None:
        db_path = Path(path)
        if db_path.parent != Path("."):
            db_path.parent.mkdir(parents=True, exist_ok=True)
        self.path = str(db_path)
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self._create_schema()

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS candidates (
                candidate_key TEXT PRIMARY KEY,
                chain TEXT NOT NULL,
                token_address TEXT NOT NULL,
                pair_address TEXT,
                source TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                launch_at TEXT,
                name TEXT,
                symbol TEXT,
                deployer TEXT,
                chart_url TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                last_score REAL,
                last_stage TEXT,
                last_signal TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_candidates_launch ON candidates(launch_at);
            CREATE INDEX IF NOT EXISTS idx_candidates_seen ON candidates(first_seen_at);

            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_key TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                pair_address TEXT,
                price_usd REAL,
                liquidity_usd REAL NOT NULL,
                market_cap_usd REAL,
                fdv_usd REAL,
                volume_5m_usd REAL NOT NULL,
                volume_1h_usd REAL NOT NULL,
                volume_24h_usd REAL NOT NULL,
                buys_5m INTEGER NOT NULL,
                sells_5m INTEGER NOT NULL,
                buys_1h INTEGER NOT NULL,
                sells_1h INTEGER NOT NULL,
                price_change_5m REAL NOT NULL,
                price_change_1h REAL NOT NULL,
                price_change_24h REAL NOT NULL,
                social_links INTEGER NOT NULL,
                boost_score REAL NOT NULL,
                social_velocity REAL NOT NULL,
                smart_wallet_buys INTEGER NOT NULL,
                smart_wallet_sells INTEGER NOT NULL,
                smart_wallet_net_usd REAL NOT NULL,
                source TEXT NOT NULL,
                raw_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(candidate_key) REFERENCES candidates(candidate_key)
            );
            CREATE INDEX IF NOT EXISTS idx_snapshots_candidate_time
                ON snapshots(candidate_key, captured_at DESC);

            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_key TEXT NOT NULL,
                sent_at TEXT NOT NULL,
                stage TEXT NOT NULL,
                signal TEXT NOT NULL,
                score REAL NOT NULL,
                payload_json TEXT NOT NULL,
                FOREIGN KEY(candidate_key) REFERENCES candidates(candidate_key)
            );
            CREATE INDEX IF NOT EXISTS idx_alerts_candidate_time
                ON alerts(candidate_key, sent_at DESC);

            CREATE TABLE IF NOT EXISTS cursors (
                cursor_key TEXT PRIMARY KEY,
                cursor_value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS feed_health (
                feed_name TEXT PRIMARY KEY,
                last_success_at TEXT,
                last_error_at TEXT,
                last_error TEXT,
                items_seen INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        self.connection.commit()

    def upsert_candidate(self, candidate: Candidate) -> None:
        now = datetime.now(timezone.utc).isoformat()
        existing = self.connection.execute(
            "SELECT metadata_json FROM candidates WHERE candidate_key = ?", (candidate.key,)
        ).fetchone()
        metadata = json.loads(existing["metadata_json"]) if existing else {}
        metadata.update(candidate.metadata)
        self.connection.execute(
            """
            INSERT INTO candidates (
                candidate_key, chain, token_address, pair_address, source,
                first_seen_at, last_seen_at, launch_at, name, symbol, deployer,
                chart_url, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(candidate_key) DO UPDATE SET
                pair_address = COALESCE(excluded.pair_address, candidates.pair_address),
                source = CASE
                    WHEN instr(candidates.source, excluded.source) > 0 THEN candidates.source
                    ELSE candidates.source || ',' || excluded.source
                END,
                last_seen_at = excluded.last_seen_at,
                launch_at = COALESCE(candidates.launch_at, excluded.launch_at),
                name = COALESCE(excluded.name, candidates.name),
                symbol = COALESCE(excluded.symbol, candidates.symbol),
                deployer = COALESCE(excluded.deployer, candidates.deployer),
                chart_url = COALESCE(excluded.chart_url, candidates.chart_url),
                metadata_json = excluded.metadata_json
            """,
            (
                candidate.key,
                candidate.chain,
                candidate.token_address,
                candidate.pair_address,
                candidate.source,
                candidate.discovered_at.isoformat(),
                now,
                candidate.launch_at.isoformat() if candidate.launch_at else None,
                candidate.name,
                candidate.symbol,
                candidate.deployer,
                candidate.chart_url,
                json.dumps(metadata, separators=(",", ":")),
            ),
        )
        self.connection.commit()

    def list_active_candidates(self, max_age_hours: int, limit: int) -> list[Candidate]:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat()
        rows = self.connection.execute(
            """
            SELECT * FROM candidates
            WHERE COALESCE(launch_at, first_seen_at) >= ?
            ORDER BY COALESCE(launch_at, first_seen_at) DESC
            LIMIT ?
            """,
            (cutoff, limit),
        ).fetchall()
        return [
            Candidate(
                chain=row["chain"],
                token_address=row["token_address"],
                pair_address=row["pair_address"],
                source=row["source"],
                discovered_at=_dt(row["first_seen_at"]) or datetime.now(timezone.utc),
                launch_at=_dt(row["launch_at"]),
                name=row["name"],
                symbol=row["symbol"],
                deployer=row["deployer"],
                chart_url=row["chart_url"],
                metadata=json.loads(row["metadata_json"] or "{}"),
            )
            for row in rows
        ]

    def add_snapshot(self, candidate_key: str, snapshot: MarketSnapshot) -> None:
        self.connection.execute(
            """
            INSERT INTO snapshots (
                candidate_key, captured_at, pair_address, price_usd, liquidity_usd,
                market_cap_usd, fdv_usd, volume_5m_usd, volume_1h_usd,
                volume_24h_usd, buys_5m, sells_5m, buys_1h, sells_1h,
                price_change_5m, price_change_1h, price_change_24h, social_links,
                boost_score, social_velocity, smart_wallet_buys, smart_wallet_sells,
                smart_wallet_net_usd, source, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate_key,
                snapshot.captured_at.isoformat(),
                snapshot.pair_address,
                snapshot.price_usd,
                snapshot.liquidity_usd,
                snapshot.market_cap_usd,
                snapshot.fdv_usd,
                snapshot.volume_5m_usd,
                snapshot.volume_1h_usd,
                snapshot.volume_24h_usd,
                snapshot.buys_5m,
                snapshot.sells_5m,
                snapshot.buys_1h,
                snapshot.sells_1h,
                snapshot.price_change_5m,
                snapshot.price_change_1h,
                snapshot.price_change_24h,
                snapshot.social_links,
                snapshot.boost_score,
                snapshot.social_velocity,
                snapshot.smart_wallet_buys,
                snapshot.smart_wallet_sells,
                snapshot.smart_wallet_net_usd,
                snapshot.source,
                json.dumps(snapshot.raw, separators=(",", ":")),
            ),
        )
        self.connection.commit()

    def recent_snapshots(self, candidate_key: str, limit: int = 12) -> list[MarketSnapshot]:
        rows = self.connection.execute(
            "SELECT * FROM snapshots WHERE candidate_key = ? ORDER BY captured_at DESC LIMIT ?",
            (candidate_key, limit),
        ).fetchall()
        return [
            MarketSnapshot(
                chain=candidate_key.split(":", 1)[0],
                token_address=candidate_key.split(":", 1)[1],
                captured_at=_dt(row["captured_at"]) or datetime.now(timezone.utc),
                pair_address=row["pair_address"],
                price_usd=row["price_usd"],
                liquidity_usd=row["liquidity_usd"],
                market_cap_usd=row["market_cap_usd"],
                fdv_usd=row["fdv_usd"],
                volume_5m_usd=row["volume_5m_usd"],
                volume_1h_usd=row["volume_1h_usd"],
                volume_24h_usd=row["volume_24h_usd"],
                buys_5m=row["buys_5m"],
                sells_5m=row["sells_5m"],
                buys_1h=row["buys_1h"],
                sells_1h=row["sells_1h"],
                price_change_5m=row["price_change_5m"],
                price_change_1h=row["price_change_1h"],
                price_change_24h=row["price_change_24h"],
                social_links=row["social_links"],
                boost_score=row["boost_score"],
                social_velocity=row["social_velocity"],
                smart_wallet_buys=row["smart_wallet_buys"],
                smart_wallet_sells=row["smart_wallet_sells"],
                smart_wallet_net_usd=row["smart_wallet_net_usd"],
                source=row["source"],
                raw=json.loads(row["raw_json"] or "{}"),
            )
            for row in rows
        ]

    def update_score(self, candidate_key: str, result: ScoreResult) -> None:
        self.connection.execute(
            "UPDATE candidates SET last_score = ?, last_stage = ?, last_signal = ? WHERE candidate_key = ?",
            (result.score, result.stage, result.signal, candidate_key),
        )
        self.connection.commit()

    def alert_allowed(
        self,
        candidate_key: str,
        result: ScoreResult,
        cooldown_minutes: int,
        score_upgrade: float,
    ) -> bool:
        row = self.connection.execute(
            "SELECT sent_at, stage, score FROM alerts WHERE candidate_key = ? ORDER BY sent_at DESC LIMIT 1",
            (candidate_key,),
        ).fetchone()
        if not row:
            return True
        sent_at = _dt(row["sent_at"]) or datetime.now(timezone.utc)
        cooling_down = datetime.now(timezone.utc) - sent_at < timedelta(minutes=cooldown_minutes)
        meaningful_upgrade = result.score >= float(row["score"]) + score_upgrade
        new_stage = result.stage != row["stage"]
        return meaningful_upgrade or (new_stage and not cooling_down) or not cooling_down

    def record_alert(self, candidate_key: str, result: ScoreResult) -> None:
        self.connection.execute(
            "INSERT INTO alerts(candidate_key, sent_at, stage, signal, score, payload_json) VALUES (?, ?, ?, ?, ?, ?)",
            (
                candidate_key,
                datetime.now(timezone.utc).isoformat(),
                result.stage,
                result.signal,
                result.score,
                json.dumps(result.to_dict(), separators=(",", ":")),
            ),
        )
        self.connection.commit()

    def get_cursor(self, key: str) -> str | None:
        row = self.connection.execute("SELECT cursor_value FROM cursors WHERE cursor_key = ?", (key,)).fetchone()
        return row["cursor_value"] if row else None

    def set_cursor(self, key: str, value: str) -> None:
        self.connection.execute(
            """
            INSERT INTO cursors(cursor_key, cursor_value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(cursor_key) DO UPDATE SET cursor_value = excluded.cursor_value, updated_at = excluded.updated_at
            """,
            (key, value, datetime.now(timezone.utc).isoformat()),
        )
        self.connection.commit()

    def mark_feed_success(self, name: str, count: int) -> None:
        self.connection.execute(
            """
            INSERT INTO feed_health(feed_name, last_success_at, last_error_at, last_error, items_seen)
            VALUES (?, ?, NULL, NULL, ?)
            ON CONFLICT(feed_name) DO UPDATE SET
                last_success_at = excluded.last_success_at,
                last_error = NULL,
                items_seen = feed_health.items_seen + excluded.items_seen
            """,
            (name, datetime.now(timezone.utc).isoformat(), count),
        )
        self.connection.commit()

    def mark_feed_error(self, name: str, error: Exception) -> None:
        message = f"{type(error).__name__}: {error}"[:300]
        self.connection.execute(
            """
            INSERT INTO feed_health(feed_name, last_success_at, last_error_at, last_error, items_seen)
            VALUES (?, NULL, ?, ?, 0)
            ON CONFLICT(feed_name) DO UPDATE SET last_error_at = excluded.last_error_at, last_error = excluded.last_error
            """,
            (name, datetime.now(timezone.utc).isoformat(), message),
        )
        self.connection.commit()

    def health(self) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM feed_health ORDER BY feed_name").fetchall()
        return [dict(row) for row in rows]

    def stats(self) -> dict[str, int]:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        return {
            "candidates_24h": self.connection.execute(
                "SELECT COUNT(*) FROM candidates WHERE first_seen_at >= ?", (cutoff,)
            ).fetchone()[0],
            "snapshots_24h": self.connection.execute(
                "SELECT COUNT(*) FROM snapshots WHERE captured_at >= ?", (cutoff,)
            ).fetchone()[0],
            "alerts_24h": self.connection.execute(
                "SELECT COUNT(*) FROM alerts WHERE sent_at >= ?", (cutoff,)
            ).fetchone()[0],
        }

    def prune(self, snapshot_days: int = 7, alert_days: int = 90) -> None:
        snapshot_cutoff = (datetime.now(timezone.utc) - timedelta(days=snapshot_days)).isoformat()
        alert_cutoff = (datetime.now(timezone.utc) - timedelta(days=alert_days)).isoformat()
        self.connection.execute("DELETE FROM snapshots WHERE captured_at < ?", (snapshot_cutoff,))
        self.connection.execute("DELETE FROM alerts WHERE sent_at < ?", (alert_cutoff,))
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()
