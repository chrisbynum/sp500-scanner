import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os

def get_sp500_tickers():
    """Get list of S&P 500 tickers"""
    # Using a Wikipedia table to get S&P 500 tickers
    url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
    tables = pd.read_html(url)
    sp500_table = tables[0]
    tickers = sp500_table['Symbol'].tolist()
    # Clean up tickers (remove dots, etc.)
    tickers = [ticker.replace('.', '-') for ticker in tickers]
    return tickers

def check_bullish_engulfing(ticker):
    """
    Check if a ticker shows bullish engulfing pattern with 3+ red days prior
    Returns signal details if pattern found, None otherwise
    """
    try:
        # Get 60 days of data to have enough history
        stock = yf.Ticker(ticker)
        df = stock.history(period='60d')
        
        if len(df) < 25:  # Need enough data
            return None
        
        # Calculate 20-day and 50-day moving averages
        df['MA_20'] = df['Close'].rolling(window=20).mean()
        df['MA_50'] = df['Close'].rolling(window=50).mean()
        
        # Calculate Average True Range (ATR) for volatility
        df['H-L'] = df['High'] - df['Low']
        df['H-PC'] = abs(df['High'] - df['Close'].shift(1))
        df['L-PC'] = abs(df['Low'] - df['Close'].shift(1))
        df['TR'] = df[['H-L', 'H-PC', 'L-PC']].max(axis=1)
        df['ATR'] = df['TR'].rolling(window=14).mean()
        
        # Calculate average volume (20-day)
        df['Avg_Volume'] = df['Volume'].rolling(window=20).mean()
        
        # Look at the last 5 days (need 3+ red, then engulfing)
        recent = df.tail(5)
        
        if len(recent) < 5:
            return None
        
        # Check last 4 days for red candles (close < open)
        days_before_last = recent.iloc[:-1]
        red_days = (days_before_last['Close'] < days_before_last['Open']).sum()
        
        if red_days < 3:
            return None
        
        # Check if last day is bullish engulfing
        last_day = recent.iloc[-1]
        prev_day = recent.iloc[-2]
        
        # Bullish engulfing conditions:
        # 1. Opens below previous close
        # 2. Closes above previous open
        # 3. Body engulfs previous candle
        is_bullish_engulfing = (
            last_day['Open'] < prev_day['Close'] and
            last_day['Close'] > prev_day['Open'] and
            last_day['Close'] > last_day['Open']  # Confirm it's a green candle
        )
        
        if not is_bullish_engulfing:
            return None
        
        # Calculate signal metrics
        body_size_pct = ((last_day['Close'] - last_day['Open']) / last_day['Open']) * 100
        volume_ratio = last_day['Volume'] / last_day['Avg_Volume']
        
        # Check if minimum criteria met
        if body_size_pct < 1.0 or volume_ratio < 1.2:
            return None
        
        # Distance from 50-day MA
        current_price = last_day['Close']
        ma_50 = last_day['MA_50']
        distance_from_ma = ((current_price - ma_50) / ma_50) * 100 if pd.notna(ma_50) else -999
        
        # Calculate rating (1-5 stars)
        rating = calculate_rating(body_size_pct, volume_ratio, distance_from_ma)
        
        # Calculate targets and stops
        atr = last_day['ATR']
        entry_price = current_price
        target_price = entry_price + (2.5 * atr)  # 2.5x ATR target
        stop_loss = last_day['Low'] - (1.5 * atr)  # Below engulfing low with buffer
        
        # Calculate weekly resistance (approximate using recent highs)
        weekly_high = df.tail(20)['High'].max()
        
        # Get option expiration suggestions (2-4 weeks out)
        today = datetime.now()
        exp_dates = get_option_expirations(today)
        
        # Suggest strike prices
        strikes = suggest_strikes(current_price, target_price)
        
        return {
            'ticker': ticker,
            'rating': rating,
            'current_price': round(current_price, 2),
            'entry_price': round(entry_price, 2),
            'target_price': round(target_price, 2),
            'stop_loss': round(stop_loss, 2),
            'weekly_resistance': round(weekly_high, 2),
            'body_size_pct': round(body_size_pct, 2),
            'volume_ratio': round(volume_ratio, 2),
            'distance_from_ma50': round(distance_from_ma, 1),
            'atr': round(atr, 2),
            'red_days_before': red_days,
            'exp_dates': exp_dates,
            'suggested_strikes': strikes,
            'date': last_day.name.strftime('%Y-%m-%d')
        }
        
    except Exception as e:
        print(f"Error processing {ticker}: {str(e)}")
        return None

