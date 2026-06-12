import logging
from typing import Dict, List, Optional
from urllib.parse import quote_plus

import aiohttp

logger = logging.getLogger(__name__)

# ── Countries ─────────────────────────────────────────────────────────────────
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

# ── Leagues (hardcoded — no API needed for steps 1-2) ─────────────────────────
# espn = ESPN API league slug  (primary team source, free, no key)
# sdb  = TheSportsDB league name (fallback team source)
# id   = TheSportsDB league ID   (second fallback)
LEAGUES_BY_COUNTRY: Dict[str, List[Dict]] = {
    "England": [
        {"name": "Premier League",    "espn": "eng.1", "sdb": "English Premier League",                  "id": "4328"},
        {"name": "Championship",      "espn": "eng.2", "sdb": "English Football League Championship",     "id": "4329"},
        {"name": "League One",        "espn": "eng.3", "sdb": "English Football League One",              "id": "4330"},
    ],
    "Spain": [
        {"name": "La Liga",           "espn": "esp.1", "sdb": "Spanish La Liga",                          "id": "4335"},
        {"name": "La Liga 2",         "espn": "esp.2", "sdb": "Spanish La Liga 2",                        "id": "4480"},
    ],
    "Germany": [
        {"name": "Bundesliga",        "espn": "ger.1", "sdb": "German Bundesliga",                        "id": "4331"},
        {"name": "2. Bundesliga",     "espn": "ger.2", "sdb": "German 2. Bundesliga",                     "id": "4336"},
        {"name": "3. Liga",           "espn": "ger.3", "sdb": "German 3. Liga",                           "id": "4481"},
    ],
    "France": [
        {"name": "Ligue 1",           "espn": "fra.1", "sdb": "French Ligue 1",                           "id": "4334"},
        {"name": "Ligue 2",           "espn": "fra.2", "sdb": "French Ligue 2",                           "id": "4482"},
    ],
    "Italy": [
        {"name": "Serie A",           "espn": "ita.1", "sdb": "Italian Serie A",                          "id": "4332"},
        {"name": "Serie B",           "espn": "ita.2", "sdb": "Italian Serie B",                          "id": "4483"},
    ],
    "Portugal": [
        {"name": "Primeira Liga",     "espn": "por.1", "sdb": "Portuguese Primeira Liga",                 "id": "4344"},
        {"name": "Liga Portugal 2",   "espn": "por.2", "sdb": "Portuguese Segunda Liga",                  "id": "4484"},
    ],
    "Netherlands": [
        {"name": "Eredivisie",        "espn": "ned.1", "sdb": "Dutch Eredivisie",                         "id": "4337"},
        {"name": "Eerste Divisie",    "espn": "ned.2", "sdb": "Dutch Eerste Divisie",                     "id": "4485"},
    ],
    "Belgium": [
        {"name": "First Division A",  "espn": "bel.1", "sdb": "Belgian First Division A",                 "id": "4397"},
        {"name": "First Division B",  "espn": "bel.2", "sdb": "Belgian Pro League",                       "id": "4398"},
    ],
    "Turkey": [
        {"name": "Süper Lig",         "espn": "tur.1", "sdb": "Turkish Süper Lig",                        "id": "4339"},
        {"name": "1. Lig",            "espn": "tur.2", "sdb": "Turkish 1. Lig",                           "id": "4486"},
    ],
    "Russia": [
        {"name": "Premier League",    "espn": "rus.1", "sdb": "Russian Premier League",                   "id": "4340"},
        {"name": "FNL",               "espn": "",      "sdb": "Russian Football National League",          "id": "4487"},
    ],
    "Ukraine": [
        {"name": "Premier League",    "espn": "ukr.1", "sdb": "Ukrainian Premier League",                 "id": "4342"},
    ],
    "Scotland": [
        {"name": "Premiership",       "espn": "sco.1", "sdb": "Scottish Premiership",                     "id": "4341"},
        {"name": "Championship",      "espn": "sco.2", "sdb": "Scottish Championship",                    "id": "4488"},
    ],
    "Greece": [
        {"name": "Super League",      "espn": "gre.1", "sdb": "Greek Super League",                       "id": "4348"},
    ],
    "Poland": [
        {"name": "Ekstraklasa",       "espn": "pol.1", "sdb": "Polish Ekstraklasa",                       "id": "4343"},
        {"name": "I liga",            "espn": "pol.2", "sdb": "Polish I Liga",                            "id": "4489"},
    ],
    "Austria": [
        {"name": "Bundesliga",        "espn": "aut.1", "sdb": "Austrian Football Bundesliga",              "id": "4344"},
    ],
    "Brazil": [
        {"name": "Série A",           "espn": "bra.1", "sdb": "Brazilian Série A",                        "id": "4350"},
        {"name": "Série B",           "espn": "bra.2", "sdb": "Brazilian Série B",                        "id": "4351"},
        {"name": "Série C",           "espn": "bra.3", "sdb": "Brazilian Série C",                        "id": "4490"},
    ],
    "Argentina": [
        {"name": "Primera División",  "espn": "arg.1", "sdb": "Argentine Primera División",               "id": "4406"},
        {"name": "Primera Nacional",  "espn": "arg.2", "sdb": "Argentine Primera Nacional",               "id": "4491"},
    ],
    "Mexico": [
        {"name": "Liga MX",           "espn": "mex.1", "sdb": "Mexican Liga MX",                          "id": "4355"},
        {"name": "Liga de Expansión", "espn": "mex.2", "sdb": "Mexican Liga de Expansión MX",             "id": "4492"},
    ],
    "Colombia": [
        {"name": "Primera A",         "espn": "col.1", "sdb": "Colombian Categoría Primera A",            "id": "4357"},
    ],
    "Chile": [
        {"name": "Primera División",  "espn": "chi.1", "sdb": "Chilean Primera División",                 "id": "4358"},
    ],
    "USA": [
        {"name": "MLS",               "espn": "usa.1", "sdb": "Major League Soccer",                      "id": "4359"},
        {"name": "USL Championship",  "espn": "usa.2", "sdb": "USL Championship",                         "id": "4493"},
    ],
    "Japan": [
        {"name": "J1 League",         "espn": "jpn.1", "sdb": "Japanese J1 League",                       "id": "4395"},
        {"name": "J2 League",         "espn": "jpn.2", "sdb": "Japanese J2 League",                       "id": "4494"},
    ],
    "South Korea": [
        {"name": "K League 1",        "espn": "kor.1", "sdb": "Korean K League 1",                        "id": "4396"},
        {"name": "K League 2",        "espn": "kor.2", "sdb": "Korean K League 2",                        "id": "4495"},
    ],
    "Australia": [
        {"name": "A-League",          "espn": "aus.1", "sdb": "Australian A-League Men",                  "id": "4346"},
    ],
    "Saudi Arabia": [
        {"name": "Saudi Pro League",  "espn": "sau.1", "sdb": "Saudi Professional League",                "id": "4405"},
    ],
    "Egypt": [
        {"name": "Premier League",    "espn": "",      "sdb": "Egyptian Premier League",                  "id": "4407"},
    ],
    "China": [
        {"name": "Super League",      "espn": "chn.1", "sdb": "Chinese Super League",                     "id": "4402"},
        {"name": "China League One",  "espn": "",      "sdb": "Chinese Football League One",              "id": "4496"},
    ],
}

