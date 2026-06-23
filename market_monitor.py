import os
import time
import math
import random
import requests
import pandas as pd
import numpy as np
import yfinance as yf
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime

app = FastAPI(title="Alpha Quant 工业级对冲雷达 (移动端局域网版)")

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

# ==========================================
# 💡 确保你的代理软件开着，并且端口是 7890
PROXY_PORT = 7890
LOCAL_PROXY = f"http://127.0.0.1:{PROXY_PORT}"
PROXIES_DICT = {"http": LOCAL_PROXY, "https": LOCAL_PROXY}
# ==========================================

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

ASSETS_CRYPTO = [{"symbol": "BTCUSDT", "name": "比特币 (BTC)"}, {"symbol": "ETHUSDT", "name": "以太坊 (ETH)"}]
ASSETS_TRADFI = [{"symbol": "NVDA", "name": "英伟达 (NVDA)"}, {"symbol": "AAPL", "name": "苹果 (AAPL)"}, {"symbol": "GLD", "name": "黄金ETF (GLD)"}]

# 活的投资组合字典
PORTFOLIO_HOLDINGS = {
    "BTCUSDT": 0.15, "ETHUSDT": 2.5, "NVDA": 40.0, "AAPL": 100.0, "GLD": 50.0
}

CACHE = {"data": None, "last_fetch_time": 0}
CACHE_TTL = 5
CRYPTO_MEMORY_CACHE = {}
DEPTH_MEMORY_CACHE = {}

BINANCE_NODES = ["https://api.binance.com", "https://api1.binance.com", "https://api2.binance.com", "https://api3.binance.com"]

class TradeRequest(BaseModel):
    symbol: str
    action: str
    amount: float

def fetch_crypto_series(symbol):
    nodes = list(BINANCE_NODES)
    random.shuffle(nodes)
    for node in nodes:
        try:
            res = requests.get(f"{node}/api/v3/klines?symbol={symbol}&interval=1d&limit=100", proxies=PROXIES_DICT, timeout=4)
            if res.status_code == 200:
                data = res.json()
                dates = [pd.to_datetime(day[0], unit='ms').normalize() for day in data]
                closes = [float(day[4]) for day in data]
                series = pd.Series(closes, index=dates, name=symbol)
                CRYPTO_MEMORY_CACHE[symbol] = series
                return series
        except Exception: continue
    if symbol in CRYPTO_MEMORY_CACHE: return CRYPTO_MEMORY_CACHE[symbol]
    raise ValueError("节点连接失败且无缓存")

def fetch_tradfi_series_with_cache(symbol):
    csv_path = os.path.join(DATA_DIR, f"{symbol}.csv")
    today_str = datetime.now().strftime("%Y-%m-%d")
    if os.path.exists(csv_path):
        if datetime.fromtimestamp(os.path.getmtime(csv_path)).strftime("%Y-%m-%d") == today_str:
            return pd.read_csv(csv_path, index_col=0, parse_dates=True)[symbol]
    try:
        time.sleep(random.uniform(1.0, 2.0))
        df = yf.Ticker(symbol).history(period="100d", proxy=LOCAL_PROXY)
        if df.empty: raise ValueError("数据为空")
        df.index = df.index.tz_localize(None).normalize()
        series = df['Close'].rename(symbol)
        series.to_csv(csv_path)
        return series
    except Exception as e:
        if os.path.exists(csv_path): return pd.read_csv(csv_path, index_col=0, parse_dates=True)[symbol]
        raise e

@app.post("/api/trade")
def execute_trade(trade: TradeRequest):
    global PORTFOLIO_HOLDINGS, CACHE
    if trade.symbol not in PORTFOLIO_HOLDINGS:
        PORTFOLIO_HOLDINGS[trade.symbol] = 0.0

    if trade.action == "BUY":
        PORTFOLIO_HOLDINGS[trade.symbol] += trade.amount
    elif trade.action == "SELL":
        PORTFOLIO_HOLDINGS[trade.symbol] -= trade.amount
        if PORTFOLIO_HOLDINGS[trade.symbol] < 0:
            PORTFOLIO_HOLDINGS[trade.symbol] = 0.0

    CACHE["data"] = None
    return {"status": "success", "new_holding": PORTFOLIO_HOLDINGS[trade.symbol]}

