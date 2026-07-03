"""Render the daily chip-flow report as Markdown + CSV."""
from __future__ import annotations

import csv
from pathlib import Path

REPORTS_DIR = Path(__file__).resolve().parents[2] / "reports"

DISCLAIMER = (
    "本報表為籌碼面輔助工具，訊號基於規則式指標計算，**未經歷史回測驗證前不構成投資建議**。"
    "分點資料需訂閱 FinMind Sponsor 才會顯示；缺分點資料時，籌碼健康度與吸籌指數僅反映法人／融資／借券部分。"
)


ACTION_LABELS = {
    "STOP_LOSS": "🔴 停損",
    "TAKE_PROFIT": "🟡 停利",
    "BUY": "🟢 買進",
    "HOLD": "－ 觀望",
}

_HOLDER_TREND_LABELS = {
    "increasing": "↑ 大戶持股上升",
    "decreasing": "↓ 大戶持股下降",
    "flat": "→ 持平",
    "unknown": "資料不足以判斷趨勢",
}

# CSV output: Chinese header labels and value translations, keyed by the same
# internal field names used throughout the codebase (those stay English —
# only what the user reads in Excel/Notepad gets translated).
_CSV_FIELD_LABELS = {
    "stock_id": "股票代號",
    "name": "股票名稱",
    "close": "收盤價",
    "light_label": "多空燈號",
    "chip_health_score": "籌碼健康度",
    "accumulation_score": "主力吸籌指數",
    "action": "訊號",
    "credibility_grade": "訊號可信度",
    "entry_conditions_met": "進場條件符合數",
    "entry_conditions_total": "進場條件總數",
    "margin_maintenance_ratio_pct": "融資維持率(%)",
    "margin_risk_level": "融資風險等級",
    "vp_pattern": "量價型態",
    "false_breakout_risk": "假突破風險",
    "foreign_net": "外資買賣超(張)",
    "trust_net": "投信買賣超(張)",
    "government_bank_net": "八大行庫買賣超(股)",
    "major_holder_pct": "大戶持股比例(%)",
    "major_holder_trend": "大戶持股趨勢",
    "broker_available": "已啟用分點資料",
    "broker_cost": "主力成本(元)",
    "broker_pnl_pct": "主力損益(%)",
    "sell_alert": "出貨警報",
}

_CSV_ACTION_LABELS = {"STOP_LOSS": "停損", "TAKE_PROFIT": "停利", "BUY": "買進", "HOLD": "觀望"}
_CSV_RISK_LEVEL_LABELS = {"safe": "安全", "warning": "警戒", "danger": "危險"}
_CSV_TREND_LABELS = {"increasing": "上升", "decreasing": "下降", "flat": "持平", "unknown": "資料不足"}
_CSV_BOOL_LABELS = {True: "是", False: "否"}


def _final_action(r: dict) -> str:
    """Exit signals take priority over entry signals: protecting an existing
    position matters more than a fresh entry read on the same day."""
    if r["exit_signal"]["action"] in ("STOP_LOSS", "TAKE_PROFIT"):
        return r["exit_signal"]["action"]
    return r["entry_signal"]["action"]


def _stock_section(r: dict) -> str:
    broker_note = "" if r["broker_available"] else "（⚠️ 未啟用分點資料，需訂閱 FinMind Sponsor）"
    cost_line = (
        f"主力成本 {r['broker_cost']['cost']} 元，{'獲利' if r['broker_cost']['pnl_pct'] and r['broker_cost']['pnl_pct'] > 0 else '套牢'} "
        f"{r['broker_cost']['pnl_pct']}%"
        if r["broker_available"] and r["broker_cost"].get("cost") is not None
        else "主力成本：無資料" + broker_note
    )

    action = _final_action(r)
    entry = r["entry_signal"]
    exit_ = r["exit_signal"]
    grade = r["credibility"]["grade"]

    lines = [
        f"## {r['stock_id']} {r['name']}",
        "",
        f"**收盤價**：{r['close']} 　**多空燈號**：{r['light']['light']}（{r['light']['label']}）"
        f"　**訊號**：{ACTION_LABELS[action]}（可信度：{grade}）",
        "",
        f"- 訊號可信度：**{grade}** — {r['credibility']['reason']}",
        f"- 籌碼健康度：{r['chip_health']['score']} / 100（{r['chip_health']['label']}）",
        f"- 主力吸籌指數：{r['accumulation_score']['score']} / 100" + broker_note,
        f"- {cost_line}",
        f"- 融資維持率：{r['margin_risk'].get('maintenance_ratio_pct', 'N/A')}%（{r['margin_risk'].get('risk_level', 'N/A')}）",
        f"- 量價型態：{r['volume_price'].get('vp_pattern', 'N/A')}"
        + ("　⚠️ 假突破風險" if r["volume_price"].get("false_breakout_risk") else ""),
        f"- 外資／投信買賣超：{r['foreign_net']:+,} / {r['trust_net']:+,} 張",
        f"- 八大行庫買賣超：{r['government_bank_net']:+,} 股",
        (
            f"- 大戶持股（400張以上）：{r['major_holder_pct']}%（{_HOLDER_TREND_LABELS.get(r['major_holder_trend'], '')}）"
            if r["major_holder_pct"] is not None
            else "- 大戶持股：無資料（需累積至少2週的股東分級資料）"
        ),
        f"- 進場條件：{len(entry['conditions_met'])}/{entry['conditions_total']} 項符合"
        + (f"（{'、'.join(entry['conditions_met'])}）" if entry["conditions_met"] else "")
        + (f"　未評估：{'、'.join(entry['conditions_unavailable'])}" if entry["conditions_unavailable"] else ""),
    ]

    if exit_["stop_loss_reasons"]:
        lines.append(f"- 🔴 停損觸發：{'、'.join(exit_['stop_loss_reasons'])}")
    if exit_["take_profit_reasons"]:
        lines.append(f"- 🟡 停利觸發：{'、'.join(exit_['take_profit_reasons'])}")

    if r["sell_alert"]["alert"]:
        lines.append(f"- {r['sell_alert']['message']}（觸發：{'、'.join(r['sell_alert']['triggered_conditions'])}）")

    lines.append("")
    return "\n".join(lines)


