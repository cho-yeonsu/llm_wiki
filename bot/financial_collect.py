#!/usr/bin/env python3
"""정기공시(DART)/SEC 재무제표를 수집해 시계열을 갱신하고, 새 분기가 나오면
Gemini로 재무 분석 리포트를 다시 생성한다.

dart_collector.py와 마찬가지로 로컬 파일시스템에 직접 쓰고, 실제 git commit/push는
GitHub Actions 워크플로우의 셸 스텝이 담당한다 (여기서는 GitHub API를 쓰지 않는다).
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import yaml

import dart_financials
import sec_financials
from financial_analyzer import (
    flatten_dart_timeseries,
    flatten_sec_timeseries,
    generate_report,
)

CODES_PATH = Path("schema/codes.yaml")
OUTPUT_BASE = Path("sources/투자/재무제표")

DART_YEARS_BACK = 5  # 현재 연도 포함 몇 개 연도까지 조회할지


def _to_millions(periods: list[dict]) -> list[dict]:
    out = []
    for p in periods:
        row = {"label": p["label"]}
        for k, v in p.items():
            if k == "label":
                continue
            row[k] = round(v / 1_000_000, 1) if isinstance(v, (int, float)) else v
        out.append(row)
    return out


def _load_cache(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def process_dart_company(api_key: str, gemini_key: str, company: dict, current_year: int) -> bool:
    name = company["canonical"]
    corp_code = company.get("dart_corp_code")
    if not corp_code:
        print(f"  ⚠️ {name}: dart_corp_code 없음, 건너뜀")
        return False

    years = [str(y) for y in range(current_year - DART_YEARS_BACK + 1, current_year + 1)]
    ts = dart_financials.build_timeseries(api_key, corp_code, years)

    company_dir = OUTPUT_BASE / name
    cache_path = company_dir / "timeseries.json"
    old = _load_cache(cache_path)

    if ts == old:
        print(f"  {name}: 변경 없음")
        return False

    print(f"  {name}: 새 분기 데이터 감지, 리포트 갱신")
    periods = _to_millions(flatten_dart_timeseries(ts))
    report = generate_report(gemini_key, name, "KRW", "백만원", periods)

    _write(cache_path, json.dumps(ts, ensure_ascii=False, indent=2))
    _write(company_dir / f"{name}_DART공시_분석.md", report)
    return True


def process_sec_company(gemini_key: str, company: dict) -> bool:
    name = company["canonical"]
    ticker = company.get("us_ticker")
    if not ticker:
        print(f"  ⚠️ {name}: us_ticker 없음, 건너뜀")
        return False

    ts = sec_financials.build_timeseries(ticker)
    if not ts:
        print(f"  ⚠️ {name}({ticker}): SEC EDGAR에 데이터 없음 (CIK 미등록 또는 비상장/OTC 비보고 종목). 건너뜀")
        return False

    company_dir = OUTPUT_BASE / name
    cache_path = company_dir / "sec_timeseries.json"
    old = _load_cache(cache_path)

    if ts == old:
        print(f"  {name}: 변경 없음")
        return False

    print(f"  {name}: 새 분기 데이터 감지, 리포트 갱신")
    periods = _to_millions(flatten_sec_timeseries(ts))
    report = generate_report(gemini_key, name, "USD", "백만달러", periods)

    _write(cache_path, json.dumps(ts, ensure_ascii=False, indent=2))
    _write(company_dir / f"{name}_SEC공시_분석.md", report)
    return True


def main():
    if not CODES_PATH.exists():
        print("schema/codes.yaml 없음. 종료.")
        return

    data = yaml.safe_load(CODES_PATH.read_text(encoding="utf-8"))
    companies = data.get("companies", [])
    gemini_key = os.environ["GEMINI_API_KEY"]
    current_year = datetime.now().year

    changed = 0

    dart_targets = [c for c in companies if c.get("dart_collect")]
    if dart_targets:
        dart_key = os.environ.get("DART_API_KEY", "")
        if not dart_key:
            print("DART_API_KEY 없음 — DART 재무제표 수집 건너뜀.")
        else:
            print(f"DART 재무제표 확인 ({len(dart_targets)}개 기업)")
            for company in dart_targets:
                if process_dart_company(dart_key, gemini_key, company, current_year):
                    changed += 1

    sec_targets = [c for c in companies if c.get("sec_collect")]
    if sec_targets:
        print(f"\nSEC 재무제표 확인 ({len(sec_targets)}개 기업)")
        for company in sec_targets:
            if process_sec_company(gemini_key, company):
                changed += 1

    print(f"\n완료: {changed}개 기업 리포트 갱신")
    if changed == 0:
        sys.exit(0)


if __name__ == "__main__":
    main()
