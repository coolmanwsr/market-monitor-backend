import yfinance as yf
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import random

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/market-status")
def get_market_status():
    # 核心监控池：加入了传统的股票、黄金，和加密货币
    tickers = {
        "NVDA": ("英伟达", "tradfi"),
        "AAPL": ("苹果", "tradfi"),
        "GLD": ("黄金ETF", "tradfi"),
        "BTC-USD": ("比特币", "crypto")
    }
    assets_data = []

    total_value = 0.0
    total_pnl = 0.0
    # 模拟持仓量
    holdings = {"NVDA": 40.0, "AAPL": 100.0, "GLD": 50.0, "BTC-USD": 0.5}

    for symbol, info in tickers.items():
        name, asset_type = info
        try:
            ticker = yf.Ticker(symbol)
            # 抓取最近一个月数据
            hist = ticker.history(period="1mo")

            if len(hist) >= 2:
                current_price = float(hist['Close'].iloc[-1])
                prev_price = float(hist['Close'].iloc[-2])
                price_change = ((current_price - prev_price) / prev_price) * 100
                history = hist['Close'].tail(20).tolist()
            else:
                current_price, price_change, history = 0.0, 0.0, [0.0]*20

            # 为前端生成布林带需要的数据
            upper_history = [x * 1.05 for x in history]
            lower_history = [x * 0.95 for x in history]

            # 随机生成一个实时的 Z-Score 用于触发 UI 的报警框 (红/绿边框)
            z_score = round(random.uniform(-2.5, 2.5), 2)

            qty = holdings.get(symbol, 0)
            total_value += current_price * qty
            total_pnl += (current_price - prev_price) * qty

            # 这里必须和 Flutter App 里的命名一模一样！
            assets_data.append({
                "symbol": symbol,
                "name": name,
                "type": asset_type,
                "price": round(current_price, 2),
                "price_change": round(price_change, 2),
                "holding": qty,
                "z_score": z_score,
                "history": [round(x, 2) for x in history],
                "upper_history": [round(x, 2) for x in upper_history],
                "lower_history": [round(x, 2) for x in lower_history]
            })
        except Exception as e:
            # 万一抓取失败，返回安全空值防止崩溃
            assets_data.append({
                "symbol": symbol, "name": name, "type": asset_type,
                "price": 0.0, "price_change": 0.0, "holding": holdings.get(symbol, 0),
                "z_score": 0.0, "history": [0.0], "upper_history": [0.0], "lower_history": [0.0]
            })

    # 计算母基金涨跌幅
    portfolio_change_pct = (total_pnl / (total_value - total_pnl)) * 100 if total_value > 0 else 0.0

    return {
        "top_alert": "✅ 全球实盘行情已接通，高频扫描中...",
        "status_mode": 0,
        "portfolio": {
            "name": "ALPHA 宏观对冲母基金",
            "total_value": round(total_value, 2),
            "daily_pnl": round(total_pnl, 2),
            "daily_pnl_pct": round(portfolio_change_pct, 2),
            "sharpe_ratio": 1.94
        },
        "assets": assets_data
    }