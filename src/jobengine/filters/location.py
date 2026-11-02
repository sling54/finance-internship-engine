"""Feature 4, part 2: the location rule.

Summer (and unknown-season) roles: anywhere in the US, or remote.
Fall, winter, spring and part-time roles, and every Data / SWE role: within the home-base
drive zone, or remote.

A location is checked against the city lists in sources.yml first (offline, instant).
Only Florida places missing from those lists go to the optional OpenRouteService map check.
"""

from __future__ import annotations

import re

from ..models import Posting, Rejected
from ..textutil import normalize


def _names(block: str) -> set[str]:
    return {n.strip() for n in block.split("|") if n.strip()}


US_STATES = dict(
    pair.split(":")
    for pair in (
        "AL:alabama|AK:alaska|AZ:arizona|AR:arkansas|CA:california|CO:colorado|CT:connecticut|"
        "DE:delaware|DC:district of columbia|FL:florida|GA:georgia|HI:hawaii|ID:idaho|"
        "IL:illinois|IN:indiana|IA:iowa|KS:kansas|KY:kentucky|LA:louisiana|ME:maine|"
        "MD:maryland|MA:massachusetts|MI:michigan|MN:minnesota|MS:mississippi|MO:missouri|"
        "MT:montana|NE:nebraska|NV:nevada|NH:new hampshire|NJ:new jersey|NM:new mexico|"
        "NY:new york|NC:north carolina|ND:north dakota|OH:ohio|OK:oklahoma|OR:oregon|"
        "PA:pennsylvania|RI:rhode island|SC:south carolina|SD:south dakota|TN:tennessee|"
        "TX:texas|UT:utah|VT:vermont|VA:virginia|WA:washington|WV:west virginia|"
        "WI:wisconsin|WY:wyoming"
    ).split("|")
)

# Finance and tech hubs that sources often list without a state.
US_CITIES = _names(
    "new york|nyc|new york city|manhattan|chicago|boston|greenwich|stamford|san francisco|sf|"
    "south sf|bay area|silicon valley|austin|philadelphia|bala cynwyd|houston|dallas|seattle|"
    "los angeles|la|denver|princeton|jersey city|short hills|red bank|westport|purchase|"
    "charlotte|atlanta|washington|miami|palo alto|menlo park|san mateo|minneapolis|st louis|"
    "columbus|pittsburgh|baltimore|owings mills|colorado springs|salt lake city|phoenix|"
    "nashville|irvine|newport beach|west palm beach|boca raton|fort lauderdale|hoboken|"
    "san diego|san jose|raleigh|durham|orlando|tampa|jacksonville|radnor|wayzata|redmond|"
    "burlingame|cambridge|new haven|hartford|richmond|glen allen|des moines|overland park"
)
US_COUNTRY = _names("united states|usa|us|u s|united states of america")

NON_US = _names(
    "london|hong kong|singapore|amsterdam|sydney|paris|tokyo|shanghai|beijing|mumbai|bangalore|"
    "bengaluru|gurgaon|gurugram|hyderabad|pune|chennai|noida|delhi|new delhi|kolkata|"
    "ahmedabad|dublin|zug|zurich|geneva|toronto|montreal|vancouver|calgary|ottawa|waterloo|"
    "ontario|quebec|british columbia|alberta|mississauga|tel aviv|dubai|abu dhabi|frankfurt|"
    "milan|madrid|barcelona|lisbon|taipei|seoul|munich|luxembourg|stockholm|copenhagen|oslo|"
    "warsaw|krakow|wroclaw|budapest|prague|vienna|cyprus|limassol|kyiv|brussels|oxford|"
    "edinburgh|manchester|glasgow|aarhus|athens|bucharest|sofia|tallinn|riga|vilnius|"
    "kuala lumpur|jakarta|manila|bangkok|ho chi minh|auckland|melbourne|brisbane|perth|"
    "cape town|johannesburg|lagos|nairobi|cairo|riyadh|doha|istanbul|sao paulo|mexico city|"
    "monterrey|guadalajara|bogota|buenos aires|lima|united kingdom|uk|england|scotland|"
    "canada|india|china|australia|germany|france|netherlands|switzerland|ireland|japan|"
    "israel|uae|united arab emirates|belgium|poland|denmark|hungary|spain|italy|sweden|"
    "norway|austria|portugal|greece|romania|bulgaria|malaysia|vietnam|korea|south korea|"
    "taiwan|new zealand|qatar|bahrain|kuwait|turkey|brazil|argentina|colombia|mexico|"
    "emea|apac|europe|hanoi|aalborg|gothenburg|helsinki|finland|reykjavik|beirut|karachi|"
    "lahore|dhaka|colombo|kathmandu"
)
_US_NAMES = set(US_STATES.values()) | US_CITIES | US_COUNTRY
_STATE_CODE = re.compile(r"(?:,|\s)\s*([A-Z]{2})\b")
_REMOTE = re.compile(r"\bremote\b", re.IGNORECASE)
_LOCAL_SEASONS = ("Fall", "Spring", "Winter", "Part-time")


