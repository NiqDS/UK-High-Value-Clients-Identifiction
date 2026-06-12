import logging
from typing import Dict, List, Optional

import aiohttp

logger = logging.getLogger(__name__)

# ── Countries shown in the team-picker keyboard ───────────────────────────────
# Each entry: (TheSportsDB country name, flag emoji)
FOOTBALL_COUNTRIES: List[tuple] = [
    ("England",      "🏴󠁧󠁢󠁥󠁮󠁧󠁿"),
    ("Spain",        "🇪🇸"),
    ("Germany",      "🇩🇪"),
    ("France",       "🇫🇷"),
    ("Italy",        "🇮🇹"),
    ("Portugal",     "🇵🇹"),
    ("Netherlands",  "🇳🇱"),
    ("Belgium",      "🇧🇪"),
    ("Turkey",       "🇹🇷"),
    ("Russia",       "🇷🇺"),
    ("Ukraine",      "🇺🇦"),
    ("Scotland",     "🏴󠁧󠁢󠁳󠁣󠁴󠁿"),
    ("Greece",       "🇬🇷"),
    ("Poland",       "🇵🇱"),
    ("Austria",      "🇦🇹"),
    ("Brazil",       "🇧🇷"),
    ("Argentina",    "🇦🇷"),
    ("Mexico",       "🇲🇽"),
    ("Colombia",     "🇨🇴"),
    ("Chile",        "🇨🇱"),
    ("USA",          "🇺🇸"),
    ("Japan",        "🇯🇵"),
    ("South Korea",  "🇰🇷"),
    ("Australia",    "🇦🇺"),
    ("Saudi Arabia", "🇸🇦"),
    ("Egypt",        "🇪🇬"),
    ("China",        "🇨🇳"),
]

# ── Flag look-up for arbitrary country strings ────────────────────────────────
COUNTRY_FLAGS: Dict[str, str] = {name: flag for name, flag in FOOTBALL_COUNTRIES}
COUNTRY_FLAGS.update({
    "United States": "🇺🇸",
    "Korea Republic": "🇰🇷",
    "Wales":          "🏴󠁧󠁢󠁷󠁬󠁳󠁿",
    "Ireland":        "🇮🇪",
    "Switzerland":    "🇨🇭",
    "Czech Republic": "🇨🇿",
    "Sweden":         "🇸🇪",
    "Norway":         "🇳🇴",
    "Denmark":        "🇩🇰",
    "Croatia":        "🇭🇷",
    "Serbia":         "🇷🇸",
    "Hungary":        "🇭🇺",
    "Romania":        "🇷🇴",
    "Morocco":        "🇲🇦",
    "Nigeria":        "🇳🇬",
    "South Africa":   "🇿🇦",
    "Uruguay":        "🇺🇾",
    "Peru":           "🇵🇪",
    "Ecuador":        "🇪🇨",
    "Paraguay":       "🇵🇾",
    "Venezuela":      "🇻🇪",
    "Bolivia":        "🇧🇴",
    "Costa Rica":     "🇨🇷",
})

SPORT_EMOJI: Dict[str, str] = {
    "Soccer":     "⚽",
    "Football":   "🏈",
    "Basketball": "🏀",
    "Baseball":   "⚾",
    "Hockey":     "🏒",
    "Tennis":     "🎾",
    "Rugby":      "🏉",
    "Cricket":    "🏏",
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


# ── TheSportsDB helpers ───────────────────────────────────────────────────────

async def get_football_leagues(country: str) -> List[Dict]:
    """Return up to 3 soccer leagues for *country* from TheSportsDB."""
    url = (
        "https://www.thesportsdb.com/api/v1/json/3/search_all_leagues.php"
        f"?c={country}&s=Soccer"
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, headers=_HEADERS, timeout=aiohttp.ClientTimeout(total=8)
            ) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json(content_type=None)
                # TheSportsDB uses "countrys" (sic) as the key for this endpoint
                raw = data.get("countrys") or data.get("leagues") or []
                leagues = [
                    {"id": str(l["idLeague"]), "name": l["strLeague"]}
                    for l in raw
                    if l.get("idLeague") and l.get("strLeague")
                ]
                return leagues[:3]
    except Exception as exc:
        logger.warning("get_football_leagues(%s) failed: %s", country, exc)
        return []


async def get_league_teams(league_id: str) -> List[Dict]:
    """Return all teams in a league, sorted alphabetically by name."""
    url = (
        "https://www.thesportsdb.com/api/v1/json/3/lookup_all_teams.php"
        f"?id={league_id}"
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, headers=_HEADERS, timeout=aiohttp.ClientTimeout(total=8)
            ) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json(content_type=None)
                raw = data.get("teams") or []
                teams = [
                    {
                        "id":      t.get("idTeam", ""),
                        "name":    t.get("strTeam", ""),
                        "country": t.get("strCountry", ""),
                        "badge":   t.get("strTeamBadge", ""),
                    }
                    for t in raw
                    if t.get("strTeam")
                ]
                return sorted(teams, key=lambda t: t["name"])
    except Exception as exc:
        logger.warning("get_league_teams(%s) failed: %s", league_id, exc)
        return []


async def lookup_team(team_name: str) -> Optional[Dict]:
    """Search TheSportsDB for a team by name. Returns best match or None."""
    url = (
        "https://www.thesportsdb.com/api/v1/json/3/searchteams.php"
        f"?t={team_name}"
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, headers=_HEADERS, timeout=aiohttp.ClientTimeout(total=8)
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json(content_type=None)
                teams = data.get("teams")
                if not teams:
                    return None
                t       = teams[0]
                country = t.get("strCountry") or "Unknown"
                sport   = t.get("strSport")   or "Sports"
                return {
                    "name":    t.get("strTeam", team_name),
                    "country": country,
                    "sport":   sport,
                    "flag":    COUNTRY_FLAGS.get(country, "🌍"),
                    "emoji":   SPORT_EMOJI.get(sport, "🏆"),
                    "badge":   t.get("strTeamBadge", ""),
                }
    except Exception as exc:
        logger.warning("lookup_team(%s) failed: %s", team_name, exc)
        return None
