"""Published club names are search hints, never assigned provider IDs.

Sources: City Football Group's Shenzhen page (including Sichuan Jiuniu),
AFC's Zhejiang club preview and East Asia league reports for Henan/Shanghai.
Every accepted fixture still requires the original ordered IDs/time/league checks.
"""
import re

CLUBS = {
    '선전신펑청': ('Shenzhen Peng City', 'Sichuan Jiuniu'),
    '선전신펑청FC': ('Shenzhen Peng City', 'Sichuan Jiuniu'),
    '허난FC': ('Henan FC',),
    '허난': ('Henan FC',),
    '저장FC': ('Zhejiang FC',),
    '저장': ('Zhejiang FC',),
    '상하이하이강': ('Shanghai Port',),
    '상하이하이강FC': ('Shanghai Port',),
}
SOURCES = (
    'https://www.cityfootballgroup.com/clubs/shenzhen-peng-city',
    'https://www.the-afc.com/en/club/afc_champions_league/news/preview_-_group_h_melbourne_city_fc_aus_v_zhejiang_fc_chn.html',
    'https://www.the-afc.com/en/more/domestic_leagues.html/news/east-asia-wrap-shanghai-shenhua-in-pole-position-ulsan-held',
)
def key(value):
    return re.sub(r'[^0-9a-z가-힣]', '', str(value or '').casefold())
def search_names(value):
    normalized = key(value)
    for local, names in CLUBS.items():
        if normalized == key(local) or normalized in {key(n) for n in names}:
            return names
    return ()
def translated(value):
    values = search_names(value)
    return values[0] if values else None
def specific_pair_score(local, provider):
    # Shenzhen FC is a different club. Never accept it as Peng City merely
    # because the older generic name matcher considers a shared city a match.
    names = search_names(local)
    if names and names[0] == 'Shenzhen Peng City':
        candidate = key(provider)
        accepted = {key(n+suffix) for n in names for suffix in ('', ' FC', ' Football Club')}
        return 1.0 if candidate in accepted else -1.0
    return None
