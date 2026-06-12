import logging
from typing import Dict, List, Optional
from urllib.parse import quote_plus

import aiohttp

logger = logging.getLogger(__name__)

# ── Countries shown in the team-picker keyboard ───────────────────────────────
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

# ── Hardcoded leagues (up to 3 per country) ───────────────────────────────────
# sdb = exact name used by TheSportsDB search_all_teams.php?l=
# id  = TheSportsDB league ID used by lookup_all_teams.php?id=
LEAGUES_BY_COUNTRY: Dict[str, List[Dict]] = {
    "England": [
        {"name": "Premier League",     "sdb": "English Premier League",                  "id": "4328"},
        {"name": "Championship",       "sdb": "English Football League Championship",     "id": "4329"},
        {"name": "League One",         "sdb": "English Football League One",              "id": "4330"},
    ],
    "Spain": [
        {"name": "La Liga",            "sdb": "Spanish La Liga",                          "id": "4335"},
        {"name": "La Liga 2",          "sdb": "Spanish La Liga 2",                        "id": "4480"},
    ],
    "Germany": [
        {"name": "Bundesliga",         "sdb": "German Bundesliga",                        "id": "4331"},
        {"name": "2. Bundesliga",      "sdb": "German 2. Bundesliga",                     "id": "4336"},
        {"name": "3. Liga",            "sdb": "German 3. Liga",                           "id": "4481"},
    ],
    "France": [
        {"name": "Ligue 1",            "sdb": "French Ligue 1",                           "id": "4334"},
        {"name": "Ligue 2",            "sdb": "French Ligue 2",                           "id": "4482"},
    ],
    "Italy": [
        {"name": "Serie A",            "sdb": "Italian Serie A",                          "id": "4332"},
        {"name": "Serie B",            "sdb": "Italian Serie B",                          "id": "4483"},
    ],
    "Portugal": [
        {"name": "Primeira Liga",      "sdb": "Portuguese Primeira Liga",                 "id": "4344"},
        {"name": "Liga Portugal 2",    "sdb": "Portuguese Segunda Liga",                  "id": "4484"},
    ],
    "Netherlands": [
        {"name": "Eredivisie",         "sdb": "Dutch Eredivisie",                         "id": "4337"},
        {"name": "Eerste Divisie",     "sdb": "Dutch Eerste Divisie",                     "id": "4485"},
    ],
    "Belgium": [
        {"name": "First Division A",   "sdb": "Belgian First Division A",                 "id": "4397"},
        {"name": "First Division B",   "sdb": "Belgian Pro League",                       "id": "4398"},
    ],
    "Turkey": [
        {"name": "Süper Lig",          "sdb": "Turkish Süper Lig",                        "id": "4339"},
        {"name": "1. Lig",             "sdb": "Turkish 1. Lig",                           "id": "4486"},
    ],
    "Russia": [
        {"name": "Premier League",     "sdb": "Russian Premier League",                   "id": "4340"},
        {"name": "FNL",                "sdb": "Russian Football National League",          "id": "4487"},
    ],
    "Ukraine": [
        {"name": "Premier League",     "sdb": "Ukrainian Premier League",                 "id": "4342"},
    ],
    "Scotland": [
        {"name": "Premiership",        "sdb": "Scottish Premiership",                     "id": "4341"},
        {"name": "Championship",       "sdb": "Scottish Championship",                    "id": "4488"},
    ],
    "Greece": [
        {"name": "Super League",       "sdb": "Greek Super League",                       "id": "4348"},
    ],
    "Poland": [
        {"name": "Ekstraklasa",        "sdb": "Polish Ekstraklasa",                       "id": "4343"},
        {"name": "I liga",             "sdb": "Polish I Liga",                            "id": "4489"},
    ],
    "Austria": [
        {"name": "Bundesliga",         "sdb": "Austrian Football Bundesliga",              "id": "4344"},
    ],
    "Brazil": [
        {"name": "Série A",            "sdb": "Brazilian Série A",                        "id": "4350"},
        {"name": "Série B",            "sdb": "Brazilian Série B",                        "id": "4351"},
        {"name": "Série C",            "sdb": "Brazilian Série C",                        "id": "4490"},
    ],
    "Argentina": [
        {"name": "Primera División",   "sdb": "Argentine Primera División",               "id": "4406"},
        {"name": "Primera Nacional",   "sdb": "Argentine Primera Nacional",               "id": "4491"},
    ],
    "Mexico": [
        {"name": "Liga MX",            "sdb": "Mexican Liga MX",                          "id": "4355"},
        {"name": "Liga de Expansión",  "sdb": "Mexican Liga de Expansión MX",             "id": "4492"},
    ],
    "Colombia": [
        {"name": "Primera A",          "sdb": "Colombian Categoría Primera A",            "id": "4357"},
    ],
    "Chile": [
        {"name": "Primera División",   "sdb": "Chilean Primera División",                 "id": "4358"},
    ],
    "USA": [
        {"name": "MLS",                "sdb": "Major League Soccer",                      "id": "4359"},
        {"name": "USL Championship",   "sdb": "USL Championship",                         "id": "4493"},
    ],
    "Japan": [
        {"name": "J1 League",          "sdb": "Japanese J1 League",                       "id": "4395"},
        {"name": "J2 League",          "sdb": "Japanese J2 League",                       "id": "4494"},
    ],
    "South Korea": [
        {"name": "K League 1",         "sdb": "Korean K League 1",                        "id": "4396"},
        {"name": "K League 2",         "sdb": "Korean K League 2",                        "id": "4495"},
    ],
    "Australia": [
        {"name": "A-League",           "sdb": "Australian A-League Men",                  "id": "4346"},
    ],
    "Saudi Arabia": [
        {"name": "Saudi Pro League",   "sdb": "Saudi Professional League",                "id": "4405"},
    ],
    "Egypt": [
        {"name": "Premier League",     "sdb": "Egyptian Premier League",                  "id": "4407"},
    ],
    "China": [
        {"name": "Super League",       "sdb": "Chinese Super League",                     "id": "4402"},
        {"name": "China League One",   "sdb": "Chinese Football League One",              "id": "4496"},
    ],
}

