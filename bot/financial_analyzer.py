"""분기 재무 시계열을 받아 Claude로 재무 분석 리포트(마크다운)를 생성한다.

claude_client.py의 위키 통합 프롬프트와는 별도의 전용 시스템 프롬프트를 쓴다 —
위키 백링크 생성이 아니라 분기 추세·마진·리스크를 다루는 재무분석 리포트가 목적이라
요구되는 출력 형식과 판단 기준이 완전히 다르다.
"""

import anthropic

SYSTEM_PROMPT = """당신은 상장기업 정기공시(재무제표)를 분석하는 애널리스트입니다.

주어진 분기별 손익 시계열(매출액/매출원가/매출총이익/판관비/영업이익/당기순이익)을 바탕으로
아래 구조의 마크다운 리포트를 작성하세요:

## 0. 한 줄 요약
최근 분기의 핵심 변화(매출 추세 전환, 마진 개선/악화, 흑자/적자 전환 등)를 1~2문장으로.

## 1. 분기별 손익 추세
분기 | 매출액 | 매출원가 | 매출총이익 | 매출총이익률 | 판관비 | 영업이익 | 영업이익률
표 형식. 값이 없는(null) 항목은 "—"로 표기. 연도별 합계 행을 추가해도 좋음.
표 아래에 "읽는 법" 단락으로 추세를 짚어준다 (박스권/반전/마진 압박 등 구체적 근거와 함께).

## 2. 리스크 하이라이트
- 연속 적자, 마진 급락, 매출 정체 등 데이터에서 직접 드러나는 위험 신호만 짚는다.
- 세그먼트별 매출 구성이나 수주잔고 등은 이 데이터에 없으므로 추측하지 말고,
  "세그먼트 분석은 사업보고서 원문 확인 필요"처럼 한계를 명시한다.

규칙:
- 숫자는 반드시 입력된 값만 사용한다 (지어내지 않는다).
- 통화 단위는 프롬프트에서 지정한 대로 표기한다.
- 한국어로 작성한다.
- 마크다운 본문만 반환한다 (코드블록으로 감싸지 않는다)."""


def build_user_message(
    company_name: str,
    currency_label: str,
    unit_label: str,
    periods: list[dict],
) -> str:
    rows = []
    for p in periods:
        rows.append(
            f"- {p['label']}: 매출액={p.get('매출액', '—')}, 매출원가={p.get('매출원가', '—')}, "
            f"매출총이익={p.get('매출총이익', '—')}, 판관비={p.get('판관비', '—')}, "
            f"영업이익={p.get('영업이익', '—')}, 당기순이익={p.get('당기순이익', '—')}"
        )
    series_block = "\n".join(rows)
    return f"""기업명: {company_name}
통화: {currency_label} (단위: {unit_label})

분기별 원시 시계열 (오래된 순):
{series_block}

위 시계열로 리포트를 작성해줘."""


def generate_report(
    api_key: str,
    company_name: str,
    currency_label: str,
    unit_label: str,
    periods: list[dict],
) -> str:
    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": build_user_message(company_name, currency_label, unit_label, periods),
        }],
    )
    return resp.content[0].text


# ─── 시계열 정규화 ──────────────────────────────────────────

def flatten_dart_timeseries(ts: dict) -> list[dict]:
    """dart_financials.build_timeseries() 결과 -> [{"label": "1Q25", **fields}, ...]"""
    quarter_order = {"1Q": 0, "2Q": 1, "3Q": 2, "4Q": 3, "FY": 4}
    periods = []
    for year in sorted(ts.keys()):
        for quarter in sorted(ts[year].keys(), key=lambda q: quarter_order.get(q, 9)):
            label = f"{quarter}{year[-2:]}" if quarter != "FY" else f"FY{year[-2:]}"
            periods.append({"label": label, **ts[year][quarter]})
    return periods


def flatten_sec_timeseries(ts: dict) -> list[dict]:
    """sec_financials.build_timeseries() 결과 -> [{"label": "2026-01-25", **fields}, ...]"""
    return [{"label": end_date, **fields} for end_date, fields in sorted(ts.items())]
