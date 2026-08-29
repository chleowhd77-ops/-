import os
import json
import sqlite3
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("API_KEY")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

if not API_KEY or not GITHUB_TOKEN:
    print("🚨 [보안 경고] .env 파일에서 키를 찾을 수 없습니다! 서버 세팅을 확인하세요.")

GITHUB_REPO = "chleowhd77-ops/-"
API_HOST = "v3.football.api-sports.io"
headers = {'x-apisports-key': API_KEY}
DEFAULT_LOGO = "https://upload.wikimedia.org/wikipedia/commons/thumb/d/d3/Soccerball.svg/120px-Soccerball.svg.png"
STRICT_REFEREES = ["Taylor", "Hernandez", "Lahoz", "Orsato", "Oliver", "Dean", "Turpin", "Makkelie"]

TEAM_NAME_MAP = {
    "광주FC": "Gwangju FC", "포항스틸": "Pohang Steelers", "포항 스틸러스": "Pohang Steelers", "제주SKFC": "Jeju United", "제주 SKFC": "Jeju United", 
    "FC안양": "FC Anyang", "FC 안양": "FC Anyang", "FC서울": "FC Seoul", "대전하나": "Daejeon Citizen", "대전 하나시티즌": "Daejeon Citizen", 
    "충북청주": "Chungbuk Cheongju", "충북청주 프로축구단": "Chungbuk Cheongju", "전남드래": "Jeonnam Dragons", "전남 드래곤즈": "Jeonnam Dragons",
    "김해FC": "Gimhae", "김해FC 2008": "Gimhae", "경남FC": "Gyeongnam FC", "수원삼성": "Suwon Samsung", "수원 삼성블루윙즈": "Suwon Samsung", "수원FC": "Suwon FC",
    "부산아이": "Busan I Park", "부산 아이파크": "Busan I Park", "화성FC": "Hwaseong", "인천유나": "Incheon United", "인천 유나이티드": "Incheon United",
    "김천상무": "Gimcheon Sangmu", "김천상무 프로축구단": "Gimcheon Sangmu", "부천FC": "Bucheon FC 1995", "부천FC 1995": "Bucheon FC 1995", 
    "전북현대": "Jeonbuk Motors", "전북 현대모터스": "Jeonbuk Motors", "울산HDFC": "Ulsan Hyundai", "울산 HDFC": "Ulsan Hyundai", "강원FC": "Gangwon FC",
    "서울이랜드": "Seoul E-Land", "서울 이랜드": "Seoul E-Land", "안산그리": "Ansan Greeners", "안산 그리너스": "Ansan Greeners", "대구FC": "Daegu FC", 
    "충남아산": "Chungnam Asan", "충남아산 프로축구단": "Chungnam Asan", "김포FC": "Gimpo FC", "천안시티": "Cheonan City", "천안 시티FC": "Cheonan City", 
    "파주프런": "Paju Citizen", "파주 프런티어": "Paju Citizen", "성남FC": "Seongnam FC", "용인FC": "Yongin",
    "맨체스C": "Manchester City", "맨체스터 시티": "Manchester City", "리버풀": "Liverpool", "뉴캐슬U": "Newcastle", "뉴캐슬 유나이티드": "Newcastle",
    "본머스": "Bournemouth", "AFC본머스": "Bournemouth", "브라이턴": "Brighton", "브라이턴&호브 앨비언": "Brighton", "A빌라": "Aston Villa", "애스턴 빌라": "Aston Villa",
    "노팅엄F": "Nottingham Forest", "노팅엄 포리스트": "Nottingham Forest", "리즈U": "Leeds", "리즈 유나이티드": "Leeds", "에버턴": "Everton",
    "크리스털": "Crystal Palace", "크리스털 팰리스": "Crystal Palace", "입스위치": "Ipswich", "입스위치 타운": "Ipswich", "선덜랜드": "Sunderland",
    "브렌트퍼": "Brentford", "브렌트퍼드": "Brentford", "토트넘": "Tottenham", "토트넘 홋스퍼": "Tottenham", "아스널": "Arsenal",
    "맨유": "Manchester United", "맨체스U": "Manchester United", "맨체스터 유나이티드": "Manchester United", "웨스트햄 유나이티드": "West Ham", "웨스트브로미치 앨비언": "West Brom", "번리": "Burnley",
    "코번트리": "Coventry", "코번트리 시티": "Coventry", "버밍엄 시티": "Birmingham", "브리스틀 시티": "Bristol City", "링컨 시티": "Lincoln", "포츠머스": "Portsmouth",
    "밀월": "Millwall", "노리치 시티": "Norwich City", "헐시티": "Hull City", "헐 시티": "Hull City", "블랙번 로버스": "Blackburn", "미들즈브러": "Middlesbrough",
    "더비 카운티": "Derby", "카디프 시티": "Cardiff City", "프레스턴 노스엔드": "Preston", "울버햄튼 원더러스": "Wolves", "울버햄튼": "Wolves",
    "퀸즈파크 레인저스": "QPR", "볼턴 원더러스": "Bolton", "사우샘프턴": "Southampton", "스토크 시티": "Stoke City", "스완지 시티": "Swansea",
    "셰필드 유나이티드": "Sheffield Utd", "찰턴 애슬레틱": "Charlton", "렉섬": "Wrexham", "왓포드": "Watford", "풀럼": "Fulham", "첼시": "Chelsea",
    "프로시노": "Frosinone", "프로시노네": "Frosinone", "유벤투스": "Juventus", "베네치아": "Venezia", "US레체": "Lecce",
    "아탈란타": "Atalanta", "아탈란타BC": "Atalanta", "사수올로": "Sassuolo", "US사수올로": "Sassuolo", "토리노": "Torino", "AC밀란": "AC Milan",
    "제노아": "Genoa", "나폴리": "Napoli", "SSC나폴리": "Napoli", "파르마": "Parma", "칼리아리": "Cagliari", "인테르나치오날레 밀라노": "Inter",
    "인테르": "Inter", "AC몬차": "Monza", "우디네세": "Udinese", "코모1907": "Como", "볼로냐": "Bologna", "라치오": "Lazio", "SS라치오": "Lazio", "AS로마": "Roma", "피오렌티": "Fiorentina", "ACF피오렌티나": "Fiorentina",
    "레알 마드리드": "Real Madrid", "바르셀로나": "Barcelona", "아틀레티코 마드리드": "Atletico Madrid", "비야레알": "Villarreal",
    "레알 베티스": "Real Betis", "레알 소시에다드": "Real Sociedad", "발렌시아": "Valencia", "RC셀타데비고": "Celta Vigo", 
    "RCD에스파뇰": "Espanyol", "헤타페": "Getafe", "라싱 산탄데르": "Racing Santander", "엘체": "Elche", "오사수나": "Osasuna", "레반테": "Levante", "말라가": "Malaga", "데포르티보 아코루냐": "Deportivo La Coruna",
    "파리 생제르맹": "Paris Saint Germain", "AS모나코": "Monaco", "올랭피크드 마르세유": "Marseille", "올랭피크 리옹": "Lyon",
    "RC스트라스부르": "Strasbourg", "RC랑스": "Lens", "AJ오세르": "Auxerre", "르망FC": "Le Mans", "스타드 브레스투아29": "Brest",
    "OGC니스": "Nice", "로리앙": "Lorient", "툴루즈": "Toulouse", "트루아AC": "Troyes", "파리FC": "Paris FC", "스타드 렌": "Rennes",
    "르아브르AC": "Le Havre", "앙제SCO": "Angers", "릴OSC": "Lille",
    "도르트문트": "Borussia Dortmund", "함부르크": "Hamburger SV", "바이에른 뮌헨": "Bayern Munich",
    "RB라이프치히": "RB Leipzig", "묀헨글라트바흐": "Borussia Monchengladbach", "FSV마인츠05": "FSV Mainz 05",
    "파더보른07": "SC Paderborn 07", "프랑크푸르트": "Eintracht Frankfurt",
    "포르튀나 시타르트": "Fortuna Sittard", "AZ알크마르": "AZ Alkmaar", "스파르타 로테르담": "Sparta Rotterdam", "위트레흐트": "Utrecht",
    "엑셀시오르 로테르담": "Excelsior", "엑셀시오르": "Excelsior",
    "SC헤이렌베인": "Heerenveen", "PEC즈볼러": "PEC Zwolle", "고어헤드 이글스": "Go Ahead Eagles", "ADO덴하흐": "ADO Den Haag",
    "PSV에인트호번": "PSV Eindhoven", "흐로닝언": "Groningen", "SC캄뷔르": "Cambuur", "페예노르트": "Feyenoord",
    "가시와 레이솔": "Kashiwa Reysol", "V바렌 나가사키": "V-Varen Nagasaki", "FC도쿄": "FC Tokyo", "제프 유나이티드": "JEF United Chiba",
    "가시마 앤틀러스": "Kashima Antlers", "아비스파 후쿠오카": "Avispa Fukuoka", "파지아노 오카야마": "Fagiano Okayama", "도쿄 베르디": "Tokyo Verdy",
    "나고야 그램퍼스": "Nagoya Grampus", "감바 오사카": "Gamba Osaka", "교토 상가FC": "Kyoto Sanga", "미토 홀리호크": "Mito Hollyhock",
    "세레소 오사카": "Cerezo Osaka", "시미즈 에스펄스": "Shimizu S-Pulse", "산프레체 히로시마": "Sanfrecce Hiroshima", "가와사키 프론탈레": "Kawasaki Frontale",
    "요코하마 F마리노스": "Yokohama F. Marinos", "비셀 고베": "Vissel Kobe", "FC마치다 젤비아": "Machida Zelvia", "우라와 레드": "Urawa Red Diamonds",
    "콘사도레 삿포로": "Consadole Sapporo", "RB오미야 아르디자": "Omiya Ardija", "오미야 아르디자": "Omiya Ardija",
    "반라우레 하치노헤FC": "Vanraure Hachinohe", "반라우레 하치노헤": "Vanraure Hachinohe", "베갈타 센다이": "Vegalta Sendai", 
    "블라우블리츠 아키타": "Blaublitz Akita", "반포레 고후": "Ventforet", "카탈레 도야마": "Kataller", 
    "FC이마바리": "Imabari", "이마바리": "Imabari", "몬테디오 야마가타": "Montedio Yamagata", "요코하마FC": "Yokohama FC", 
    "알비렉스 니가타": "Albirex Niigata", "후지에다 MYFC": "Fujieda", "주빌로 이와타": "Jubilo Iwata", 
    "도쿠시마 보르티스": "Tokushima Vortis", "사간 도스": "Sagan Tosu", "도치기 시티FC": "Tochigi", "도치기": "Tochigi",
    "오이타 트리니타": "Oita Trinita", "이와키FC": "Iwaki", "이와키": "Iwaki", "테게바자로 미야자키": "Tegevajaro", 
    "쇼난 벨마레": "Shonan Bellmare",
    "태국": "Thailand", "베트남": "Vietnam",
    "샬럿FC": "Charlotte", "DC유나이티드": "DC United", "FC신시내티": "FC Cincinnati", "시애틀 사운더스FC": "Seattle Sounders",
    "인터 마이애미CF": "Inter Miami", "토론토FC": "Toronto FC", "CF몽레알": "Montreal Impact", "LA 갤럭시": "LA Galaxy",
    "뉴욕 레드불스": "New York Red Bulls", "시카고 파이어FC": "Chicago Fire", "올랜도 시티SC": "Orlando City", "레알 솔트레이크": "Real Salt Lake",
    "오스틴FC": "Austin", "필라델피아 유니언": "Philadelphia Union", "내슈빌SC": "Nashville SC", "콜럼버스 크루": "Columbus Crew",
    "세인트루이스 시티SC": "St. Louis City", "휴스턴 다이너모FC": "Houston Dynamo", "밴쿠버 화이트캡스FC": "Vancouver Whitecaps", "FC댈러스": "FC Dallas",
    "LAFC": "Los Angeles FC", "포틀랜드 팀버스": "Portland Timbers", "샌디에이고FC": "San Diego", "콜로라도 래피즈": "Colorado Rapids",
    "새너제이 어스퀘이크스": "San Jose Earthquakes", "미네소타 유나이티드FC": "Minnesota United", "뉴잉글랜드 레벌루션": "New England Revolution",
    "뉴욕 시티FC": "New York City FC", "애틀랜타 유나이티드FC": "Atlanta United", "스포팅 캔자스시티": "Sporting Kansas City",
    "AEK아테": "AEK Athens", "L소피아": "Lokomotiv Sofia", "비킹FK": "Viking FK", "D자그레": "Dinamo Zagreb",
    "NK첼레": "NK Celje", "슬로반브": "Slovan Bratislava", "리옹": "Lyon", "페네르SK": "Fenerbahce",
    "이베리아": "Iberia 1999", "야기엘로": "Jagiellonia Bialystok", "오모니아": "Omonia Nicosia", "신트트라": "Sint-Truiden",
    "플젠": "Viktoria Plzen", "츠르베나": "Crvena Zvezda", "릴레스트": "Lillestrom", "에그나티": "Egnatia",
    "잘츠부르": "Red Bull Salzburg", "미엘뷔": "Mjallby", "카우노잘": "Kauno Zalgiris", "베식타시": "Besiktas",
    "FC툰": "FC Thun", "L포즈난": "Lech Poznan", "C소피아": "CSKA Sofia", "OFI크레": "OFI Crete",
    "페렌츠바": "Ferencvaros", "트라브존": "Trabzonspor", "안더레흐": "Anderlecht", "카이라트": "Kairat Almaty",
    "LASK": "LASK Linz", "셀틱": "Celtic"
}