def calculate_rating(body_size_pct, volume_ratio, distance_from_ma):
    """Calculate 1-5 star rating based on signal strength"""
    score = 0
    
    # Volume scoring (max 2 points)
    if volume_ratio >= 1.5:
        score += 2
    elif volume_ratio >= 1.3:
        score += 1.5
    elif volume_ratio >= 1.2:
        score += 1
    
    # Body size scoring (max 2 points)
    if body_size_pct >= 3.0:
        score += 2
    elif body_size_pct >= 2.0:
        score += 1.5
    elif body_size_pct >= 1.5:
        score += 1
    elif body_size_pct >= 1.0:
        score += 0.5
    
    # Distance from MA50 scoring (max 1 point)
    if distance_from_ma > -10:
        score += 1
    elif distance_from_ma > -15:
        score += 0.7
    elif distance_from_ma > -20:
        score += 0.4
    
    # Convert to 1-5 scale
    if score >= 4.5:
        return 5
    elif score >= 3.5:
        return 4
    elif score >= 2.5:
        return 3
    elif score >= 1.5:
        return 2
    else:
        return 1

def get_option_expirations(today):
    """Generate suggested option expiration dates (2-4 weeks out)"""
    expirations = []
    
    # Find next 3 Fridays (typical expiration day)
    days_ahead = 0
    while len(expirations) < 4:
        days_ahead += 1
        future_date = today + timedelta(days=days_ahead)
        
        # Check if it's a Friday and within 2-4 week range
        if future_date.weekday() == 4:  # Friday
            days_diff = (future_date - today).days
            if 10 <= days_diff <= 35:
                expirations.append(future_date.strftime('%Y-%m-%d'))
    
    return expirations[:3]  # Return top 3

def suggest_strikes(current_price, target_price):
    """Suggest option strike prices"""
    # Round to nearest $2.50 or $5 depending on price
    if current_price < 50:
        increment = 2.5
    elif current_price < 200:
        increment = 5
    else:
        increment = 10
    
    atm_strike = round(current_price / increment) * increment
    itm_strike = atm_strike - increment
    otm_strike = atm_strike + increment
    
    return {
        'ITM': round(itm_strike, 2),
        'ATM': round(atm_strike, 2),
        'OTM': round(otm_strike, 2)
    }