@app.get("/api/market-status")
def get_market_status():
    global CACHE_TTL
    current_time = time.time()
    if current_time - CACHE["last_fetch_time"] < CACHE_TTL and CACHE["data"] is not None: return CACHE["data"]

    series_list = []
    asset_info = {}
    for a in ASSETS_CRYPTO:
        try:
            series_list.append(fetch_crypto_series(a["symbol"]))
            asset_info[a["symbol"]] = {"name": a["name"], "type": "crypto"}
        except Exception: pass
    for a in ASSETS_TRADFI:
        try:
            series_list.append(fetch_tradfi_series_with_cache(a["symbol"]))
            asset_info[a["symbol"]] = {"name": a["name"], "type": "tradfi"}
        except Exception: pass

    if not series_list: return {"assets": [], "portfolio": None, "top_alert": "⚠️ 全球数据流连通异常", "status_mode": 1}

    master_df = pd.concat(series_list, axis=1).sort_index().ffill().dropna()
    results = []

    portfolio_df = master_df.copy()
    for col in portfolio_df.columns:
        portfolio_df[col] = portfolio_df[col] * PORTFOLIO_HOLDINGS.get(col, 0)

    portfolio_total_series = portfolio_df.sum(axis=1)
    port_latest = float(portfolio_total_series.iloc[-1])
    port_prev = float(portfolio_total_series.iloc[-2])
    port_pnl = port_latest - port_prev
    port_pnl_pct = (port_pnl / port_prev) * 100 if port_prev != 0 else 0

    daily_returns = portfolio_total_series.pct_change().dropna()
    sharpe_ratio = (daily_returns.mean() / daily_returns.std()) * math.sqrt(252) if daily_returns.std() != 0 else 0

    portfolio_data = {
        "total_value": round(port_latest, 2),
        "daily_pnl": round(port_pnl, 2),
        "daily_pnl_pct": round(port_pnl_pct, 2),
        "sharpe_ratio": round(sharpe_ratio, 2)
    }

    extreme_alerts = []

    for symbol in master_df.columns:
        col_data = master_df[symbol]
        info = asset_info[symbol]
        ma20 = col_data.rolling(window=20).mean()
        std20 = col_data.rolling(window=20).std()
        upper = ma20 + 2 * std20
        lower = ma20 - 2 * std20
        tail_data = col_data.tail(20)
        z_score = (float(tail_data.iloc[-1]) - float(ma20.iloc[-1])) / float(std20.iloc[-1]) if float(std20.iloc[-1]) != 0 else 0.0
        price_change_pct = ((float(tail_data.iloc[-1]) - float(tail_data.iloc[-2])) / float(tail_data.iloc[-2])) * 100

        holding_amount = PORTFOLIO_HOLDINGS.get(symbol, 0.0)

        if z_score <= -2.0: extreme_alerts.append(f"{symbol} 严重超卖")
        elif z_score >= 2.0: extreme_alerts.append(f"{symbol} 严重超买")

        results.append({
            "name": info["name"], "symbol": symbol, "type": info["type"],
            "price": round(float(tail_data.iloc[-1]), 2), "price_change": round(price_change_pct, 2), "z_score": round(z_score, 2),
            "holding": holding_amount,
            "history": [round(float(p), 2) for p in tail_data],
            "upper_history": [round(float(p), 2) for p in upper.tail(20)], "lower_history": [round(float(p), 2) for p in lower.tail(20)]
        })

    top_msg = "🌐 雷达运转正常，跨市场基金面板已上线..."
    mode = 0
    if len(extreme_alerts) > 0:
        top_msg = "🚨 雷达捕捉到极端信号: " + " | ".join(extreme_alerts)
        mode = 1

    CACHE["last_fetch_time"] = current_time
    CACHE["data"] = {"assets": results, "portfolio": portfolio_data, "top_alert": top_msg, "status_mode": mode}
    return CACHE["data"]