DIRECT_TEAM_INFO = {
    "제주 SKFC": {"id": 2977, "logo": "https://media.api-sports.io/football/teams/2977.png"},
    "제주": {"id": 2977, "logo": "https://media.api-sports.io/football/teams/2977.png"},
    "제주 유나이티드": {"id": 2977, "logo": "https://media.api-sports.io/football/teams/2977.png"},
    "울산 HDFC": {"id": 2975, "logo": "https://media.api-sports.io/football/teams/2975.png"},
    "울산HD": {"id": 2975, "logo": "https://media.api-sports.io/football/teams/2975.png"},
    "김천상무": {"id": 2978, "logo": "https://media.api-sports.io/football/teams/2978.png"},
    "김천상무 프로축구단": {"id": 2978, "logo": "https://media.api-sports.io/football/teams/2978.png"},
    "강원FC": {"id": 2972, "logo": "https://media.api-sports.io/football/teams/2972.png"},
    "포항스틸": {"id": 2974, "logo": "https://media.api-sports.io/football/teams/2974.png"},
    "포항 스틸러스": {"id": 2974, "logo": "https://media.api-sports.io/football/teams/2974.png"},
    "FC서울": {"id": 2766, "logo": "https://media.api-sports.io/football/teams/2766.png"},
    "FC 서울": {"id": 2766, "logo": "https://media.api-sports.io/football/teams/2766.png"},
    "서울FC": {"id": 2766, "logo": "https://media.api-sports.io/football/teams/2766.png"},
    "수원FC": {"id": 2980, "logo": "https://media.api-sports.io/football/teams/2980.png"},
    "광주FC": {"id": 2983, "logo": "https://media.api-sports.io/football/teams/2983.png"},
    "인천유나": {"id": 2973, "logo": "https://media.api-sports.io/football/teams/2973.png"},
    "인천 유나이티드": {"id": 2973, "logo": "https://media.api-sports.io/football/teams/2973.png"},
    "전북현대": {"id": 2971, "logo": "https://media.api-sports.io/football/teams/2971.png"},
    "전북 현대모터스": {"id": 2971, "logo": "https://media.api-sports.io/football/teams/2971.png"},
    "대전하나": {"id": 2985, "logo": "https://media.api-sports.io/football/teams/2985.png"},
    "대전 하나시티즌": {"id": 2985, "logo": "https://media.api-sports.io/football/teams/2985.png"},
    "FC안양": {"id": 2986, "logo": "https://media.api-sports.io/football/teams/2986.png"},
    "FC 안양": {"id": 2986, "logo": "https://media.api-sports.io/football/teams/2986.png"},
    "전남드래": {"id": 2988, "logo": "https://media.api-sports.io/football/teams/2988.png"},
    "전남 드래곤즈": {"id": 2988, "logo": "https://media.api-sports.io/football/teams/2988.png"},
    "서울이랜드": {"id": 2987, "logo": "https://media.api-sports.io/football/teams/2987.png"},
    "서울 이랜드": {"id": 2987, "logo": "https://media.api-sports.io/football/teams/2987.png"},
    "수원삼성": {"id": 2976, "logo": "https://media.api-sports.io/football/teams/2976.png"},
    "수원 삼성블루윙즈": {"id": 2976, "logo": "https://media.api-sports.io/football/teams/2976.png"},
    "부산아이": {"id": 2990, "logo": "https://media.api-sports.io/football/teams/2990.png"},
    "부산 아이파크": {"id": 2990, "logo": "https://media.api-sports.io/football/teams/2990.png"},
    "부천FC": {"id": 2984, "logo": "https://media.api-sports.io/football/teams/2984.png"},
    "부천FC 1995": {"id": 2984, "logo": "https://media.api-sports.io/football/teams/2984.png"},
    "김포FC": {"id": 10453, "logo": "https://media.api-sports.io/football/teams/10453.png"},
    "충남아산": {"id": 3155, "logo": "https://media.api-sports.io/football/teams/3155.png"},
    "충남아산 프로축구단": {"id": 3155, "logo": "https://media.api-sports.io/football/teams/3155.png"},
    "충북청주": {"id": 10452, "logo": "https://media.api-sports.io/football/teams/10452.png"},
    "충북청주 프로축구단": {"id": 10452, "logo": "https://media.api-sports.io/football/teams/10452.png"},
    "안산그리": {"id": 2989, "logo": "https://media.api-sports.io/football/teams/2989.png"},
    "안산 그리너스": {"id": 2989, "logo": "https://media.api-sports.io/football/teams/2989.png"},
    "경남FC": {"id": 2981, "logo": "https://media.api-sports.io/football/teams/2981.png"},
    "천안시티": {"id": 3410, "logo": "https://media.api-sports.io/football/teams/3410.png"},
    "천안 시티FC": {"id": 3410, "logo": "https://media.api-sports.io/football/teams/3410.png"},
    "베트남": {"id": 24, "logo": "https://media.api-sports.io/football/teams/24.png"},
    "태국": {"id": 25, "logo": "https://media.api-sports.io/football/teams/25.png"},
    "프레스턴 라이온스": {"id": 15001, "logo": DEFAULT_LOGO},
    "사우스 멜버른": {"id": 6542, "logo": "https://media.api-sports.io/football/teams/6542.png"},
    "인디펜디엔테 델바예": {"id": 1133, "logo": "https://media.api-sports.io/football/teams/1133.png"},
    "데포르테스 톨리마": {"id": 1184, "logo": "https://media.api-sports.io/football/teams/1184.png"},
    "발렌시아": {"id": 532, "logo": "https://media.api-sports.io/football/teams/532.png"},
    "레알 베티스": {"id": 543, "logo": "https://media.api-sports.io/football/teams/543.png"},
    "버밍엄 시티": {"id": 33, "logo": "https://media.api-sports.io/football/teams/33.png"},
    "브렌트퍼드": {"id": 55, "logo": "https://media.api-sports.io/football/teams/55.png"},
    "노팅엄 포리스트": {"id": 65, "logo": "https://media.api-sports.io/football/teams/65.png"},
    "리즈 유나이티드": {"id": 63, "logo": "https://media.api-sports.io/football/teams/63.png"},
    "LASK": {"id": 649, "logo": "https://media.api-sports.io/football/teams/649.png"},
    "셀틱": {"id": 247, "logo": "https://media.api-sports.io/football/teams/247.png"},
    "FK보되 글림트": {"id": 353, "logo": "https://media.api-sports.io/football/teams/353.png"},
    "NEC네이메헌": {"id": 417, "logo": "https://media.api-sports.io/football/teams/417.png"},
    "블랙번 로버스": {"id": 43, "logo": "https://media.api-sports.io/football/teams/43.png"},
    "셰필드 유나이티드": {"id": 62, "logo": "https://media.api-sports.io/football/teams/62.png"},
    "사우샘프턴": {"id": 41, "logo": "https://media.api-sports.io/football/teams/41.png"},
    "웨스트햄 유나이티드": {"id": 48, "logo": "https://media.api-sports.io/football/teams/48.png"},
    "스토크 시티": {"id": 68, "logo": "https://media.api-sports.io/football/teams/68.png"},
    "헐 시티": {"id": 66, "logo": "https://media.api-sports.io/football/teams/66.png"},
    "FC툰": {"id": 792, "logo": DEFAULT_LOGO},
    "페렌츠바": {"id": 594, "logo": DEFAULT_LOGO},
    "CF몬테레이": {"id": 2284, "logo": "https://media.api-sports.io/football/teams/2284.png"},
    "클루브 레온": {"id": 2288, "logo": "https://media.api-sports.io/football/teams/2288.png"},
    "마카비 하이파": {"id": 4440, "logo": "https://media.api-sports.io/football/teams/4440.png"},
    "시카고 파이어FC": {"id": 254, "logo": "https://media.api-sports.io/football/teams/254.png"},
    "레알 솔트레이크": {"id": 257, "logo": "https://media.api-sports.io/football/teams/257.png"},
    "사바FK": {"id": 20456, "logo": "https://media.api-sports.io/football/teams/20456.png"},
    "하포엘 베르셰바": {"id": 4443, "logo": "https://media.api-sports.io/football/teams/4443.png"},
    "NK첼레": {"id": 579, "logo": "https://media.api-sports.io/football/teams/579.png"},
    "야기엘로": {"id": 336, "logo": "https://media.api-sports.io/football/teams/336.png"},
    "신트트라": {"id": 738, "logo": "https://media.api-sports.io/football/teams/738.png"},
    "OFI크레": {"id": 249, "logo": "https://media.api-sports.io/football/teams/249.png"},
    "퀸즐랜드 라이온스": {"id": 6516, "logo": "https://media.api-sports.io/football/teams/6516.png"},
"신트트라": {"id": 738, "logo": "https://media.api-sports.io/football/teams/738.png"},
    "OFI크레": {"id": 249, "logo": "https://media.api-sports.io/football/teams/249.png"},
    "퀸즐랜드 라이온스": {"id": 6516, "logo": "https://media.api-sports.io/football/teams/6516.png"},
    "아라라트 아르메니아": {"id": 5934, "logo": "https://media.api-sports.io/football/teams/5934.png"},
    "신트 트라위던vv": {"id": 738, "logo": "https://media.api-sports.io/football/teams/738.png"},
    "잘츠부르크": {"id": 571, "logo": "https://media.api-sports.io/football/teams/571.png"},
    "OFI크레타": {"id": 249, "logo": "https://media.api-sports.io/football/teams/249.png"},
    "데포르티보 톨루카": {"id": 2282, "logo": "https://media.api-sports.io/football/teams/2282.png"},
    "신트 트라위던VV": {"id": 738, "logo": "https://media.api-sports.io/football/teams/738.png"},
    "신트 트라위던vv": {"id": 738, "logo": "https://media.api-sports.io/football/teams/738.png"},
    "FC툰": {"id": 792, "logo": "https://media.api-sports.io/football/teams/792.png"},
    "FC 툰": {"id": 792, "logo": "https://media.api-sports.io/football/teams/792.png"}
}

