"""SEC EDGAR 재무제표(XBRL companyfacts) 조회 및 분기 단위 재구성.

SEC 공개 API 사용 (인증 불필요, User-Agent 헤더만 요구):
  - https://www.sec.gov/files/company_tickers.json  (ticker -> CIK)
  - https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json  (XBRL 전체 팩트)

XBRL duration fact는 (start, end) 구간 길이로 분기/누적을 구분한다 (실측 확인):
  ~90일  = 단일 분기 (10-Q의 "three months ended")
  ~181일 = 반기 누적, ~272일 = 3분기 누적, ~360일+ = 연간 (10-K)
  4분기는 직접 보고되지 않아 연간 - 3분기 누적으로 역산한다.

주의: API가 붙여주는 'fy' 필드는 신뢰할 수 없다 — 같은 팩트라도 그 뒤 몇 년치 10-K/10-Q에
비교연도 컬럼으로 반복 등장하며, 그때마다 "그 10-K/10-Q 자체의 fy"가 붙어 실제 회계기간과
어긋난다(예: NVDA 2023 회계연도 매출이 2025 회계연도 10-K의 비교 컬럼에도 fy=2025로 찍힘).
그래서 그룹핑은 'fy'가 아니라 실제 회계기간 종료일(end date)로 한다.
"""

from datetime import date

import httpx

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

HEADERS = {"User-Agent": "llm_wiki research choys7773@gmail.com"}

# 회사마다 실제 사용하는 XBRL 태그가 다르다(시간에 따라 태그를 바꾸는 경우도 있음).
# 후보 태그의 데이터를 모두 합쳐서(dedup) 사용한다.
CONCEPT_CANDIDATES = {
    "매출액":   ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"],
    "매출원가": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"],
    "매출총이익": ["GrossProfit"],
    "영업이익": ["OperatingIncomeLoss"],
    "당기순이익": ["NetIncomeLoss"],
}


def resolve_cik(ticker: str) -> str | None:
    resp = httpx.get(TICKERS_URL, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    for entry in resp.json().values():
        if entry.get("ticker", "").upper() == ticker.upper():
            return f"{entry['cik_str']:010d}"
    return None


def fetch_companyfacts(cik: str) -> dict:
    resp = httpx.get(FACTS_URL.format(cik=cik), headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _parse_date(d: str) -> date:
    y, m, dd = d.split("-")
    return date(int(y), int(m), int(dd))


_STATEMENT_FORMS = {"10-K", "10-K/A", "10-Q", "10-Q/A", "20-F", "20-F/A"}


def _merged_usd_entries(facts: dict, tags: list[str]) -> list[dict]:
    """여러 후보 태그의 USD 단위 팩트를 (start,end) 기준 dedup해서 합친다.

    DEF 14A(위임장) 등 정기 재무제표가 아닌 서류도 같은 XBRL 태그를 재사용하는
    경우가 있어 더 늦게 제출됐다는 이유로 진짜 10-K/10-Q 값을 덮어쓸 수 있다.
    그래서 재무제표 서류가 아닌 폼은 애초에 후보에서 제외한다.
    """
    usgaap = facts.get("facts", {}).get("us-gaap", {})
    by_period: dict[tuple, dict] = {}
    for tag in tags:
        node = usgaap.get(tag)
        if not node:
            continue
        for e in node.get("units", {}).get("USD", []):
            if "start" not in e or "end" not in e:
                continue
            if e.get("form") not in _STATEMENT_FORMS:
                continue
            key = (e["start"], e["end"])
            existing = by_period.get(key)
            if existing is None or e.get("filed", "") >= existing.get("filed", ""):
                by_period[key] = e
    return list(by_period.values())


def _dedup_by_end(entries: list[dict]) -> dict:
    """같은 종료일의 팩트가 여러 번(재작성 포함) 나오면 가장 최근 제출본을 쓴다."""
    by_end: dict[str, dict] = {}
    for e in entries:
        existing = by_end.get(e["end"])
        if existing is None or e.get("filed", "") >= existing.get("filed", ""):
            by_end[e["end"]] = e
    return by_end


def extract_quarterly(facts: dict, field: str) -> dict:
    """{"2026-01-25": value, ...} — 분기 종료일을 key로 하는 분기 계열."""
    entries = _merged_usd_entries(facts, CONCEPT_CANDIDATES[field])

    quarterly, nine_month, annual = [], [], []
    for e in entries:
        duration = (_parse_date(e["end"]) - _parse_date(e["start"])).days
        if 80 <= duration <= 100:
            quarterly.append(e)
        elif 260 <= duration <= 290:
            nine_month.append(e)
        elif 345 <= duration <= 380 and e.get("form") == "10-K":
            annual.append(e)

    quarterly_by_end = _dedup_by_end(quarterly)
    ninemonth_by_end = _dedup_by_end(nine_month)
    annual_by_end = _dedup_by_end(annual)

    for end, fact in annual_by_end.items():
        if end in quarterly_by_end:
            continue  # 회사가 4분기를 직접 보고하는 드문 경우
        end_date = _parse_date(end)
        match = next(
            (v for e9, v in ninemonth_by_end.items()
             if 80 <= (end_date - _parse_date(e9)).days <= 100),
            None,
        )
        if match is not None:
            quarterly_by_end[end] = {**fact, "val": fact["val"] - match["val"]}

    return {end: fact["val"] for end, fact in sorted(quarterly_by_end.items())}


def build_timeseries(ticker: str) -> dict:
    """{"2026-01-25": {field: val}, ...} 형태로 전 계정 합쳐서 반환. key는 회계분기 종료일."""
    cik = resolve_cik(ticker)
    if not cik:
        return {}
    facts = fetch_companyfacts(cik)

    per_field = {field: extract_quarterly(facts, field) for field in CONCEPT_CANDIDATES}
    all_periods = sorted(set().union(*(d.keys() for d in per_field.values())))

    return {
        period: {field: per_field[field].get(period) for field in CONCEPT_CANDIDATES}
        for period in all_periods
    }