@app.get("/api/depth/{symbol}")
def get_order_book_depth(symbol: str):
    if symbol not in [a["symbol"] for a in ASSETS_CRYPTO]: return {"error": "TradFi no depth"}
    nodes = list(BINANCE_NODES)
    random.shuffle(nodes)
    for node in nodes:
        try:
            res = requests.get(f"{node}/api/v3/depth?symbol={symbol}&limit=50", proxies=PROXIES_DICT, timeout=3)
            if res.status_code == 200:
                data = res.json()
                bids = [[float(p), float(q)] for p, q in data.get("bids", [])]
                cum_bids = []; total_bid = 0
                for p, q in bids: total_bid += q; cum_bids.append([p, total_bid])
                cum_bids.reverse()
                asks = [[float(p), float(q)] for p, q in data.get("asks", [])]
                cum_asks = []; total_ask = 0
                for p, q in asks: total_ask += q; cum_asks.append([p, total_ask])
                result = {"symbol": symbol, "bids": cum_bids, "asks": cum_asks}
                DEPTH_MEMORY_CACHE[symbol] = result
                return result
        except Exception: continue
    if symbol in DEPTH_MEMORY_CACHE: return DEPTH_MEMORY_CACHE[symbol]
    return {"error": "Network Unstable"}

@app.get("/api/backtest/{symbol}")
def run_strategy_backtest(symbol: str):
    try:
        if symbol in [a["symbol"] for a in ASSETS_CRYPTO]: series = fetch_crypto_series(symbol)
        else: series = fetch_tradfi_series_with_cache(symbol)
        df = pd.DataFrame({'Close': series})
        df['MA20'] = df['Close'].rolling(window=20).mean()
        df['STD20'] = df['Close'].rolling(window=20).std()
        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
        loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))
        df = df.dropna()

        capital = 10000.0; position = 0.0; entry_price = 0.0
        trades = 0; winning_trades = 0; stop_loss_count = 0
        peak_equity = 10000.0; max_drawdown = 0.0; equity_curve = []

        for i in range(len(df)):
            price = float(df['Close'].iloc[i])
            z = (price - float(df['MA20'].iloc[i])) / float(df['STD20'].iloc[i]) if float(df['STD20'].iloc[i]) > 0 else 0
            rsi = float(df['RSI'].iloc[i])
            if position > 0:
                if price <= entry_price * 0.95:
                    capital = position * price; trades += 1; stop_loss_count += 1; position = 0.0
                elif z >= 0.0:
                    capital = position * price
                    if price > entry_price: winning_trades += 1
                    trades += 1; position = 0.0
            else:
                if z <= -2.0 and rsi < 40:
                    position = capital / price; capital = 0.0; entry_price = price
            current_equity = capital if position == 0 else position * price
            equity_curve.append(round(current_equity, 2))
            if current_equity > peak_equity: peak_equity = current_equity
            dd = (peak_equity - current_equity) / peak_equity * 100
            if dd > max_drawdown: max_drawdown = dd

        final_capital = equity_curve[-1] if equity_curve else capital
        roi = ((final_capital - 10000) / 10000) * 100
        win_rate = (winning_trades / trades * 100) if trades > 0 else 0.0
        return {
            "symbol": symbol, "final_capital": round(final_capital, 2), "roi": round(roi, 2), "trades": trades,
            "win_rate": round(win_rate, 2), "stop_loss_count": stop_loss_count, "max_drawdown": round(max_drawdown, 2), "equity_curve": equity_curve[-30:]
        }
    except Exception as e: return {"error": str(e)}

if __name__ == "__main__":
    import uvicorn
    # 💡 核心破壁：0.0.0.0 允许手机连接
    uvicorn.run(app, host="0.0.0.0", port=8000)