def init_cache_db():
    try:
        conn = sqlite3.connect("ai_predictions.db")
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE IF NOT EXISTS db_meta (version INTEGER)")
        cursor.execute("SELECT version FROM db_meta")
        row = cursor.fetchone()
        if not row or row[0] < 3:
            cursor.execute("DROP TABLE IF EXISTS predictions")
            cursor.execute("DELETE FROM db_meta")
            cursor.execute("INSERT INTO db_meta (version) VALUES (3)")
            conn.commit()
        cursor.execute("CREATE TABLE IF NOT EXISTS api_cache (cache_key TEXT PRIMARY KEY, cache_value TEXT, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, match_id TEXT UNIQUE, league TEXT, home_team TEXT, away_team TEXT,
                prob_pick TEXT, prob_pick_prob REAL,
                ev_pick TEXT, ev_pick_prob REAL,
                odd_h REAL, odd_d REAL, odd_a REAL,
                actual_score TEXT DEFAULT '-:-', actual_result TEXT DEFAULT 'PENDING', 
                is_correct_prob INTEGER DEFAULT 0, is_correct_ev INTEGER DEFAULT 0,
                ai_note TEXT DEFAULT '', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, 
                is_toto14 INTEGER DEFAULT 0, api_fixture_id INTEGER DEFAULT 0, match_time TEXT DEFAULT ''
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS prediction_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id TEXT NOT NULL,
                analysis_version TEXT NOT NULL,
                stage TEXT NOT NULL,
                confidence REAL DEFAULT 0,
                prob_pick TEXT,
                prob_pick_prob REAL,
                ev_pick TEXT,
                ev_pick_prob REAL,
                odd_h REAL,
                odd_d REAL,
                odd_a REAL,
                api_fixture_id INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_prediction_snapshots_match ON prediction_snapshots(match_id, created_at)")
        conn.commit()
        conn.close()
    except Exception as e: print(f"❌ [DB 에러] 초기화 실패: {e}")

def get_db_cache(key, ttl_hours):
    try:
        conn = sqlite3.connect("ai_predictions.db")
        cursor = conn.cursor()
        cursor.execute("SELECT cache_value, updated_at FROM api_cache WHERE cache_key = ?", (key,))
        row = cursor.fetchone()
        conn.close()
        if row:
            val, updated_at = row
            updated_time = datetime.strptime(updated_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - updated_time < timedelta(hours=ttl_hours):
                return json.loads(val)
    except Exception as e: 
        print(f"⚠️ [관제 봇 떡밥] DB 캐시 읽기 실패 ({key}): {e}")
    return None

def set_db_cache(key, value):
    try:
        conn = sqlite3.connect("ai_predictions.db")
        cursor = conn.cursor()
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("INSERT OR REPLACE INTO api_cache (cache_key, cache_value, updated_at) VALUES (?, ?, ?)", (key, json.dumps(value), now_str))
        conn.commit()
        conn.close()
    except Exception as e: 
        print(f"⚠️ [관제 봇 떡밥] DB 캐시 쓰기 실패 ({key}): {e}")

DIRECT_TEAM_INFO["신트 트라위던VV"] = {"id": 738, "logo": "https://media.api-sports.io/football/teams/738.png"}
DIRECT_TEAM_INFO["신트 트라위던vv"] = {"id": 738, "logo": "https://media.api-sports.io/football/teams/738.png"}
DIRECT_TEAM_INFO["FC툰"] = {"id": 792, "logo": "https://media.api-sports.io/football/teams/792.png"}
DIRECT_TEAM_INFO["FC 툰"] = {"id": 792, "logo": "https://media.api-sports.io/football/teams/792.png"}
DIRECT_TEAM_INFO["FC툰"] = {"id": 729, "logo": "https://media.api-sports.io/football/teams/729.png"}
DIRECT_TEAM_INFO["FC 툰"] = {"id": 729, "logo": "https://media.api-sports.io/football/teams/729.png"}

DIRECT_TEAM_INFO["FC툰"] = {"id": 1012, "logo": "https://media.api-sports.io/football/teams/1012.png"}
DIRECT_TEAM_INFO["FC 툰"] = {"id": 1012, "logo": "https://media.api-sports.io/football/teams/1012.png"}

DIRECT_TEAM_INFO["아라라트 아르메니아"] = {"id": 3683, "logo": "https://media.api-sports.io/football/teams/3683.png"}

DIRECT_TEAM_INFO.update({
    "RB라이프치히": {"id": 173, "logo": "https://media.api-sports.io/football/teams/173.png"},
    "묀헨글라트바흐": {"id": 163, "logo": "https://media.api-sports.io/football/teams/163.png"},
    "FSV마인츠05": {"id": 164, "logo": "https://media.api-sports.io/football/teams/164.png"},
    "파더보른07": {"id": 185, "logo": "https://media.api-sports.io/football/teams/185.png"},
    "프랑크푸르트": {"id": 169, "logo": "https://media.api-sports.io/football/teams/169.png"},
    "함부르크": {"id": 175, "logo": "https://media.api-sports.io/football/teams/175.png"},
})

