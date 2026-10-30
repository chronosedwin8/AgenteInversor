"""Descarga de velas históricas desde data.binance.vision (archivos mensuales con SHA256)."""

from __future__ import annotations

import csv
import hashlib
import io
import zipfile
from dataclasses import dataclass
from pathlib import Path

import aiohttp

from bot.data.klines import Candle
from bot.log import get_logger

log = get_logger(__name__)


class VisionChecksumError(RuntimeError):
    pass


@dataclass(frozen=True)
class Month:
    year: int
    month: int

    def __str__(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"

    def prev(self) -> Month:
        return Month(self.year - 1, 12) if self.month == 1 else Month(self.year, self.month - 1)

    def next(self) -> Month:
        return Month(self.year + 1, 1) if self.month == 12 else Month(self.year, self.month + 1)


def last_complete_months(current: Month, count: int) -> list[Month]:
    """Los `count` meses completos anteriores a `current`, en orden cronológico."""
    months: list[Month] = []
    m = current
    for _ in range(count):
        m = m.prev()
        months.append(m)
    return list(reversed(months))


def parse_kline_csv(data: bytes, symbol: str, interval: str) -> list[Candle]:
    """CSV de klines (sin cabecera en spot; se tolera cabecera por si cambia el formato)."""
    rows = csv.reader(io.StringIO(data.decode("utf-8")))
    candles: list[Candle] = []
    for row in rows:
        if not row or not row[0].strip().isdigit():
            continue
        candles.append(Candle.from_row(symbol, interval, row))
    return candles


class VisionClient:
    def __init__(self, base_url: str, cache_dir: Path, session: aiohttp.ClientSession):
        self._base = base_url.rstrip("/")
        self._cache = cache_dir
        self._session = session

    def _url(self, symbol: str, interval: str, month: Month) -> str:
        name = f"{symbol}-{interval}-{month}.zip"
        return f"{self._base}/data/spot/monthly/klines/{symbol}/{interval}/{name}"

    async def _get(self, url: str) -> bytes | None:
        async with self._session.get(url, timeout=aiohttp.ClientTimeout(total=120)) as resp:
            if resp.status == 404:
                return None
            resp.raise_for_status()
            return await resp.read()

    async def monthly(self, symbol: str, interval: str, month: Month) -> list[Candle] | None:
        """Velas de un mes completo, o None si el archivo aún no está publicado."""
        url = self._url(symbol, interval, month)
        path = self._cache / symbol / interval / url.rsplit("/", 1)[1]
        if path.exists():
            blob = path.read_bytes()
        else:
            got = await self._get(url)
            if got is None:
                log.info("vision_not_available", symbol=symbol, interval=interval, month=str(month))
                return None
            checksum = await self._get(url + ".CHECKSUM")
            if checksum is None:
                raise VisionChecksumError(f"sin CHECKSUM para {url}")
            expected = checksum.decode().split()[0].lower()
            actual = hashlib.sha256(got).hexdigest()
            if actual != expected:
                raise VisionChecksumError(f"SHA256 no coincide para {url}")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(got)
            blob = got
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            (name,) = [n for n in zf.namelist() if n.endswith(".csv")]
            return parse_kline_csv(zf.read(name), symbol, interval)
