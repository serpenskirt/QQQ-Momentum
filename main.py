import os
import argparse
import requests
import pandas as pd
from datetime import datetime
import pytz

# --- Configuration ---
SYMBOL = 'QQQ'
TRADIER_URL = "https://api.tradier.com/v1"
TRADIER_TOKEN = os.environ.get("TRADIER_TOKEN")
OA_WEBHOOK_BUY = os.environ.get("OA_WEBHOOK_BUY")
OA_WEBHOOK_SELL = os.environ.get("OA_WEBHOOK_SELL")

# Set Timezone to US/Eastern
EST = pytz.timezone('US/Eastern')

def get_headers():
    return {
        "Authorization": f"Bearer {TRADIER_TOKEN}",
        "Accept": "application/json"
    }

def is_market_open():
    """Checks if current time is within 9:30 AM - 4:00 PM ET on a weekday."""
    now = datetime.now(EST)
    
    # Check Weekend (Mon=0, Sun=6)
    if now.weekday() > 4:
        print("Market Closed (Weekend).")
        return False

    # Check Hours (09:30 to 16:00)
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
    
    if market_open <= now <= market_close:
        return True
    
    print("Market Closed (Outside Hours).")
    return False

def get_market_data():
    """Fetches real-time quote (including prev close) and historical daily data for SMA."""
    # 1. Get Real-time Quote
    quote_resp = requests.get(
        f"{TRADIER_URL}/markets/quotes",
        params={'symbols': SYMBOL},
        headers=get_headers()
    )
    quote_resp.raise_for_status()
    quote_data = quote_resp.json()['quotes']['quote']
    
    current_price = quote_data['last']
    open_price = quote_data['open']
    prev_close = quote_data['prevclose']  # Fetches yesterday's closing price
    
    # 2. Get Historical Data (Last 300 Days to be safe for SMA200)
    history_resp = requests.get(
        f"{TRADIER_URL}/markets/history",
        params={
            'symbol': SYMBOL,
            'interval': 'daily',
            'start': '2023-01-01' # In production, this ensures enough data for 200 SMA
        },
        headers=get_headers()
    )
    history_resp.raise_for_status()
    history = history_resp.json()['history']['day']
    
    # Create DataFrame
    df = pd.DataFrame(history)
    
    # Calculate SMA 200 using the last 200 closes
    sma_200 = df['close'].tail(200).mean()
    
    return current_price, open_price, prev_close, sma_200

def trigger_webhook(url, signal_type, price, sma, open_p=None):
    """Sends payload to Option Alpha."""
    payload = {
        "ticker": SYMBOL,
        "signal": signal_type,
        "price": price,
        "sma200": sma,
        "timestamp": datetime.now(EST).isoformat()
    }
    if open_p:
        payload["open_price"] = open_p

    print(f"Sending Payload: {payload}")
    
    try:
        r = requests.post(url, json=payload)
        r.raise_for_status()
        print(f"✅ Webhook Sent ({signal_type}): {r.status_code}")
    except Exception as e:
        print(f"❌ Failed to send webhook: {e}")

def run_strategy(mode):
    # Skip execution if market is closed (save API calls)
    if not is_market_open():
        return

    print(f"--- Running {mode.upper()} Logic for {SYMBOL} ---")
    
    try:
        price, open_price, prev_close, sma200 = get_market_data()
    except Exception as e:
        print(f"Error fetching data: {e}")
        return

    print(f"Price: {price} | Open: {open_price} | Prev Close: {prev_close} | SMA200: {sma200:.2f}")

    # --- BUY LOGIC ---
    if mode == 'buy':
        # Criteria 1: Trend Filter (Price must be 4% above SMA200)
        threshold_sma = sma200 * 1.04
        
        # Criteria 2: Dip Filter (Price must be 1% below PREVIOUS CLOSE)
        # Using Prev Close captures overnight gap downs
        threshold_dip = prev_close * 0.99
        
        print(f"Buy Criteria: Price >= {threshold_sma:.2f} (SMA+4%) AND Price <= {threshold_dip:.2f} (Close-1%)")
        
        if (price >= threshold_sma) and (price <= threshold_dip):
            print(">>> BUY SIGNAL TRIGGERED <<<")
            trigger_webhook(OA_WEBHOOK_BUY, "BUY", price, sma200, open_price)
        else:
            print("No Buy Signal.")

    # --- SELL LOGIC ---
    elif mode == 'sell':
        # Criteria: Price < 0.97 * SMA200 (Stop Loss / Exit Condition)
        threshold_sell = sma200 * 0.97
        print(f"Sell Criteria: Price < {threshold_sell:.2f}")
        
        if price < threshold_sell:
            print(">>> SELL SIGNAL TRIGGERED <<<")
            trigger_webhook(OA_WEBHOOK_SELL, "SELL", price, sma200)
        else:
            print("No Sell Signal.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['buy', 'sell'], required=True)
    args = parser.parse_args()
    
    run_strategy(args.mode)