def render_markdown(results: list[dict], run_date: str) -> str:
    overview_rows = "\n".join(
        f"| {r['stock_id']} {r['name']} | {r['close']} | {r['light']['light']} | "
        f"{r['chip_health']['score']} | {ACTION_LABELS[_final_action(r)]} | {r['credibility']['grade']} | "
        f"{'⚠️' if r['sell_alert']['alert'] else '-'} |"
        for r in results
    )

    header = f"""# 籌碼流向日報 — {run_date}

> {DISCLAIMER}
>
> 「訊號可信度」是根據 `run_backtest.py` 的歷史回測結果評定（A~D，N/A 代表尚未回測），不是憑感覺打分數。
> 沒有 A/B 級佐證的訊號請勿直接當作進出場依據。

## 總覽

| 股票 | 收盤價 | 燈號 | 籌碼健康度 | 訊號 | 可信度 | 出貨警報 |
|---|---|---|---|---|---|---|
{overview_rows}

---

"""
    body = "\n".join(_stock_section(r) for r in results)
    return header + body


def render_csv(results: list[dict], path: Path) -> None:
    fieldnames = list(_CSV_FIELD_LABELS.keys())
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writerow(_CSV_FIELD_LABELS)  # Chinese header row instead of the internal English keys
        for r in results:
            false_breakout = r["volume_price"].get("false_breakout_risk")
            broker_available = r["broker_available"]
            sell_alert = r["sell_alert"]["alert"]
            writer.writerow({
                "stock_id": r["stock_id"],
                "name": r["name"],
                "close": r["close"],
                "light_label": r["light"]["label"],
                "chip_health_score": r["chip_health"]["score"],
                "accumulation_score": r["accumulation_score"]["score"],
                "action": _CSV_ACTION_LABELS.get(_final_action(r), _final_action(r)),
                "credibility_grade": r["credibility"]["grade"],
                "entry_conditions_met": len(r["entry_signal"]["conditions_met"]),
                "entry_conditions_total": r["entry_signal"]["conditions_total"],
                "margin_maintenance_ratio_pct": r["margin_risk"].get("maintenance_ratio_pct"),
                "margin_risk_level": _CSV_RISK_LEVEL_LABELS.get(
                    r["margin_risk"].get("risk_level"), r["margin_risk"].get("risk_level")
                ),
                "vp_pattern": r["volume_price"].get("vp_pattern"),
                "false_breakout_risk": _CSV_BOOL_LABELS.get(false_breakout, false_breakout),
                "foreign_net": r["foreign_net"],
                "trust_net": r["trust_net"],
                "government_bank_net": r["government_bank_net"],
                "major_holder_pct": r["major_holder_pct"],
                "major_holder_trend": _CSV_TREND_LABELS.get(r["major_holder_trend"], r["major_holder_trend"]),
                "broker_available": _CSV_BOOL_LABELS.get(broker_available, broker_available),
                "broker_cost": r["broker_cost"].get("cost"),
                "broker_pnl_pct": r["broker_cost"].get("pnl_pct"),
                "sell_alert": _CSV_BOOL_LABELS.get(sell_alert, sell_alert),
            })


def save_report(results: list[dict], run_date: str) -> dict:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    md_path = REPORTS_DIR / f"{run_date}.md"
    csv_path = REPORTS_DIR / f"{run_date}.csv"

    md_path.write_text(render_markdown(results, run_date), encoding="utf-8")
    render_csv(results, csv_path)

    return {"markdown": md_path, "csv": csv_path}
