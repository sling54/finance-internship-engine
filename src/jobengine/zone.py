"""Optional: the real 1-hour driving zone around the home base, from OpenRouteService.

Used only for Florida places not in the city lists. Needs a free key from
openrouteservice.org saved in .env as ORS_API_KEY=... The zone is drawn once and cached
in data/drive_zone.geojson; geocoding results are cached in data/geocode_cache.json.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from shapely.geometry import Point, shape

from .http import session

log = logging.getLogger(__name__)

ISOCHRONE_URL = "https://api.openrouteservice.org/v2/isochrones/driving-car"
GEOCODE_URL = "https://api.openrouteservice.org/geocode/search"


def api_key(root: Path, name: str = "ORS_API_KEY") -> str | None:
    """A key from the environment, or else from a NAME=... line in .env."""
    key = os.environ.get(name)
    env = root / ".env"
    if not key and env.exists():
        for line in env.read_text().splitlines():
            if line.startswith(f"{name}="):
                key = line.split("=", 1)[1].strip()
    return key or None


def build_zone(key: str, lon: float, lat: float, minutes: int, out: Path) -> None:
    resp = session().post(
        ISOCHRONE_URL,
        json={"locations": [[lon, lat]], "range": [minutes * 60]},
        headers={"Authorization": key},
        timeout=30,
    )
    resp.raise_for_status()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(resp.json()))
    log.info("drive zone saved to %s", out)


class DriveZone:
    def __init__(self, geojson: dict, key: str | None = None, cache_path: Path | None = None):
        self.polygon = shape(geojson["features"][0]["geometry"])
        self.key = key
        self.cache_path = cache_path
        self.cache: dict[str, list[float] | None] = {}
        if cache_path and cache_path.exists():
            self.cache = json.loads(cache_path.read_text())

    @classmethod
    def load(cls, root: Path) -> DriveZone | None:
        path = root / "data" / "drive_zone.geojson"
        if not path.exists():
            return None
        return cls(
            json.loads(path.read_text()), api_key(root), root / "data" / "geocode_cache.json"
        )

    def contains_point(self, lon: float, lat: float) -> bool:
        return self.polygon.contains(Point(lon, lat))

    def contains_place(self, place: str) -> bool | None:
        """True/False if the place could be located, None if not."""
        coords = self._geocode(place)
        return None if coords is None else self.contains_point(*coords)

    def _geocode(self, place: str) -> list[float] | None:
        if place in self.cache:
            return self.cache[place]
        if not self.key:
            return None
        try:
            resp = session().get(
                GEOCODE_URL,
                params={"api_key": self.key, "text": place, "boundary.country": "US", "size": 1},
                timeout=30,
            )
            resp.raise_for_status()
            features = resp.json().get("features", [])
            coords = features[0]["geometry"]["coordinates"] if features else None
        except Exception as exc:
            log.warning("geocode failed for %r: %s", place, exc)
            return None
        self.cache[place] = coords
        if self.cache_path:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(self.cache, indent=1))
        return coords
