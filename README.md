# 主力分點 + 籌碼流向分析系統（Phase 1 MVP）

規則式籌碼分析工具：抓取三大法人／融資融券／借券／股價（＋選配的券商分點）資料，計算籌碼健康度、主力吸籌指數、出貨警報等指標，產出每日 Markdown/CSV 報表，並附回測模組驗證訊號是否真的有效。

**定位**：籌碼面輔助工具，所有分數與燈號都是規則式計算，**未經回測驗證前不構成投資建議**。

## 安裝

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
```

## 設定 FinMind Sponsor（選配，啟用分點資料才需要）

分點進出資料（券商分點買賣超）需要訂閱 [FinMind Sponsor](https://finmindtrade.com/analysis/#/Sponsor/sponsor)（NT$999/月）才能取得。取得 API Token 後：

1. 編輯 `.env`，填入 `FINMIND_TOKEN=你的token`
2. 之後執行 `run_daily.py` 會自動啟用分點連續性、主力成本、集中度、吸籌指數等功能

**沒有訂閱也能跑**：不填 token 時，系統只用免費資料集（三大法人、融資融券、借券、股價），分點相關欄位會在報表中明確標示「未啟用（需訂閱）」，不會顯示假數字，也不會讓程式崩潰。

## 修改追蹤股票清單

編輯 `config/stocks.yaml` 的 `stocks` 清單，換成你想追蹤的 10~30 檔股票代號即可。同檔案也能調整所有指標門檻（連續買超天數、融資維持率警戒線等）。

## 執行

```bash
.venv\Scripts\python run_daily.py
```

會在 `reports/` 產出當天的 `YYYY-MM-DD.md` 與 `.csv`。

## 排程（每日自動執行）

分點與法人資料約在收盤後 20:00~21:00 更新完成，建議排在 21:30 之後執行。用 Windows 工作排程器（工作排程器 → 建立基本工作）指向：

```
"C:\Users\user\Documents\claude\Brokerage commission\.venv\Scripts\python.exe" "C:\Users\user\Documents\claude\Brokerage commission\run_daily.py"
```

## 回測

`src/backtest/backtest.py` 提供一個通用框架：把任何指標轉成一組訊號日期（`set[str]`），丟進 `backtest.run()`，就能得到訊號後 N 日的勝率／平均報酬／最大回撤。範例：

```python
from src.backtest import backtest
signals = backtest.signal_from_institutional_streak(inst_df, min_streak_days=3)
results = backtest.run(price_df, signals, holding_days_list=[3, 5, 10, 20])
```

**在信任任何指標之前，先用這個框架驗證它在你關注的股票上是否真的有統計優勢**——尤其是分點相關的指標，目前的權重（`accumulation_score.py`／`chip_health.py`）都是規則式假設，不是回測校準出來的。

## 專案結構

```
config/stocks.yaml     股票清單與所有可調參數
src/ingest/             資料擷取（FinMind）
src/storage/db.py       SQLite schema 與存取
src/indicators/         各項籌碼指標計算
src/backtest/           訊號回測框架
src/report/render.py    Markdown/CSV 報表輸出
run_daily.py             主入口
```

## 已知限制（Phase 1 範圍）

- 不含 AI 每日文字結論（需 Anthropic API Key，規劃在 Phase 2 加入）
- 主力生命週期／意圖推論等敘述性判斷未實作（屬 Phase 2，且需標註為「輔助觀察」而非精確機率）
- 對敲偵測、分點性格資料庫未實作（現有日彙總資料做不到，需 tick 級資料或長期人工維護，投報比低）
- 融資維持率為簡化估算（用收盤價加權推算融資成本，非真實帳戶維持率）