# ── Flags and sport emoji ─────────────────────────────────────────────────────
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


# ── League lookup (hardcoded — no API needed) ─────────────────────────────────

def get_football_leagues(country: str) -> List[Dict]:
    """Return hardcoded football leagues for *country* (up to 3)."""
    return LEAGUES_BY_COUNTRY.get(country, [])


# ── Team roster (TheSportsDB, with dual-method fallback) ──────────────────────

async def _fetch_json(url: str) -> Optional[dict]:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, headers=_HEADERS, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    return await resp.json(content_type=None)
    except Exception as exc:
        logger.debug("fetch %s: %s", url, exc)
    return None


async def get_league_teams(league: Dict) -> List[Dict]:
    """Return teams for a league dict (has keys: name, sdb, id).

    Tries TheSportsDB search-by-name first, then lookup-by-id.
    Returns [] if both fail (caller should offer 'type name' fallback).
    """
    raw: Optional[list] = None

    # Method 1: search by league name (more reliable than ID)
    sdb_name = league.get("sdb", league.get("name", ""))
    url1 = (
        "https://www.thesportsdb.com/api/v1/json/3/search_all_teams.php"
        f"?l={quote_plus(sdb_name)}&s=Soccer"
    )
    data = await _fetch_json(url1)
    if data:
        raw = data.get("teams")

    # Method 2: lookup by league ID
    if not raw:
        league_id = league.get("id", "")
        if league_id:
            url2 = (
                "https://www.thesportsdb.com/api/v1/json/3/lookup_all_teams.php"
                f"?id={league_id}"
            )
            data = await _fetch_json(url2)
            if data:
                raw = data.get("teams")

    if not raw:
        logger.warning("No teams found for league '%s'", sdb_name)
        return []

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


async def lookup_team(team_name: str) -> Optional[Dict]:
    """Search TheSportsDB for a team by free-text name."""
    url = (
        "https://www.thesportsdb.com/api/v1/json/3/searchteams.php"
        f"?t={quote_plus(team_name)}"
    )
    data = await _fetch_json(url)
    if not data:
        return None
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