# ── Flags / sport emoji ───────────────────────────────────────────────────────
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


# ── Utility ───────────────────────────────────────────────────────────────────

def get_football_leagues(country: str) -> List[Dict]:
    """Return hardcoded football leagues for *country* (instant, no network)."""
    return LEAGUES_BY_COUNTRY.get(country, [])


async def _fetch_json(url: str) -> Optional[dict]:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, headers=_HEADERS, timeout=aiohttp.ClientTimeout(total=8)
            ) as resp:
                if resp.status == 200:
                    return await resp.json(content_type=None)
    except Exception as exc:
        logger.debug("HTTP fetch failed %s: %s", url, exc)
    return None


# ── Team roster: ESPN first, TheSportsDB as fallback ─────────────────────────

async def _teams_from_espn(espn_code: str) -> List[Dict]:
    """Fetch teams from the ESPN public API (no key required)."""
    url = (
        f"https://site.api.espn.com/apis/site/v2/sports/soccer"
        f"/{espn_code}/teams"
    )
    data = await _fetch_json(url)
    if not data:
        return []
    try:
        raw = data["sports"][0]["leagues"][0]["teams"]
        teams = [
            {
                "id":      t["team"].get("id", ""),
                "name":    t["team"].get("displayName", ""),
                "country": "",
            }
            for t in raw
            if t.get("team", {}).get("displayName")
        ]
        return sorted(teams, key=lambda t: t["name"])
    except (KeyError, IndexError, TypeError) as exc:
        logger.debug("ESPN parse error (%s): %s", espn_code, exc)
        return []


async def _teams_from_sdb_name(sdb_name: str) -> List[Dict]:
    url = (
        "https://www.thesportsdb.com/api/v1/json/3/search_all_teams.php"
        f"?l={quote_plus(sdb_name)}&s=Soccer"
    )
    data = await _fetch_json(url)
    if not data:
        return []
    raw = data.get("teams") or []
    return [
        {"id": t.get("idTeam", ""), "name": t["strTeam"], "country": t.get("strCountry", "")}
        for t in raw
        if t.get("strTeam")
    ]


async def _teams_from_sdb_id(league_id: str) -> List[Dict]:
    url = (
        "https://www.thesportsdb.com/api/v1/json/3/lookup_all_teams.php"
        f"?id={league_id}"
    )
    data = await _fetch_json(url)
    if not data:
        return []
    raw = data.get("teams") or []
    return [
        {"id": t.get("idTeam", ""), "name": t["strTeam"], "country": t.get("strCountry", "")}
        for t in raw
        if t.get("strTeam")
    ]


async def get_league_teams(league: Dict) -> List[Dict]:
    """Return teams for a league dict.

    Tries in order:
      1. ESPN public API  (free, no key, very reliable)
      2. TheSportsDB by league name
      3. TheSportsDB by league ID
    Returns [] if all sources fail; caller should show a 'type name' fallback.
    """
    # 1 — ESPN
    espn_code = league.get("espn", "")
    if espn_code:
        teams = await _teams_from_espn(espn_code)
        if teams:
            logger.info("Teams loaded via ESPN (%s): %d teams", espn_code, len(teams))
            return teams

    # 2 — TheSportsDB by league name
    sdb_name = league.get("sdb", "")
    if sdb_name:
        teams = await _teams_from_sdb_name(sdb_name)
        if teams:
            logger.info("Teams loaded via SDB name (%s): %d teams", sdb_name, len(teams))
            return sorted(teams, key=lambda t: t["name"])

    # 3 — TheSportsDB by league ID
    league_id = league.get("id", "")
    if league_id:
        teams = await _teams_from_sdb_id(league_id)
        if teams:
            logger.info("Teams loaded via SDB id (%s): %d teams", league_id, len(teams))
            return sorted(teams, key=lambda t: t["name"])

    logger.warning("No teams found for league '%s'", league.get("name", "?"))
    return []


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
