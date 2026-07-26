"""DART 재무제표(fnlttSinglAcntAll) 조회 및 분기 단위 재구성.

DART 손익계산서 항목의 thstrm_amount는 보고서 유형과 무관하게 "해당 분기 단독" 값이고,
thstrm_add_amount는 "연초 누적" 값이다 (실측으로 확인: 반기보고서 thstrm_amount = 2분기
단독 매출, thstrm_add_amount = 반기 누적 매출). 사업보고서(연간)만 예외로 thstrm_amount가
연간 총액이라 4분기는 별도로 역산해야 한다:
  1Q = 1분기보고서 thstrm_amount
  2Q = 반기보고서 thstrm_amount
  3Q = 3분기보고서 thstrm_amount
  4Q = 사업보고서 thstrm_amount - 3분기보고서 thstrm_add_amount (연초~3분기 누적)
"""

import httpx

FNLTT_URL = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"

REPRT_CODES = {
    "11013": "1Q",   # 1분기보고서
    "11012": "2Q",   # 반기보고서
    "11014": "3Q",   # 3분기보고서
    "11011": "4Q",   # 사업보고서 (연간)
}

# 계정명은 회사마다 표기가 조금씩 다르다. 후보 목록 중 먼저 매치되는 것을 사용.
# 짧고 일반적인 문자열("매출" 등)은 다른 계정명의 부분 문자열이 될 수 있어 넣지 않는다
# (예: "매출원가"에 "매출"이 포함되어 매출액으로 오매칭됨).
ACCOUNT_CANDIDATES = {
    "매출액":   ["매출액", "수익(매출액)", "영업수익"],
    "매출원가": ["매출원가"],
    "매출총이익": ["매출총이익"],
    "판관비":   ["판매비와관리비", "판매비와 관리비"],
    "영업이익": ["영업이익"],
    "당기순이익": ["당기순이익", "분기순이익", "반기순이익"],
}
_SORTED_CANDIDATES = sorted(
    ((c, field) for field, cs in ACCOUNT_CANDIDATES.items() for c in cs),
    key=lambda pair: len(pair[0]),
    reverse=True,
)


def fetch_report(api_key: str, corp_code: str, bsns_year: str, reprt_code: str, fs_div: str = "OFS") -> list[dict]:
    """단일 (연도, 보고서유형) 재무제표 항목 목록. 별도(OFS) 우선, 없으면 연결(CFS)."""
    resp = httpx.get(
        FNLTT_URL,
        params={
            "crtfc_key": api_key,
            "corp_code": corp_code,
            "bsns_year": bsns_year,
            "reprt_code": reprt_code,
            "fs_div": fs_div,
        },
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") == "000":
        return data.get("list", [])
    if fs_div == "OFS":
        return fetch_report(api_key, corp_code, bsns_year, reprt_code, fs_div="CFS")
    return []


def _match_account(account_nm: str) -> str | None:
    for candidate, field in _SORTED_CANDIDATES:
        if candidate in account_nm:
            return field
    return None


def _parse_amount(raw: str) -> float | None:
    if not raw:
        return None
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return None


def extract_is_fields(items: list[dict]) -> dict:
    """단일 보고서 응답에서 손익계산서 핵심 계정만 뽑는다.

    반환: {"매출액": {"thstrm": x, "thstrm_add": y}, ...}
    """
    result: dict = {}
    for item in items:
        if item.get("sj_div") not in ("IS", "CIS"):
            continue
        field = _match_account(item.get("account_nm", ""))
        if not field or field in result:
            continue
        result[field] = {
            "thstrm": _parse_amount(item.get("thstrm_amount", "")),
            "thstrm_add": _parse_amount(item.get("thstrm_add_amount", "")),
        }
    return result


def _sub(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    return a - b


def build_year_quarters(api_key: str, corp_code: str, bsns_year: str) -> dict:
    """한 사업연도의 1~4분기 손익 항목을 재구성한다. 아직 발표 안 된 분기는 생략."""
    by_report = {}
    for reprt_code in REPRT_CODES:
        items = fetch_report(api_key, corp_code, bsns_year, reprt_code)
        if items:
            by_report[reprt_code] = extract_is_fields(items)

    quarters: dict[str, dict] = {}
    fields = ACCOUNT_CANDIDATES.keys()

    if "11013" in by_report:
        quarters["1Q"] = {f: by_report["11013"].get(f, {}).get("thstrm") for f in fields}

    if "11012" in by_report:
        quarters["2Q"] = {f: by_report["11012"].get(f, {}).get("thstrm") for f in fields}

    if "11014" in by_report:
        quarters["3Q"] = {f: by_report["11014"].get(f, {}).get("thstrm") for f in fields}

    if "11011" in by_report and "11014" in by_report:
        quarters["4Q"] = {
            f: _sub(
                by_report["11011"].get(f, {}).get("thstrm"),
                by_report["11014"].get(f, {}).get("thstrm_add"),
            )
            for f in fields
        }
    elif "11011" in by_report and "11014" not in by_report:
        # 3분기 누적치가 없으면(외감 예외 등) 연간치를 4Q로 대체할 수 없으니 FY로만 보존
        quarters["FY"] = {f: by_report["11011"].get(f, {}).get("thstrm") for f in fields}

    return quarters


def build_timeseries(api_key: str, corp_code: str, years: list[str]) -> dict:
    """여러 연도를 모아 {year: {quarter: {field: amount}}} 형태로 반환."""
    return {year: build_year_quarters(api_key, corp_code, year) for year in years}
