# us-stock-screener-data

S&P500構成銘柄の財務データ（年次）を、米国SECの公開データから週1回自動で集めたCSVです。
Googleスプレッドシート「米国株スクリーナー」が読み込んで使います。

| ファイル | 中身 |
|---|---|
| `data/financials.csv` | 1行1社。売上高・営業利益・純利益・EPS・営業CF・設備投資・フリーCF・自己資本・総資産（直近6年分） |
| `data/meta.json` | 更新日時・対象年 |

- 更新：毎週土曜 朝5時（日本時間）に GitHub Actions で自動実行（`fetch_sec.py`）
- データの出どころ：U.S. Securities and Exchange Commission（data.sec.gov）、Wikipedia「List of S&P 500 companies」
- 本データは情報提供のみを目的としており、投資助言ではありません。正確性は保証されません。
