from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any

from .models import Candidate, MarketSnapshot, ScoreResult, SecurityProfile
from .targeting import combine_target, structural_target


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
        self.connection.execute("PRAGMA foreign_keys=ON")
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
                last_signal TEXT,
                last_result_json TEXT NOT NULL DEFAULT '{}',
                first_detected_market_cap_usd REAL,
                first_detected_market_at TEXT
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
                unique_buyers_5m INTEGER NOT NULL DEFAULT 0,
                unique_buyers_15m INTEGER NOT NULL DEFAULT 0,
                unique_sellers_5m INTEGER NOT NULL DEFAULT 0,
                unique_sellers_15m INTEGER NOT NULL DEFAULT 0,
                net_new_wallets_5m INTEGER NOT NULL DEFAULT 0,
                net_new_wallets_15m INTEGER NOT NULL DEFAULT 0,
                holder_count INTEGER,
                exact_ca_mentions_5m INTEGER NOT NULL DEFAULT 0,
                exact_ca_mentions_15m INTEGER NOT NULL DEFAULT 0,
                credible_social_mentions_5m INTEGER NOT NULL DEFAULT 0,
                creator_reputation REAL NOT NULL DEFAULT 0,
                narrative_score REAL NOT NULL DEFAULT 0,
                deployer_sells_15m INTEGER NOT NULL DEFAULT 0,
                flow_checked INTEGER NOT NULL DEFAULT 0,
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
                entry_price_usd REAL,
                entry_market_cap_usd REAL,
                entry_liquidity_usd REAL,
                first_detected_market_cap_usd REAL,
                alert_market_cap_usd REAL,
                current_market_cap_usd REAL,
                peak_after_alert_market_cap_usd REAL,
                mfe_pct REAL,
                mae_pct REAL,
                payload_json TEXT NOT NULL,
                FOREIGN KEY(candidate_key) REFERENCES candidates(candidate_key)
            );
            CREATE INDEX IF NOT EXISTS idx_alerts_candidate_time
                ON alerts(candidate_key, sent_at DESC);

            CREATE TABLE IF NOT EXISTS alert_outcomes (
                alert_id INTEGER NOT NULL,
                horizon_minutes INTEGER NOT NULL,
                due_at TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                price_usd REAL,
                market_cap_usd REAL,
                liquidity_usd REAL,
                return_pct REAL,
                capture_lag_minutes REAL,
                mfe_pct REAL,
                mae_pct REAL,
                PRIMARY KEY(alert_id, horizon_minutes),
                FOREIGN KEY(alert_id) REFERENCES alerts(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_alert_outcomes_horizon
                ON alert_outcomes(horizon_minutes, captured_at DESC);

            CREATE TABLE IF NOT EXISTS alert_wallets (
                alert_id INTEGER NOT NULL,
                chain TEXT NOT NULL,
                wallet TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                PRIMARY KEY(alert_id, chain, wallet),
                FOREIGN KEY(alert_id) REFERENCES alerts(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS wallet_reputation (
                chain TEXT NOT NULL,
                wallet TEXT NOT NULL,
                evaluated_alerts INTEGER NOT NULL DEFAULT 0,
                wins INTEGER NOT NULL DEFAULT 0,
                total_return_pct REAL NOT NULL DEFAULT 0,
                last_return_pct REAL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(chain, wallet)
            );

            CREATE TABLE IF NOT EXISTS wallet_outcomes (
                chain TEXT NOT NULL,
                wallet TEXT NOT NULL,
                candidate_key TEXT NOT NULL,
                evaluated_at TEXT NOT NULL,
                return_pct REAL NOT NULL,
                won INTEGER NOT NULL,
                PRIMARY KEY(chain, wallet, candidate_key)
            );

            CREATE TABLE IF NOT EXISTS candidate_market_failures (
                candidate_key TEXT PRIMARY KEY,
                consecutive INTEGER NOT NULL DEFAULT 0,
                last_checked_at TEXT NOT NULL,
                FOREIGN KEY(candidate_key) REFERENCES candidates(candidate_key) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS security_profiles (
                candidate_key TEXT PRIMARY KEY,
                checked_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                FOREIGN KEY(candidate_key) REFERENCES candidates(candidate_key) ON DELETE CASCADE
            );

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
        self._ensure_column("alerts", "entry_price_usd", "REAL")
        self._ensure_column("alerts", "entry_market_cap_usd", "REAL")
        self._ensure_column("alerts", "entry_liquidity_usd", "REAL")
        self._ensure_column("candidates", "first_detected_market_cap_usd", "REAL")
        self._ensure_column("candidates", "first_detected_market_at", "TEXT")
        self._ensure_column("candidates", "last_result_json", "TEXT NOT NULL DEFAULT '{}'")
        for column, declaration in (
            ("unique_buyers_5m", "INTEGER NOT NULL DEFAULT 0"),
            ("unique_buyers_15m", "INTEGER NOT NULL DEFAULT 0"),
            ("unique_sellers_5m", "INTEGER NOT NULL DEFAULT 0"),
            ("unique_sellers_15m", "INTEGER NOT NULL DEFAULT 0"),
            ("net_new_wallets_5m", "INTEGER NOT NULL DEFAULT 0"),
            ("net_new_wallets_15m", "INTEGER NOT NULL DEFAULT 0"),
            ("holder_count", "INTEGER"),
            ("exact_ca_mentions_5m", "INTEGER NOT NULL DEFAULT 0"),
            ("exact_ca_mentions_15m", "INTEGER NOT NULL DEFAULT 0"),
            ("credible_social_mentions_5m", "INTEGER NOT NULL DEFAULT 0"),
            ("creator_reputation", "REAL NOT NULL DEFAULT 0"),
            ("narrative_score", "REAL NOT NULL DEFAULT 0"),
            ("deployer_sells_15m", "INTEGER NOT NULL DEFAULT 0"),
            ("flow_checked", "INTEGER NOT NULL DEFAULT 0"),
        ):
            self._ensure_column("snapshots", column, declaration)
        for column in (
            "first_detected_market_cap_usd",
            "alert_market_cap_usd",
            "current_market_cap_usd",
            "peak_after_alert_market_cap_usd",
        ):
            self._ensure_column("alerts", column, "REAL")
        self._ensure_column("alerts", "mfe_pct", "REAL")
        self._ensure_column("alerts", "mae_pct", "REAL")
        self._ensure_column("alert_outcomes", "capture_lag_minutes", "REAL")
        self._ensure_column("alert_outcomes", "mfe_pct", "REAL")
        self._ensure_column("alert_outcomes", "mae_pct", "REAL")
        self.connection.commit()

    def _ensure_column(self, table: str, column: str, declaration: str) -> None:
        columns = {row["name"] for row in self.connection.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

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

    @staticmethod
    def _candidate_from_row(row: sqlite3.Row) -> Candidate:
        metadata = json.loads(row["metadata_json"] or "{}")
        metadata["first_detected_market_cap_usd"] = row["first_detected_market_cap_usd"]
        metadata["first_detected_market_at"] = row["first_detected_market_at"]
        metadata["_has_score"] = row["last_score"] is not None
        return Candidate(
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
            metadata=metadata,
        )

    def list_active_candidates(self, max_age_hours: int, limit: int) -> list[Candidate]:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat()
        fresh_limit = max(1, min(limit, int(limit * 0.7)))
        recheck_limit = max(0, limit - fresh_limit)
        fresh_rows = self.connection.execute(
            """
            SELECT * FROM candidates
            WHERE COALESCE(launch_at, first_seen_at) >= ?
            ORDER BY COALESCE(launch_at, first_seen_at) DESC
            LIMIT ?
            """,
            (cutoff, fresh_limit),
        ).fetchall()
        rows = list(fresh_rows)
        if recheck_limit:
            selected = [row["candidate_key"] for row in fresh_rows]
            exclusion = ""
            parameters: list[Any] = [cutoff]
            if selected:
                placeholders = ",".join("?" for _ in selected)
                exclusion = f"AND c.candidate_key NOT IN ({placeholders})"
                parameters.extend(selected)
            parameters.append(recheck_limit)
            recheck_rows = self.connection.execute(
                f"""
                SELECT c.*, MAX(s.captured_at) AS last_snapshot_at
                FROM candidates c
                LEFT JOIN snapshots s ON s.candidate_key = c.candidate_key
                WHERE COALESCE(c.launch_at, c.first_seen_at) >= ?
                {exclusion}
                GROUP BY c.candidate_key
                ORDER BY
                    CASE WHEN MAX(s.captured_at) IS NULL THEN 0 ELSE 1 END,
                    MAX(s.captured_at) ASC,
                    COALESCE(c.last_score, 0) DESC,
                    COALESCE(c.launch_at, c.first_seen_at) DESC
                LIMIT ?
                """,
                parameters,
            ).fetchall()
            rows.extend(recheck_rows)
        return [self._candidate_from_row(row) for row in rows]

    def list_outcome_candidates(self, limit: int = 50) -> list[Candidate]:
        # Keep a grace window for free-tier sleeps/outages so 24h outcomes are
        # still collected after the service wakes up.
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        rows = self.connection.execute(
            """
            WITH horizons(minutes) AS (VALUES (15), (60), (360), (1440)),
            pending_alerts AS (
                SELECT
                    a.candidate_key,
                    MIN(strftime('%s', a.sent_at) + horizons.minutes * 60) AS next_due_epoch
                FROM alerts a
                CROSS JOIN horizons
                LEFT JOIN alert_outcomes o
                  ON o.alert_id = a.id AND o.horizon_minutes = horizons.minutes
                WHERE a.sent_at >= ?
                  AND a.entry_price_usd IS NOT NULL
                  AND o.alert_id IS NULL
                GROUP BY a.id, a.candidate_key
            ),
            candidate_due AS (
                SELECT candidate_key, MIN(next_due_epoch) AS next_due_epoch
                FROM pending_alerts
                GROUP BY candidate_key
            )
            SELECT c.*, candidate_due.next_due_epoch
            FROM candidates c
            JOIN candidate_due ON candidate_due.candidate_key = c.candidate_key
            ORDER BY
                CASE WHEN candidate_due.next_due_epoch <= strftime('%s', 'now') THEN 0 ELSE 1 END,
                ABS(candidate_due.next_due_epoch - strftime('%s', 'now')),
                candidate_due.next_due_epoch
            LIMIT ?
            """,
            (cutoff, limit),
        ).fetchall()
        return [self._candidate_from_row(row) for row in rows]

    def add_snapshot(self, candidate_key: str, snapshot: MarketSnapshot) -> None:
        market_cap = snapshot.market_cap_usd or snapshot.fdv_usd
        if market_cap and market_cap > 0:
            self.connection.execute(
                """
                UPDATE candidates SET
                    first_detected_market_cap_usd = COALESCE(first_detected_market_cap_usd, ?),
                    first_detected_market_at = COALESCE(first_detected_market_at, ?)
                WHERE candidate_key = ?
                """,
                (market_cap, snapshot.captured_at.isoformat(), candidate_key),
            )
        self.connection.execute(
            """
            INSERT INTO snapshots (
                candidate_key, captured_at, pair_address, price_usd, liquidity_usd,
                market_cap_usd, fdv_usd, volume_5m_usd, volume_1h_usd,
                volume_24h_usd, buys_5m, sells_5m, buys_1h, sells_1h,
                price_change_5m, price_change_1h, price_change_24h, social_links,
                boost_score, social_velocity, smart_wallet_buys, smart_wallet_sells,
                smart_wallet_net_usd, unique_buyers_5m, unique_buyers_15m,
                unique_sellers_5m, unique_sellers_15m, net_new_wallets_5m,
                net_new_wallets_15m, holder_count, exact_ca_mentions_5m,
                exact_ca_mentions_15m, credible_social_mentions_5m,
                creator_reputation, narrative_score, deployer_sells_15m,
                flow_checked, source, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                snapshot.unique_buyers_5m,
                snapshot.unique_buyers_15m,
                snapshot.unique_sellers_5m,
                snapshot.unique_sellers_15m,
                snapshot.net_new_wallets_5m,
                snapshot.net_new_wallets_15m,
                snapshot.holder_count,
                snapshot.exact_ca_mentions_5m,
                snapshot.exact_ca_mentions_15m,
                snapshot.credible_social_mentions_5m,
                snapshot.creator_reputation,
                snapshot.narrative_score,
                snapshot.deployer_sells_15m,
                int(snapshot.flow_checked),
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
                unique_buyers_5m=row["unique_buyers_5m"],
                unique_buyers_15m=row["unique_buyers_15m"],
                unique_sellers_5m=row["unique_sellers_5m"],
                unique_sellers_15m=row["unique_sellers_15m"],
                net_new_wallets_5m=row["net_new_wallets_5m"],
                net_new_wallets_15m=row["net_new_wallets_15m"],
                holder_count=row["holder_count"],
                exact_ca_mentions_5m=row["exact_ca_mentions_5m"],
                exact_ca_mentions_15m=row["exact_ca_mentions_15m"],
                credible_social_mentions_5m=row["credible_social_mentions_5m"],
                creator_reputation=row["creator_reputation"],
                narrative_score=row["narrative_score"],
                deployer_sells_15m=row["deployer_sells_15m"],
                flow_checked=bool(row["flow_checked"]),
                source=row["source"],
                raw=json.loads(row["raw_json"] or "{}"),
            )
            for row in rows
        ]

    def get_security_profile(
        self, candidate_key: str, max_age_minutes: int
    ) -> SecurityProfile | None:
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)).isoformat()
        row = self.connection.execute(
            "SELECT checked_at, payload_json FROM security_profiles WHERE candidate_key = ? AND checked_at >= ?",
            (candidate_key, cutoff),
        ).fetchone()
        if not row:
            return None
        payload = json.loads(row["payload_json"] or "{}")
        payload["checked_at"] = _dt(row["checked_at"]) or datetime.now(timezone.utc)
        payload["providers"] = tuple(payload.get("providers") or ())
        payload["flags"] = tuple(payload.get("flags") or ())
        try:
            return SecurityProfile(**payload)
        except TypeError:
            return None

    def upsert_security_profile(self, profile: SecurityProfile) -> None:
        payload = profile.to_record()
        payload.pop("checked_at", None)
        self.connection.execute(
            """
            INSERT INTO security_profiles(candidate_key, checked_at, payload_json) VALUES (?, ?, ?)
            ON CONFLICT(candidate_key) DO UPDATE SET
                checked_at = excluded.checked_at,
                payload_json = excluded.payload_json
            """,
            (
                profile.key,
                profile.checked_at.isoformat(),
                json.dumps(payload, separators=(",", ":")),
            ),
        )
        self.connection.commit()

    @staticmethod
    def _normalise_identity(value: str | None) -> str:
        translation = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t"})
        return re.sub(r"[^a-z]", "", (value or "").lower().translate(translation))

    @staticmethod
    def blocked_identity_theme(candidate: Candidate) -> str | None:
        """Block recurring impersonation themes the operator has rejected."""
        name = re.sub(r"[^a-z0-9]+", " ", (candidate.name or "").lower()).strip()
        symbol = re.sub(r"[^a-z0-9]+", "", (candidate.symbol or "").lower())
        compact = (name + symbol).replace(" ", "")
        if "spacex" in compact or re.search(r"\bspace\s+x\b", name):
            return "blocked SpaceX impersonation theme"
        if (
            re.search(r"\b(?:usd|usdc|usdt|stable\s*coin|stablecoin|us\s*dollar)\b", name)
            or symbol in {"usd", "usdc", "usdt", "busd", "usde", "usds"}
            or symbol.startswith("usd")
        ):
            return "blocked USD/stablecoin impersonation theme"
        if (
            re.search(r"\b(?:us\s*oil|crude\s*oil|west\s*texas\s*intermediate)\b", name)
            or symbol in {"oil", "usoil", "wti", "crude"}
        ):
            return "blocked US-oil/commodity impersonation theme"
        return None

    def identity_risk(self, candidate: Candidate, lookback_days: int = 7) -> dict[str, Any]:
        name = self._normalise_identity(candidate.name)
        symbol = self._normalise_identity(candidate.symbol)
        missing_identity = not name and not symbol
        cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
        rows = (
            []
            if missing_identity
            else self.connection.execute(
                """
                SELECT candidate_key, name, symbol FROM candidates
                WHERE first_seen_at >= ? AND candidate_key != ?
                ORDER BY first_seen_at DESC LIMIT 2000
                """,
                (cutoff, candidate.key),
            ).fetchall()
        )
        exact_name = 0
        exact_symbol = 0
        exact_both = 0
        for row in rows:
            other_name = self._normalise_identity(row["name"])
            other_symbol = self._normalise_identity(row["symbol"])
            same_name = bool(name and other_name == name)
            same_symbol = bool(symbol and other_symbol == symbol)
            exact_name += int(same_name)
            exact_symbol += int(same_symbol)
            exact_both += int(same_name and same_symbol)
        blocked_theme = self.blocked_identity_theme(candidate)
        penalty = 4.0 if missing_identity else min(
            24.0,
            exact_both * 12.0
            + max(0, exact_symbol - exact_both) * 4.0
            + max(0, exact_name - exact_both) * 3.0,
        )
        serial_launches = 0
        if candidate.deployer:
            serial_launches = int(
                self.connection.execute(
                    """
                    SELECT COUNT(*) FROM candidates
                    WHERE first_seen_at >= ? AND candidate_key != ? AND deployer = ?
                    """,
                    (cutoff, candidate.key, candidate.deployer),
                ).fetchone()[0]
            )
        if serial_launches >= 3:
            penalty += min(15.0, 3.0 + (serial_launches - 3) * 2.0)
        if blocked_theme:
            penalty += 60.0
        reasons: list[str] = []
        if blocked_theme:
            reasons.append(blocked_theme)
        if missing_identity:
            reasons.append("missing token identity")
        elif exact_both:
            reasons.append(f"{exact_both} recent exact name/ticker duplicate(s)")
        elif exact_symbol or exact_name:
            reasons.append(f"recent identity overlap ({exact_symbol} ticker, {exact_name} name)")
        else:
            reasons.append("no recent exact identity duplicates")
        if serial_launches >= 3:
            reasons.append(f"deployer launched {serial_launches} other recent tokens")
        return {
            "copycat_penalty": round(penalty, 1),
            "reason": "; ".join(reasons),
            "matches": max(exact_name, exact_symbol),
            "exact_both": exact_both,
            "serial_deployer_launches": serial_launches,
            "blocked_theme": blocked_theme,
        }

    def deployer_reputation(self, candidate: Candidate) -> dict[str, Any]:
        if not candidate.deployer:
            return {"identified": False, "samples": 0, "score": 0.0}
        rows = self.connection.execute(
            """
            SELECT c.candidate_key, o.return_pct, o.mfe_pct
            FROM candidates c
            JOIN alerts a ON a.candidate_key = c.candidate_key
            JOIN alert_outcomes o ON o.alert_id = a.id AND o.horizon_minutes = 1440
            WHERE c.chain = ? AND c.deployer = ?
              AND c.candidate_key != ?
              AND o.return_pct IS NOT NULL
              AND COALESCE(o.capture_lag_minutes, 0) <= 120
            ORDER BY o.captured_at DESC
            LIMIT 100
            """,
            (candidate.chain, candidate.deployer, candidate.key),
        ).fetchall()
        distinct: dict[str, sqlite3.Row] = {}
        for row in rows:
            distinct.setdefault(str(row["candidate_key"]), row)
        values = list(distinct.values())
        samples = len(values)
        if not values:
            return {"identified": True, "samples": 0, "score": 0.0}
        returns = [float(row["return_pct"]) for row in values]
        win_rate = sum(value > 0 for value in returns) / samples
        average_return = sum(returns) / samples
        score = 0.0
        if samples >= 3:
            score = max(0.0, min(1.0, win_rate * 0.65 + max(-0.25, min(0.35, average_return / 200))))
        return {
            "identified": True,
            "samples": samples,
            "win_rate": round(win_rate, 3),
            "average_return_pct": round(average_return, 1),
            "score": round(score, 3),
        }

    def first_detected_market_cap(self, candidate_key: str) -> float | None:
        row = self.connection.execute(
            "SELECT first_detected_market_cap_usd FROM candidates WHERE candidate_key = ?",
            (candidate_key,),
        ).fetchone()
        return float(row["first_detected_market_cap_usd"]) if row and row[0] is not None else None

    def update_score(self, candidate_key: str, result: ScoreResult) -> None:
        self.connection.execute(
            """
            UPDATE candidates SET
                last_score = ?, last_stage = ?, last_signal = ?, last_result_json = ?
            WHERE candidate_key = ?
            """,
            (
                result.score,
                result.stage,
                result.signal,
                json.dumps(result.to_dict(), separators=(",", ":")),
                candidate_key,
            ),
        )
        self.connection.commit()

    def near_misses(self, limit: int = 3, minimum_score: float = 40.0) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT candidate_key, chain, name, symbol, source, last_score,
                   last_signal, last_result_json
            FROM candidates
            WHERE last_score >= ? AND COALESCE(last_signal, 'MONITOR') = 'MONITOR'
            ORDER BY last_score DESC, last_seen_at DESC
            LIMIT ?
            """,
            (minimum_score, limit),
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            payload = json.loads(row["last_result_json"] or "{}")
            result.append(
                {
                    "candidate_key": row["candidate_key"],
                    "chain": row["chain"],
                    "name": row["name"],
                    "symbol": row["symbol"],
                    "source": row["source"],
                    "score": float(row["last_score"]),
                    "blockers": list(payload.get("blockers") or [])[:3],
                }
            )
        return result

    def alert_allowed(
        self,
        candidate_key: str,
        result: ScoreResult,
        cooldown_minutes: int,
        score_upgrade: float,
        token_realert_hours: int = 24,
    ) -> bool:
        row = self.connection.execute(
            "SELECT sent_at, stage, score FROM alerts WHERE candidate_key = ? ORDER BY sent_at DESC LIMIT 1",
            (candidate_key,),
        ).fetchone()
        if not row:
            return True
        sent_at = _dt(row["sent_at"]) or datetime.now(timezone.utc)
        elapsed = datetime.now(timezone.utc) - sent_at
        if elapsed < timedelta(hours=max(1, token_realert_hours)):
            return False
        cooling_down = elapsed < timedelta(minutes=cooldown_minutes)
        meaningful_upgrade = result.score >= float(row["score"]) + score_upgrade
        new_reawakening = result.stage == "REAWAKENING" and result.stage != row["stage"]
        # A token gets one alert per 24h. A later repeat must represent a true
        # reawakening (or a materially stronger reawakening), never a routine
        # rescan or a second discovery source.
        return not cooling_down and (
            new_reawakening or (result.stage == "REAWAKENING" and meaningful_upgrade)
        )

    def record_alert(
        self,
        candidate_key: str,
        result: ScoreResult,
        snapshot: MarketSnapshot | None = None,
    ) -> int:
        market_cap = (snapshot.market_cap_usd or snapshot.fdv_usd) if snapshot else None
        first_detected = self.connection.execute(
            "SELECT first_detected_market_cap_usd FROM candidates WHERE candidate_key = ?",
            (candidate_key,),
        ).fetchone()
        first_detected_market_cap = (
            first_detected["first_detected_market_cap_usd"] if first_detected else None
        )
        self.connection.execute(
            """
            INSERT INTO alerts(
                candidate_key, sent_at, stage, signal, score,
                entry_price_usd, entry_market_cap_usd, entry_liquidity_usd,
                first_detected_market_cap_usd, alert_market_cap_usd,
                current_market_cap_usd, peak_after_alert_market_cap_usd,
                mfe_pct, mae_pct, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate_key,
                datetime.now(timezone.utc).isoformat(),
                result.stage,
                result.signal,
                result.score,
                snapshot.price_usd if snapshot else None,
                market_cap,
                snapshot.liquidity_usd if snapshot else None,
                first_detected_market_cap,
                market_cap,
                market_cap,
                market_cap,
                0.0 if snapshot and snapshot.price_usd else None,
                0.0 if snapshot and snapshot.price_usd else None,
                json.dumps(result.to_dict(), separators=(",", ":")),
            ),
        )
        alert_id = int(self.connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        self.connection.commit()
        return alert_id

    def update_alert_outcomes(
        self,
        candidate_key: str,
        snapshot: MarketSnapshot,
        *,
        terminal_loss: bool = False,
    ) -> int:
        if snapshot.price_usd is None or snapshot.price_usd < 0:
            return 0
        if snapshot.price_usd == 0 and not terminal_loss:
            return 0
        cutoff = (snapshot.captured_at - timedelta(hours=48)).isoformat()
        rows = self.connection.execute(
            """
            SELECT id, sent_at, entry_price_usd, mfe_pct, mae_pct,
                   peak_after_alert_market_cap_usd
            FROM alerts
            WHERE candidate_key = ? AND sent_at >= ? AND entry_price_usd > 0
            """,
            (candidate_key, cutoff),
        ).fetchall()
        inserted = 0
        market_cap = snapshot.market_cap_usd or snapshot.fdv_usd
        horizons = (15, 60, 360, 1440)
        for row in rows:
            sent_at = _dt(row["sent_at"])
            if not sent_at:
                continue
            return_pct = ((snapshot.price_usd / float(row["entry_price_usd"])) - 1.0) * 100.0
            mfe = max(float(row["mfe_pct"] or 0.0), return_pct)
            mae = min(float(row["mae_pct"] or 0.0), return_pct)
            self.connection.execute(
                """
                UPDATE alerts SET
                    mfe_pct = ?,
                    mae_pct = ?,
                    current_market_cap_usd = COALESCE(?, current_market_cap_usd),
                    peak_after_alert_market_cap_usd = CASE
                        WHEN ? IS NULL THEN peak_after_alert_market_cap_usd
                        WHEN peak_after_alert_market_cap_usd IS NULL THEN ?
                        ELSE MAX(peak_after_alert_market_cap_usd, ?)
                    END
                WHERE id = ?
                """,
                (mfe, mae, market_cap, market_cap, market_cap, market_cap, row["id"]),
            )
            elapsed_minutes = (snapshot.captured_at - sent_at).total_seconds() / 60.0
            for horizon in horizons:
                if elapsed_minutes < horizon:
                    continue
                cursor = self.connection.execute(
                    """
                    INSERT OR IGNORE INTO alert_outcomes(
                        alert_id, horizon_minutes, due_at, captured_at,
                        price_usd, market_cap_usd, liquidity_usd, return_pct,
                        capture_lag_minutes, mfe_pct, mae_pct
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["id"],
                        horizon,
                        (sent_at + timedelta(minutes=horizon)).isoformat(),
                        snapshot.captured_at.isoformat(),
                        snapshot.price_usd,
                        market_cap,
                        snapshot.liquidity_usd,
                        return_pct,
                        max(0.0, elapsed_minutes - horizon),
                        mfe,
                        mae,
                    ),
                )
                was_inserted = cursor.rowcount > 0
                inserted += int(was_inserted)
                if was_inserted and horizon == 1440:
                    self._update_wallet_reputation(int(row["id"]), return_pct)
        self.connection.commit()
        return inserted

    def reset_market_failures(self, candidate_key: str) -> None:
        self.connection.execute(
            "DELETE FROM candidate_market_failures WHERE candidate_key = ?",
            (candidate_key,),
        )
        self.connection.commit()

    def record_missing_market(
        self,
        candidate_key: str,
        captured_at: datetime,
        confirmations: int = 3,
    ) -> int:
        """Classify a repeatedly missing alerted market as a terminal -100% loss.

        Only successful empty market lookups call this method; transport/API
        failures do not increment the confirmation count.
        """
        self.connection.execute(
            """
            INSERT INTO candidate_market_failures(candidate_key, consecutive, last_checked_at)
            VALUES (?, 1, ?)
            ON CONFLICT(candidate_key) DO UPDATE SET
                consecutive = candidate_market_failures.consecutive + 1,
                last_checked_at = excluded.last_checked_at
            """,
            (candidate_key, captured_at.isoformat()),
        )
        row = self.connection.execute(
            "SELECT consecutive FROM candidate_market_failures WHERE candidate_key = ?",
            (candidate_key,),
        ).fetchone()
        self.connection.commit()
        if not row or int(row["consecutive"]) < max(2, confirmations):
            return 0
        chain, token_address = candidate_key.split(":", 1)
        return self.update_alert_outcomes(
            candidate_key,
            MarketSnapshot(
                chain=chain,
                token_address=token_address,
                captured_at=captured_at,
                price_usd=0.0,
                liquidity_usd=0.0,
                source="confirmed-market-disappearance",
            ),
            terminal_loss=True,
        )

    def record_alert_wallets(self, alert_id: int, chain: str, wallets: set[str]) -> int:
        now = datetime.now(timezone.utc).isoformat()
        inserted = 0
        for wallet in wallets:
            normalised = wallet.strip().lower()
            if not normalised.startswith("0x") or len(normalised) != 42:
                continue
            cursor = self.connection.execute(
                """
                INSERT OR IGNORE INTO alert_wallets(alert_id, chain, wallet, observed_at)
                VALUES (?, ?, ?, ?)
                """,
                (alert_id, chain.lower(), normalised, now),
            )
            inserted += int(cursor.rowcount > 0)
        self.connection.commit()
        return inserted

    def _update_wallet_reputation(self, alert_id: int, return_pct: float) -> None:
        rows = self.connection.execute(
            """
            SELECT aw.chain, aw.wallet, a.candidate_key
            FROM alert_wallets aw JOIN alerts a ON a.id = aw.alert_id
            WHERE aw.alert_id = ?
            """,
            (alert_id,),
        ).fetchall()
        now = datetime.now(timezone.utc).isoformat()
        # Small tolerance avoids classifying a mathematically exact +20% as a
        # loss because binary floating point may represent it as 19.9999999.
        win = int(return_pct >= 19.999)
        for row in rows:
            unique = self.connection.execute(
                """
                INSERT OR IGNORE INTO wallet_outcomes(
                    chain, wallet, candidate_key, evaluated_at, return_pct, won
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    row["chain"],
                    row["wallet"],
                    row["candidate_key"],
                    now,
                    return_pct,
                    win,
                ),
            )
            if unique.rowcount <= 0:
                continue
            self.connection.execute(
                """
                INSERT INTO wallet_reputation(
                    chain, wallet, evaluated_alerts, wins, total_return_pct,
                    last_return_pct, updated_at
                ) VALUES (?, ?, 1, ?, ?, ?, ?)
                ON CONFLICT(chain, wallet) DO UPDATE SET
                    evaluated_alerts = wallet_reputation.evaluated_alerts + 1,
                    wins = wallet_reputation.wins + excluded.wins,
                    total_return_pct = wallet_reputation.total_return_pct + excluded.total_return_pct,
                    last_return_pct = excluded.last_return_pct,
                    updated_at = excluded.updated_at
                """,
                (row["chain"], row["wallet"], win, return_pct, return_pct, now),
            )

    def curated_smart_wallets(
        self,
        min_observations: int = 3,
        min_win_rate: float = 0.60,
        min_average_return: float = 10.0,
        chain: str | None = None,
    ) -> set[str]:
        chain_filter = "AND chain = ?" if chain else ""
        parameters: list[Any] = [min_observations, min_win_rate, min_average_return]
        if chain:
            parameters.append(chain.lower())
        rows = self.connection.execute(
            f"""
            SELECT wallet FROM wallet_reputation
            WHERE evaluated_alerts >= ?
              AND CAST(wins AS REAL) / evaluated_alerts >= ?
              AND total_return_pct / evaluated_alerts >= ?
              {chain_filter}
            """,
            parameters,
        ).fetchall()
        return {str(row["wallet"]).lower() for row in rows}

    def smart_wallet_report(self) -> dict[str, int]:
        return {
            "observed": int(
                self.connection.execute("SELECT COUNT(DISTINCT chain || ':' || wallet) FROM alert_wallets").fetchone()[0]
            ),
            "evaluated": int(
                self.connection.execute("SELECT COUNT(*) FROM wallet_reputation").fetchone()[0]
            ),
        }

    def apply_target_estimate(
        self,
        candidate: Candidate,
        snapshot: MarketSnapshot,
        result: ScoreResult,
    ) -> None:
        """Attach a live, history-aware upside estimate to an eligible result."""
        structural = structural_target(candidate, snapshot, result)
        selected: list[sqlite3.Row] = []
        selected_horizon = 0
        for horizon in (1440, 360):
            rows = self.connection.execute(
                """
                SELECT a.candidate_key, a.signal, a.stage, a.score, o.mfe_pct
                FROM alert_outcomes o
                JOIN alerts a ON a.id = o.alert_id
                JOIN candidates c ON c.candidate_key = a.candidate_key
                WHERE c.chain = ?
                  AND o.horizon_minutes = ?
                  AND o.mfe_pct IS NOT NULL
                  AND COALESCE(o.capture_lag_minutes, 0) <= CASE ?
                        WHEN 1440 THEN 120 ELSE 30
                      END
                ORDER BY o.captured_at DESC
                LIMIT 500
                """,
                (candidate.chain, horizon, horizon),
            ).fetchall()
            unique: list[sqlite3.Row] = []
            seen_candidates: set[str] = set()
            for row in rows:
                key = str(row["candidate_key"])
                if key in seen_candidates:
                    continue
                seen_candidates.add(key)
                unique.append(row)
            tiers = (
                [
                    row
                    for row in unique
                    if row["signal"] == result.signal
                    and row["stage"] == result.stage
                    and abs(float(row["score"]) - result.score) <= 8
                ],
                [
                    row
                    for row in unique
                    if row["signal"] == result.signal
                    and abs(float(row["score"]) - result.score) <= 10
                ],
                [row for row in unique if row["signal"] == result.signal],
            )
            selected = next((tier for tier in tiers if len(tier) >= 5), [])
            if selected:
                selected_horizon = horizon
                break

        historical_mfe = [float(row["mfe_pct"] or 0.0) for row in selected]
        target, confidence = combine_target(structural, historical_mfe)
        result.target_multiple = target
        result.target_confidence = confidence
        if selected:
            label = "24h" if selected_horizon == 1440 else "6h"
            result.target_basis = (
                f"{len(selected)} comparable {label} outcomes + live liquidity/flow structure"
            )
        else:
            result.target_basis = "live liquidity/flow structure; comparable history building"

    def outcome_report(self) -> dict[str, Any]:
        outcome_counts = {
            int(row["horizon_minutes"]): int(row["count"])
            for row in self.connection.execute(
                """
                SELECT horizon_minutes, COUNT(*) AS count
                FROM alert_outcomes
                WHERE COALESCE(capture_lag_minutes, 0) <= CASE horizon_minutes
                    WHEN 1440 THEN 120
                    WHEN 360 THEN 30
                    ELSE 15
                END
                GROUP BY horizon_minutes
                """
            ).fetchall()
        }
        rows = self.connection.execute(
            """
            SELECT a.signal, o.horizon_minutes, o.return_pct, o.mfe_pct, o.mae_pct
            FROM alert_outcomes o JOIN alerts a ON a.id = o.alert_id
            WHERE o.return_pct IS NOT NULL
              AND COALESCE(o.capture_lag_minutes, 0) <= CASE o.horizon_minutes
                    WHEN 1440 THEN 120
                    WHEN 360 THEN 30
                    ELSE 15
                  END
            ORDER BY o.captured_at DESC LIMIT 1000
            """
        ).fetchall()
        grouped: dict[str, dict[int, list[sqlite3.Row]]] = {}
        for row in rows:
            grouped.setdefault(row["signal"], {}).setdefault(int(row["horizon_minutes"]), []).append(row)
        summaries: dict[str, dict[int, dict[str, float]]] = {}
        for signal, horizons in grouped.items():
            summaries[signal] = {}
            for horizon, values in horizons.items():
                returns = [float(value["return_pct"]) for value in values]
                mfes = [float(value["mfe_pct"] or 0.0) for value in values]
                maes = [float(value["mae_pct"] or 0.0) for value in values]
                summaries[signal][horizon] = {
                    "samples": len(values),
                    "win_rate": round(sum(item > 0 for item in returns) / len(returns) * 100.0, 1),
                    "median_return": round(median(returns), 1),
                    "median_mfe": round(median(mfes), 1),
                    "median_mae": round(median(maes), 1),
                }
        tracked = int(self.connection.execute("SELECT COUNT(*) FROM alerts WHERE entry_price_usd IS NOT NULL").fetchone()[0])
        cap_rows = self.connection.execute(
            """
            SELECT a.candidate_key, c.symbol, a.sent_at,
                   a.first_detected_market_cap_usd, a.alert_market_cap_usd,
                   a.current_market_cap_usd, a.peak_after_alert_market_cap_usd
            FROM alerts a
            JOIN candidates c ON c.candidate_key = a.candidate_key
            ORDER BY a.sent_at DESC LIMIT 10
            """
        ).fetchall()
        market_cap_audit = [dict(row) for row in cap_rows]
        return {
            "tracked_alerts": tracked,
            "outcome_counts": outcome_counts,
            "signals": summaries,
            "market_cap_audit": market_cap_audit,
        }

    def calibrated_thresholds(self, config: Any) -> dict[str, Any]:
        defaults = {
            "scout": float(config.scout_alert_score),
            "watch": float(config.min_alert_score),
            "buy": float(config.strong_alert_score),
            "samples": 0,
            "calibrated": False,
        }
        if not getattr(config, "auto_calibrate", False):
            return defaults
        rows = self.connection.execute(
            """
            SELECT a.score, o.mfe_pct, o.mae_pct, o.return_pct
            FROM alert_outcomes o JOIN alerts a ON a.id = o.alert_id
            WHERE o.horizon_minutes = 1440
              AND o.return_pct IS NOT NULL
              AND COALESCE(o.capture_lag_minutes, 0) <= 120
            ORDER BY o.captured_at DESC LIMIT 200
            """
        ).fetchall()
        sample_count = len(rows)
        defaults["samples"] = sample_count
        minimum = int(getattr(config, "calibration_min_samples", 30))
        defaults["calibrated"] = sample_count >= minimum
        return defaults

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
            "outcomes_24h": self.connection.execute(
                "SELECT COUNT(*) FROM alert_outcomes WHERE captured_at >= ?", (cutoff,)
            ).fetchone()[0],
        }

    def prune(self, snapshot_days: int = 7, alert_days: int = 90) -> None:
        snapshot_cutoff = (datetime.now(timezone.utc) - timedelta(days=snapshot_days)).isoformat()
        alert_cutoff = (datetime.now(timezone.utc) - timedelta(days=alert_days)).isoformat()
        self.connection.execute("DELETE FROM snapshots WHERE captured_at < ?", (snapshot_cutoff,))
        self.connection.execute(
            "DELETE FROM alert_outcomes WHERE alert_id IN (SELECT id FROM alerts WHERE sent_at < ?)",
            (alert_cutoff,),
        )
        self.connection.execute(
            "DELETE FROM alert_wallets WHERE alert_id IN (SELECT id FROM alerts WHERE sent_at < ?)",
            (alert_cutoff,),
        )
        self.connection.execute("DELETE FROM alerts WHERE sent_at < ?", (alert_cutoff,))
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()
