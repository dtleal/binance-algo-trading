"""Interactive AXSUSDC candlestick chart with toggleable indicators."""

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

CSV_FILE = "axsusdc_1m_klines.csv"

# Load and resample to daily
df = pd.read_csv(CSV_FILE)
df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
df.set_index("open_time", inplace=True)

daily = df.resample("1D").agg({
    "open": "first", "high": "max", "low": "min",
    "close": "last", "volume": "sum",
}).dropna()

# Indicators
daily["SMA 20"] = daily["close"].rolling(20).mean()
daily["SMA 50"] = daily["close"].rolling(50).mean()

# VWAP: 20-day rolling window
pv = daily["close"] * daily["volume"]
daily["VWAP 20"] = pv.rolling(20).sum() / daily["volume"].rolling(20).sum()

# Build figure with volume subplot
fig = make_subplots(
    rows=2, cols=1, shared_xaxes=True,
    vertical_spacing=0.03,
    row_heights=[0.75, 0.25],
)

# Candlesticks
fig.add_trace(go.Candlestick(
    x=daily.index,
    open=daily["open"], high=daily["high"],
    low=daily["low"], close=daily["close"],
    name="OHLC",
    increasing_line_color="#26a69a", decreasing_line_color="#ef5350",
), row=1, col=1)

# SMA 20
fig.add_trace(go.Scatter(
    x=daily.index, y=daily["SMA 20"],
    name="SMA 20", line=dict(color="#00bcd4", width=1.5),
    visible="legendonly",
), row=1, col=1)

# SMA 50
fig.add_trace(go.Scatter(
    x=daily.index, y=daily["SMA 50"],
    name="SMA 50", line=dict(color="#e040fb", width=1.5),
    visible="legendonly",
), row=1, col=1)

# VWAP
fig.add_trace(go.Scatter(
    x=daily.index, y=daily["VWAP 20"],
    name="VWAP 20", line=dict(color="#ffa726", width=2),
), row=1, col=1)

# Volume bars
colors = ["#26a69a" if c >= o else "#ef5350" for c, o in zip(daily["close"], daily["open"])]
fig.add_trace(go.Bar(
    x=daily.index, y=daily["volume"],
    name="Volume", marker_color=colors,
    showlegend=False,
), row=2, col=1)

# Layout
fig.update_layout(
    title="AXSUSDC Daily Candles",
    template="plotly_dark",
    xaxis_rangeslider_visible=False,
    yaxis_title="Price (USDC)",
    yaxis2_title="Volume (AXS)",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    height=800,
)

fig.write_html("axsusdc_chart.html")
print("Saved interactive chart to axsusdc_chart.html")
fig.show()