def format_email_body(signals):
    """Format the signals into an HTML email"""
    
    if not signals:
        return "<html><body><h2>No bullish engulfing signals found today.</h2></body></html>"
    
    # Sort by rating (highest first)
    signals_sorted = sorted(signals, key=lambda x: x['rating'], reverse=True)
    
    html = """
    <html>
    <head>
        <style>
            body { font-family: Arial, sans-serif; }
            .signal { 
                border: 2px solid #ddd; 
                margin: 20px 0; 
                padding: 15px; 
                border-radius: 8px;
                background-color: #f9f9f9;
            }
            .rating-5 { border-color: #FFD700; background-color: #FFFACD; }
            .rating-4 { border-color: #87CEEB; background-color: #F0F8FF; }
            .rating-3 { border-color: #90EE90; background-color: #F0FFF0; }
            .header { background-color: #4CAF50; color: white; padding: 10px; }
            .stars { color: #FFD700; font-size: 24px; }
            table { width: 100%; border-collapse: collapse; margin: 10px 0; }
            td { padding: 8px; }
            .label { font-weight: bold; width: 40%; }
            h1 { color: #333; }
            h2 { color: #4CAF50; margin-top: 0; }
        </style>
    </head>
    <body>
        <div class="header">
            <h1>📈 Daily Bullish Engulfing Signals - """ + datetime.now().strftime('%B %d, %Y') + """</h1>
        </div>
        <p>Found <strong>""" + str(len(signals)) + """</strong> signal(s) today.</p>
    """
    
    for signal in signals_sorted:
        stars = '⭐' * signal['rating']
        rating_class = f"rating-{signal['rating']}"
        
        html += f"""
        <div class="signal {rating_class}">
            <h2>{signal['ticker']}</h2>
            <div class="stars">{stars} ({signal['rating']}/5)</div>
            
            <h3>📊 Signal Details</h3>
            <table>
                <tr>
                    <td class="label">Current Price:</td>
                    <td>${signal['current_price']}</td>
                </tr>
                <tr>
                    <td class="label">Engulfing Body Size:</td>
                    <td>{signal['body_size_pct']}%</td>
                </tr>
                <tr>
                    <td class="label">Volume Ratio:</td>
                    <td>{signal['volume_ratio']}x average</td>
                </tr>
                <tr>
                    <td class="label">Red Days Before:</td>
                    <td>{signal['red_days_before']} days</td>
                </tr>
                <tr>
                    <td class="label">Distance from 50-day MA:</td>
                    <td>{signal['distance_from_ma50']}%</td>
                </tr>
            </table>
            
            <h3>🎯 Trading Guidance</h3>
            <table>
                <tr>
                    <td class="label">Entry Price:</td>
                    <td>${signal['entry_price']}</td>
                </tr>
                <tr>
                    <td class="label">Target Price:</td>
                    <td>${signal['target_price']} (+{round(((signal['target_price']/signal['entry_price'])-1)*100, 1)}%)</td>
                </tr>
                <tr>
                    <td class="label">Stop Loss:</td>
                    <td>${signal['stop_loss']} (-{round(((signal['entry_price']/signal['stop_loss'])-1)*100, 1)}%)</td>
                </tr>
                <tr>
                    <td class="label">Weekly Resistance:</td>
                    <td>${signal['weekly_resistance']}</td>
                </tr>
                <tr>
                    <td class="label">ATR (14-day):</td>
                    <td>${signal['atr']}</td>
                </tr>
            </table>
            
            <h3>📝 Options Suggestions (2-4 weeks)</h3>
            <table>
                <tr>
                    <td class="label">Suggested Expirations:</td>
                    <td>{', '.join(signal['exp_dates'])}</td>
                </tr>
                <tr>
                    <td class="label">ITM Strike:</td>
                    <td>${signal['suggested_strikes']['ITM']}</td>
                </tr>
                <tr>
                    <td class="label">ATM Strike:</td>
                    <td>${signal['suggested_strikes']['ATM']}</td>
                </tr>
                <tr>
                    <td class="label">OTM Strike:</td>
                    <td>${signal['suggested_strikes']['OTM']}</td>
                </tr>
            </table>
            
            <p><em>Note: Always verify current option prices and implied volatility before trading. Consider your risk tolerance and position sizing.</em></p>
        </div>
        """
    
    html += """
        <hr>
        <p style="color: #666; font-size: 12px;">
            <strong>Disclaimer:</strong> This is automated technical analysis for educational purposes. 
            Not financial advice. Always do your own research and consult with a financial advisor.
        </p>
    </body>
    </html>
    """
    
    return html

def send_email(subject, body_html, to_email, from_email, password):
    """Send email via Gmail SMTP"""
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = from_email
        msg['To'] = to_email
        
        html_part = MIMEText(body_html, 'html')
        msg.attach(html_part)
        
        # Connect to Gmail SMTP
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(from_email, password)
        server.send_message(msg)
        server.quit()
        
        print(f"Email sent successfully to {to_email}")
        return True
    except Exception as e:
        print(f"Error sending email: {str(e)}")
        return False

def main():
    """Main function to run the scanner"""
    print(f"Starting S&P 500 Bullish Engulfing Scanner - {datetime.now()}")
    
    # Get environment variables for email
    FROM_EMAIL = os.environ.get('GMAIL_ADDRESS')
    EMAIL_PASSWORD = os.environ.get('GMAIL_APP_PASSWORD')
    TO_EMAIL = os.environ.get('TO_EMAIL', FROM_EMAIL)  # Default to same email
    
    if not FROM_EMAIL or not EMAIL_PASSWORD:
        print("ERROR: Email credentials not set in environment variables")
        return
    
    # Get S&P 500 tickers
    print("Fetching S&P 500 tickers...")
    tickers = get_sp500_tickers()
    print(f"Scanning {len(tickers)} tickers...")
    
    # Scan all tickers
    signals = []
    for i, ticker in enumerate(tickers):
        if (i + 1) % 50 == 0:
            print(f"Progress: {i + 1}/{len(tickers)} tickers scanned...")
        
        signal = check_bullish_engulfing(ticker)
        if signal:
            signals.append(signal)
            print(f"✓ Signal found: {ticker} ({signal['rating']} stars)")
    
    print(f"\nScan complete! Found {len(signals)} signals.")
    
    # Format and send email
    subject = f"📈 Bullish Engulfing Signals - {datetime.now().strftime('%B %d, %Y')} ({len(signals)} found)"
    body_html = format_email_body(signals)
    
    send_email(subject, body_html, TO_EMAIL, FROM_EMAIL, EMAIL_PASSWORD)
    
    print("Done!")

if __name__ == "__main__":
    main()
