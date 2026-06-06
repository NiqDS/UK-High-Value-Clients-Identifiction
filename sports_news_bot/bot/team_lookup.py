import logging
from typing import Dict, Optional

import aiohttp

logger = logging.getLogger(__name__)

# fmt: off
COUNTRY_FLAGS: Dict[str, str] = {
    "England":        "🏴󠁧󠁢󠁥󠁮󠁧󠁿",
    "Scotland":       "🏴󠁧󠁢󠁳󠁣󠁴󠁿",
    "Wales":          "🏴󠁧󠁢󠁷󠁬󠁳󠁿",
    "Spain":          "🇪🇸",
    "Germany":        "🇩🇪",
    "France":         "🇫🇷",
    "Italy":          "🇮🇹",
    "Portugal":       "🇵🇹",
    "Netherlands":    "🇳🇱",
    "Belgium":        "🇧🇪",
    "Brazil":         "🇧🇷",
    "Argentina":      "🇦🇷",
    "Russia":         "🇷🇺",
    "Ukraine":        "🇺🇦",
    "United States":  "🇺🇸",
    "USA":            "🇺🇸",
    "Japan":          "🇯🇵",
    "South Korea":    "🇰🇷",
    "China":          "🇨🇳",
    "Australia":      "🇦🇺",
    "Turkey":         "🇹🇷",
    "Mexico":         "🇲🇽",
    "Colombia":       "🇨🇴",
    "Chile":          "🇨🇱",
    "Uruguay":        "🇺🇾",
    "Poland":         "🇵🇱",
    "Austria":        "🇦🇹",
    "Switzerland":    "🇨🇭",
    "Croatia":        "🇭🇷",
    "Serbia":         "🇷🇸",
    "Greece":         "🇬🇷",
    "Romania":        "🇷🇴",
    "Czech Republic": "🇨🇿",
    "Hungary":        "🇭🇺",
    "Sweden":         "🇸🇪",
    "Norway":         "🇳🇴",
    "Denmark":        "🇩🇰",
    "Finland":        "🇫🇮",
    "Ireland":        "🇮🇪",
    "Canada":         "🇨🇦",
    "Saudi Arabia":   "🇸🇦",
    "Egypt":          "🇪🇬",
    "Morocco":        "🇲🇦",
    "Nigeria":        "🇳🇬",
    "South Africa":   "🇿🇦",
    "Algeria":        "🇩🇿",
    "Tunisia":        "🇹🇳",
    "Iran":           "🇮🇷",
    "India":          "🇮🇳",
    "Indonesia":      "🇮🇩",
    "Thailand":       "🇹🇭",
    "New Zealand":    "🇳🇿",
}
# fmt: on

SPORT_EMOJI: Dict[str, str] = {
    "Soccer":      "⚽",
    "Football":    "🏈",
    "Basketball":  "🏀",
    "Baseball":    "⚾",
    "Hockey":      "🏒",
    "Tennis":      "🎾",
    "Rugby":       "🏉",
    "Cricket":     "🏏",
    "Volleyball":  "🏐",
    "Handball":    "🤾",
    "Baseball":    "⚾",
    "Golf":        "⛳",
    "Cycling":     "🚴",
    "Athletics":   "🏃",
    "Swimming":    "🏊",
    "Boxing":      "🥊",
    "MMA":         "🥋",
}


async def lookup_team(team_name: str) -> Optional[Dict]:
    """Query TheSportsDB for a team.  Returns the best match or None."""
    url = (
        "https://www.thesportsdb.com/api/v1/json/3/searchteams.php"
        f"?t={team_name}"
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=8)
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json(content_type=None)
                teams = data.get("teams")
                if not teams:
                    return None
                t = teams[0]
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
        logger.warning("TheSportsDB lookup failed for '%s': %s", team_name, exc)
        return None
