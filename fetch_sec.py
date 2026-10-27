"""
S&P500の財務データを米国SECから取得して CSV にまとめる（GitHub Actions で週1回実行）

出力
  data/financials.csv … 1行1社。ティッカー・会社名・セクター・CIK と、項目_年 の列（例: REV_2025）
  data/meta.json      … 更新日時・対象年

Google Apps Script からは SEC に接続できない（Google-Apps-Script という名乗りが拒否される）ため、
ここで取得した CSV をスプレッドシートが読み込む。
"""
import csv
import datetime
import html
import json
import os
import re
import time
import urllib.error
import urllib.request

CONTACT = os.environ.get("SEC_CONTACT", "contact@example.com")
USER_AGENT = f"StockScreener-Data {CONTACT}"
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# SECから取る項目（コード.gs の旧実装と同じ定義）
# dur = 1年間の合計（売上など）／ inst = 期末時点の残高（自己資本など）
# pick: max = 複数タグの最大値を採用 ／ first = 先に見つかったタグを採用
SEC_ITEMS = [
    ("REV", "dur", "USD", "max",
     ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
      "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet"]),
    ("OPI", "dur", "USD", "first", ["OperatingIncomeLoss"]),
    ("NI", "dur", "USD", "first", ["NetIncomeLoss", "ProfitLoss"]),
    ("EPS", "dur", "USD-per-shares", "first", ["EarningsPerShareDiluted", "EarningsPerShareBasic"]),
    ("OCF", "dur", "USD", "first",
     ["NetCashProvidedByUsedInOperatingActivities",
      "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"]),
    ("CAPEX", "dur", "USD", "first",
     ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"]),
    ("EQ", "inst", "USD", "first",
     ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"]),
    ("ASSETS", "inst", "USD", "first", ["Assets"]),
    ("SHARES", "dur", "shares", "first",
     ["WeightedAverageNumberOfDilutedSharesOutstanding", "WeightedAverageNumberOfSharesOutstandingBasic"]),
]
# CSVに出す項目（FCF = 営業CF − 設備投資 を追加）
FIN_KEYS = ["REV", "OPI", "NI", "EPS", "OCF", "CAPEX", "FCF", "EQ", "ASSETS", "SHARES"]
YEAR_COUNT = 6

# 組織再編でSECの企業番号（CIK）が変わった会社 { 新CIK: 旧CIK }。新CIKにデータがない年は旧CIKで補う
CIK_ALIAS = {
    2115436: 34088,  # ExxonMobil（2026年に持株会社へ移行）
}

SECTOR_JA = {
    "Information Technology": "情報技術",
    "Health Care": "ヘルスケア",
    "Financials": "金融",
    "Consumer Discretionary": "一般消費財",
    "Communication Services": "通信サービス",
    "Industrials": "資本財",
    "Consumer Staples": "生活必需品",
    "Energy": "エネルギー",
    "Utilities": "公益",
    "Real Estate": "不動産",
    "Materials": "素材",
}


def get(url):
    """GETしてテキストを返す。404は None。SECのルール（1秒10回まで）を守るため毎回少し待つ"""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as res:
                body = res.read().decode("utf-8")
            time.sleep(0.15)
            return body
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if attempt == 2:
                raise
            time.sleep(5)


def fetch_sp500():
    """WikipediaのS&P500一覧から [ティッカー, 会社名, セクター, CIK] を取り出す"""
    page = get("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")
    start = page.index('id="constituents"')
    table = page[start:page.index("</table>", start)]
    companies = []
    for row in re.split(r"<tr[^>]*>", table):
        cells = [html.unescape(re.sub(r"<[^>]+>", "", c)).strip()
                 for c in re.findall(r"<td[^>]*>([\s\S]*?)</td>", row)]
        if len(cells) < 7 or not cells[0] or not cells[6].isdigit():
            continue
        companies.append({"ticker": cells[0], "name": cells[1],
                          "sector": SECTOR_JA.get(cells[2], cells[2]), "cik": int(cells[6])})
    if len(companies) < 450:
        raise RuntimeError(f"S&P500一覧の読み取りに失敗しました（{len(companies)}社）")
    return companies


def fetch_year(year, cik_set):
    """1年分を取得して { 'REV': {cik: 値}, ... } を返す（cik_setに含まれる会社だけ）"""
    out = {}
    for key, typ, unit, pick, tags in SEC_ITEMS:
        period = f"CY{year}Q4I" if typ == "inst" else f"CY{year}"
        merged = {}
        for tag in tags:
            body = get(f"https://data.sec.gov/api/xbrl/frames/us-gaap/{tag}/{unit}/{period}.json")
            if body is None:
                continue
            for d in json.loads(body)["data"]:
                cik, val = d["cik"], d["val"]
                if cik not in cik_set:
                    continue
                if cik not in merged or (pick == "max" and val > merged[cik]):
                    merged[cik] = val
        out[key] = merged
    out["FCF"] = {cik: v - out["CAPEX"][cik] for cik, v in out["OCF"].items() if cik in out["CAPEX"]}
    return out


def target_years(today):
    """直近の決算年度を含む6年分。4月以降は前年、3月までは2年前を最新とする"""
    latest = today.year - (1 if today.month >= 4 else 2)
    return list(range(latest - YEAR_COUNT + 1, latest + 1))


def main():
    companies = fetch_sp500()
    print(f"S&P500: {len(companies)}社")
    ciks = [c["cik"] for c in companies]
    cik_set = set(ciks) | {CIK_ALIAS[c] for c in ciks if c in CIK_ALIAS}
    years = target_years(datetime.date.today())

    data = {}
    for y in years:
        print(f"SEC {y}年分を取得中…")
        data[y] = fetch_year(y, cik_set)

    os.makedirs(OUT_DIR, exist_ok=True)
    header = ["ticker", "name", "sector", "cik"] + [f"{k}_{y}" for y in years for k in FIN_KEYS]
    with open(os.path.join(OUT_DIR, "financials.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        for c in companies:
            row = [c["ticker"], c["name"], c["sector"], c["cik"]]
            for y in years:
                for k in FIN_KEYS:
                    v = data[y][k].get(c["cik"])
                    if v is None and c["cik"] in CIK_ALIAS:
                        v = data[y][k].get(CIK_ALIAS[c["cik"]])
                    row.append("" if v is None else v)
            w.writerow(row)

    meta = {
        "updated": datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime("%Y/%m/%d %H:%M"),
        "years": years,
        "companies": len(companies),
        "source": "U.S. SEC (data.sec.gov frames API), Wikipedia (S&P 500 constituents)",
    }
    with open(os.path.join(OUT_DIR, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print("完了:", meta)


if __name__ == "__main__":
    main()