def _contains(text: str, names: set[str]) -> bool:
    padded = f" {text} "
    return any(f" {n} " in padded for n in names)


def place_kind(place: str, inside: set[str], borderline: set[str]) -> str:
    """Classify one location string: local | borderline | us | non_us | unknown."""
    norm = normalize(place)
    if not norm:
        return "unknown"
    if _contains(norm, inside):
        return "local"
    if _contains(norm, borderline):
        return "borderline"
    codes = {c for c in _STATE_CODE.findall(place) if c in US_STATES}
    if codes or _contains(norm, _US_NAMES):
        return "us"  # a string naming both a US and a non-US city still has a US office
    if _contains(norm, NON_US):
        return "non_us"
    return "unknown"


def is_florida(place: str) -> bool:
    return bool(re.search(r"(?:,|\s)FL\b", place)) or "florida" in place.lower()


class LocationFilter:
    def __init__(self, sources_cfg: dict, location_rule, zone=None):
        local = sources_cfg.get("local_area", {})
        self.inside = {normalize(c) for c in local.get("inside", [])}
        self.borderline = {normalize(c) for c in local.get("borderline", [])}
        self.location_rule = location_rule  # job -> "season" | "miami"
        self.zone = zone  # optional DriveZone for unlisted Florida places

    def __call__(self, job: Posting) -> None:
        places = [p for p in job.locations if p and p.strip()]
        remote = bool(job.remote)
        kinds = []
        for place in places:
            kind = self._kind(_REMOTE.sub(" ", place))
            if _REMOTE.search(place) and kind in ("us", "unknown"):
                remote = True  # "Remote", "Remote in USA"
            elif _REMOTE.search(place) and kind == "non_us":
                kinds.append(kind)  # "Remote in UK" is still abroad
            elif not _REMOTE.search(place):
                kinds.append(kind)
        miami_track = self.location_rule(job) == "miami"

        if miami_track or job.season in _LOCAL_SEASONS:
            self._local_rule(job, places, kinds, remote, miami_track)
        else:
            self._us_rule(job, places, kinds, remote)

    def _local_rule(self, job, places, kinds, remote, miami_track) -> None:
        if "local" in kinds:
            return
        if "borderline" in kinds:
            job.flags.append("check drive time")
            return
        if remote and "non_us" not in kinds:
            job.flags.append("remote")
            return
        if not kinds or all(k == "unknown" for k in kinds):
            job.flags.append("check location")
            return
        why = "Miami-area track" if miami_track else f"{job.season} role"
        raise Rejected(f"outside 1-hour zone ({why}): {'; '.join(places)}")

    def _us_rule(self, job, places, kinds, remote) -> None:
        if any(k in ("local", "borderline", "us") for k in kinds):
            return
        if remote and "non_us" not in kinds:
            job.flags.append("remote")
            return
        if "non_us" in kinds:
            raise Rejected(f"outside the US: {'; '.join(places)}")
        job.flags.append("check location")

    def _kind(self, place: str) -> str:
        kind = place_kind(place, self.inside, self.borderline)
        if kind in ("us", "unknown") and self.zone is not None and is_florida(place):
            inside = self.zone.contains_place(place)
            if inside is True:
                return "local"
            if inside is False and kind == "unknown":
                return "us"
        return kind
