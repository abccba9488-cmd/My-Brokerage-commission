# 主力分點 + 籌碼流向分析系統（Phase 1 MVP）

規則式籌碼分析工具：抓取三大法人／融資融券／借券／股價（免費），以及券商分點進出／股東持股分級／外資持股比例／八大行庫買賣（需 FinMind Sponsor）資料，計算籌碼健康度、主力吸籌指數、出貨警報等指標，產出每日 Markdown/CSV 報表，並附回測模組驗證訊號是否真的有效。

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

已建立 Windows 工作排程器任務 `BrokerageCommission-DailyReport`，**每天 21:30** 執行（分點與法人資料約在收盤後 20:00~21:00 更新完成）。`StartWhenAvailable` 已開啟：電腦當時關機的話，下次開機會自動補跑一次（但不會回溯補齊中間錯過的每一天，只補最新一次）。

排程實際呼叫的是 `run_daily_checked.py`（檢查＋重試外殼），不是直接呼叫 `run_daily.py`：

```
"C:\Users\user\Documents\claude\Brokerage commission\.venv\Scripts\python.exe" "C:\Users\user\Documents\claude\Brokerage commission\run_daily_checked.py"
```

`run_daily_checked.py` 會在跑完後檢查當天的 CSV 報表是否包含 `config/stocks.yaml` 裡的每一檔股票；缺任何一檔就自動重跑，最多重試 3 次（間隔 5 分鐘），全部失敗才放棄，並寫一個 `reports/FAILED_YYYY-MM-DD.txt` 讓你一眼看出昨晚沒跑成功。過程記錄在 `reports/run_daily_checked.log`。

管理排程：
```powershell
Get-ScheduledTask -TaskName "BrokerageCommission-DailyReport"     # 查看狀態
Start-ScheduledTask -TaskName "BrokerageCommission-DailyReport"   # 立刻手動觸發一次
Unregister-ScheduledTask -TaskName "BrokerageCommission-DailyReport" -Confirm:$false   # 刪除排程
```

## 回測

`src/backtest/backtest.py` 提供一個通用框架：把任何指標轉成一組訊號日期（`set[str]`），丟進 `backtest.run()`，就能得到訊號後 N 日的勝率／平均報酬／最大回撤。範例：

```python
from src.backtest import backtest
signals = backtest.signal_from_institutional_streak(inst_df, min_streak_days=3)
results = backtest.run(price_df, signals, holding_days_list=[3, 5, 10, 20])
```

**在信任任何指標之前，先用這個框架驗證它在你關注的股票上是否真的有統計優勢**——尤其是分點相關的指標，目前的權重（`accumulation_score.py`／`chip_health.py`）都是規則式假設，不是回測校準出來的。

### 分點連續買超訊號：實測結果（2026-07-03，近1年，5檔股票）

跑 `python run_backtest.py --days 365` 對「分點連續買超」訊號做了完整回測，重點結論：

- **單看訊號本身**看起來不錯（20日後勝率61%），但加上「每天都進場」的基準線對照後，訊號真正贏過基準線的部分只有 1~2 個百分點——大部分表現其實是這一年股價整體上漲的順風車，不是訊號本身的功勞。
- **5 檔股票表現差異很大**：鴻海（2317）有明確、一致的優勢；台積電／聯發科／長榮優勢薄弱或只在長天期出現；玉山金（2884）完全沒有優勢，訊號比亂猜還差。
- 因此新增了**訊號可信度分級**（見下）——不要對所有股票的訊號一視同仁。

完整報表在 `reports/backtest_YYYY-MM-DD.md`，跑一次會重新產生。

### 訊號可信度分級（A/B/C/D）

`run_backtest.py` 跑完後，會用「10日／20日相對基準線的優勢＋粗估交易成本門檻」自動評級（邏輯見 `src/backtest/credibility.py`），寫入 `config/signal_credibility.yaml`：

- **A**：10日與20日的勝率、報酬都優於基準線，且報酬扣掉約 0.6% 交易成本估算後仍有空間
- **B**：方向正確但優勢較小，或報酬接近交易成本門檻
- **C**：優勢不一致或接近 0，看不出訊號比隨機進場更好
- **D**：訊號比基準線差，不建議依此訊號進出場
- **N/A**：樣本數不足或尚未回測

`run_daily.py` 會自動讀取這個檔案，把可信度標示在每天報表的每一檔股票旁邊（包含停損訊號，因為停損用的也是同一套分點成本估算）。**沒有 A/B 級佐證的訊號不要直接當作進出場依據**。這個檔案是自動產生的，不要手動改——想更新評級就重跑一次 `run_backtest.py`。

## 專案結構

```
config/stocks.yaml               股票清單與所有可調參數
config/signal_credibility.yaml   訊號可信度分級（自動產生，勿手動改）
src/ingest/                       資料擷取（FinMind）
src/storage/db.py                 SQLite schema 與存取
src/indicators/                   各項籌碼指標計算
src/backtest/                     訊號回測框架＋可信度評級邏輯
src/report/render.py              Markdown/CSV 報表輸出
run_daily.py                       每日報表主入口
run_backtest.py                    回測與可信度評級主入口
```

## 已知限制（Phase 1 範圍）

- 不含 AI 每日文字結論（需 Anthropic API Key，規劃在 Phase 2 加入）
- 主力生命週期／意圖推論等敘述性判斷未實作（屬 Phase 2，且需標註為「輔助觀察」而非精確機率）
- 對敲偵測、分點性格資料庫未實作（現有日彙總資料做不到，需 tick 級資料或長期人工維護，投報比低）
- 融資維持率為簡化估算（用收盤價加權推算融資成本，非真實帳戶維持率）
