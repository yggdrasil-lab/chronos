"""Application service for concurrent peer passive habit signal evaluation."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
import logging
from zoneinfo import ZoneInfo

import httpx

from app.domain.passive_signals import (
    EirDaySnapshot,
    KratosDaySnapshot,
    PassiveSignalsReport,
    PeerFetchStatus,
    PlutusCheckinSnapshot,
    evaluate_passive_signals,
)
from app.services.peer_gateways import EirGateway, KratosGateway, PlutusGateway

logger = logging.getLogger(__name__)


class PassiveSignalService:
    """Concurrently queries Eir, Kratos, and Plutus gateways and evaluates passive cues."""

    def __init__(
        self,
        *,
        eir_gateway: EirGateway,
        kratos_gateway: KratosGateway,
        plutus_gateway: PlutusGateway,
        timezone_name: str = "Pacific/Auckland",
    ) -> None:
        self._eir_gateway = eir_gateway
        self._kratos_gateway = kratos_gateway
        self._plutus_gateway = plutus_gateway
        self._tz = ZoneInfo(timezone_name)

    def _safe_fetch_eir(self, target_date: date) -> EirDaySnapshot:
        try:
            return self._eir_gateway.fetch_day_snapshot(target_date)
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            logger.warning("Eir gateway exception: %s", exc)
            return EirDaySnapshot(status=PeerFetchStatus(available=False, error=str(exc)))

    def _safe_fetch_kratos(self, target_date: date) -> KratosDaySnapshot:
        try:
            return self._kratos_gateway.fetch_day_snapshot(target_date)
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            logger.warning("Kratos gateway exception: %s", exc)
            return KratosDaySnapshot(status=PeerFetchStatus(available=False, error=str(exc)))

    def _safe_fetch_plutus(self, target_date: date) -> PlutusCheckinSnapshot:
        try:
            return self._plutus_gateway.fetch_checkin_snapshot(target_date)
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            logger.warning("Plutus gateway exception: %s", exc)
            return PlutusCheckinSnapshot(status=PeerFetchStatus(available=False, error=str(exc)))

    def get_passive_signals(self, target_date: date | None = None) -> PassiveSignalsReport:
        """Fetch peer snapshots concurrently and evaluate all canonical passive signal rules."""
        resolved_date = target_date or datetime.now(self._tz).date()
        with ThreadPoolExecutor(max_workers=3) as pool:
            eir_future = pool.submit(self._safe_fetch_eir, resolved_date)
            kratos_future = pool.submit(self._safe_fetch_kratos, resolved_date)
            plutus_future = pool.submit(self._safe_fetch_plutus, resolved_date)
            eir_snapshot = eir_future.result()
            kratos_snapshot = kratos_future.result()
            plutus_snapshot = plutus_future.result()
        return evaluate_passive_signals(
            target_date=resolved_date,
            eir=eir_snapshot,
            kratos=kratos_snapshot,
            plutus=plutus_snapshot,
            tz=self._tz,
        )
