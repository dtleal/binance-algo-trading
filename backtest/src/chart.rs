//! HTML chart writer — Plotly via CDN, JSON inline. Sem deps Rust.
//!
//! Output igual ao Python (`scripts/backtest_detail.py::plot_equity`):
//! 3 subplots empilhados — equity / per-trade P&L / drawdown.

use crate::detail::Trade;
use anyhow::{Context, Result};
use std::fs::File;
use std::io::Write;

const PLOTLY_CDN: &str = "https://cdn.plot.ly/plotly-2.35.2.min.js";

pub fn write_html(
    path: &str,
    title: &str,
    trades: &[Trade],
    initial_capital: f64,
) -> Result<()> {
    // Series: x = timestamp ISO, y = capital / pnl / drawdown
    let times: Vec<String> = trades.iter().map(|t| t.exit_time.to_rfc3339()).collect();
    let equity: Vec<f64>   = trades.iter().map(|t| t.capital_after).collect();
    let pnl:    Vec<f64>   = trades.iter().map(|t| t.pnl_dollar).collect();
    let dd:     Vec<f64>   = trades.iter().map(|t| t.drawdown_pct).collect();

    let bar_colors: Vec<&str> = trades.iter()
        .map(|t| if t.pnl_dollar >= 0.0 { "#26a69a" } else { "#ef5350" })
        .collect();

    let times_json   = json_strings(&times);
    let equity_json  = json_floats(&equity);
    let pnl_json     = json_floats(&pnl);
    let dd_json      = json_floats(&dd);
    let colors_json  = json_strings(&bar_colors.iter().map(|s| s.to_string()).collect::<Vec<_>>());

    let html = format!(
r#"<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>{title}</title>
<script src="{cdn}"></script>
<style>body{{margin:0;background:#1a1a1a;font-family:system-ui;}}</style>
</head><body>
<div id="chart" style="width:100%;height:95vh;"></div>
<script>
const initial = {init};
Plotly.newPlot('chart', [
  {{
    x: {times}, y: {equity},
    type: 'scatter', mode: 'lines',
    name: 'Equity', line: {{color: '#26a69a', width: 2}},
    xaxis: 'x', yaxis: 'y'
  }},
  {{
    x: {times}, y: [...Array({n}).fill(initial)],
    type: 'scatter', mode: 'lines',
    line: {{color: '#888', width: 1, dash: 'dash'}},
    showlegend: false, hoverinfo: 'skip',
    xaxis: 'x', yaxis: 'y'
  }},
  {{
    x: {times}, y: {pnl},
    type: 'bar', name: 'Trade P&L',
    marker: {{color: {colors}}},
    xaxis: 'x2', yaxis: 'y2'
  }},
  {{
    x: {times}, y: {dd},
    type: 'scatter', mode: 'lines', fill: 'tozeroy',
    name: 'Drawdown %',
    line: {{color: '#ef5350', width: 1}},
    fillcolor: 'rgba(239,83,80,0.2)',
    xaxis: 'x3', yaxis: 'y3'
  }}
], {{
  title: {{text: '{title}', font: {{color: '#eee'}}}},
  template: 'plotly_dark',
  paper_bgcolor: '#1a1a1a',
  plot_bgcolor: '#1a1a1a',
  font: {{color: '#ddd'}},
  grid: {{rows: 3, columns: 1, pattern: 'independent', roworder: 'top to bottom'}},
  yaxis:  {{title: 'Capital ($)',  domain: [0.66, 1.00]}},
  yaxis2: {{title: 'P&L ($)',      domain: [0.34, 0.64]}},
  yaxis3: {{title: 'Drawdown %',   domain: [0.00, 0.32], autorange: 'reversed'}},
  xaxis:  {{anchor: 'y',  domain: [0, 1]}},
  xaxis2: {{anchor: 'y2', domain: [0, 1]}},
  xaxis3: {{anchor: 'y3', domain: [0, 1]}},
  showlegend: true,
  legend: {{orientation: 'h', y: 1.05}},
  margin: {{t: 60, b: 40, l: 60, r: 30}}
}}, {{responsive: true, displaylogo: false}});
</script>
</body></html>
"#,
        title = title,
        cdn = PLOTLY_CDN,
        init = initial_capital,
        times = times_json,
        equity = equity_json,
        pnl = pnl_json,
        dd = dd_json,
        colors = colors_json,
        n = trades.len()
    );

    let mut f = File::create(path).with_context(|| format!("creating {path}"))?;
    f.write_all(html.as_bytes())?;
    Ok(())
}

fn json_strings(v: &[String]) -> String {
    let mut s = String::from("[");
    for (i, x) in v.iter().enumerate() {
        if i > 0 { s.push(','); }
        s.push('"');
        for ch in x.chars() {
            match ch {
                '\\' | '"' => { s.push('\\'); s.push(ch); }
                '\n' => s.push_str("\\n"),
                '\r' => s.push_str("\\r"),
                '\t' => s.push_str("\\t"),
                _ => s.push(ch),
            }
        }
        s.push('"');
    }
    s.push(']');
    s
}

// ── Range debug chart ─────────────────────────────────────────────────────────

pub fn write_range_debug_html(path: &str, day: &crate::range_debug::DayDebug) -> Result<()> {
    use crate::range_debug::TradeEvent;

    let times: Vec<String> = day.snapshots.iter().map(|s| s.time.to_rfc3339()).collect();
    let opens:  Vec<f64> = day.snapshots.iter().map(|s| s.open).collect();
    let highs:  Vec<f64> = day.snapshots.iter().map(|s| s.high).collect();
    let lows:   Vec<f64> = day.snapshots.iter().map(|s| s.low).collect();
    let closes: Vec<f64> = day.snapshots.iter().map(|s| s.close).collect();
    let adx:    Vec<f64> = day.snapshots.iter().map(|s| s.adx).collect();
    let atr_pct:Vec<f64> = day.snapshots.iter().map(|s| s.atr_pct).collect();

    // Shapes para lateral regions: rect translúcido azul
    let mut shapes = String::from("[");
    let mut first = true;
    for r in &day.regions {
        if !first { shapes.push(','); } first = false;
        shapes.push_str(&format!(
            r#"{{"type":"rect","xref":"x","yref":"y","x0":"{x0}","x1":"{x1}","y0":{y0},"y1":{y1},"fillcolor":"rgba(80,130,255,0.18)","line":{{"width":1,"color":"rgba(80,130,255,0.6)"}},"layer":"below"}}"#,
            x0 = r.start_time.to_rfc3339(),
            x1 = r.end_time.to_rfc3339(),
            y0 = r.range_low,
            y1 = r.range_high,
        ));
    }
    shapes.push(']');

    // Trade markers
    let mut open_long_x  = Vec::new(); let mut open_long_y  = Vec::new();
    let mut open_short_x = Vec::new(); let mut open_short_y = Vec::new();
    let mut close_x      = Vec::new(); let mut close_y      = Vec::new();
    let mut close_text   = Vec::new();
    for t in &day.trades {
        match (t.event, t.side) {
            (TradeEvent::Open, crate::Direction::Long)  => { open_long_x.push(t.time.to_rfc3339()); open_long_y.push(t.price); }
            (TradeEvent::Open, crate::Direction::Short) => { open_short_x.push(t.time.to_rfc3339()); open_short_y.push(t.price); }
            (TradeEvent::Close, _) => {
                close_x.push(t.time.to_rfc3339());
                close_y.push(t.price);
                close_text.push(format!("{:.2}%", t.pnl_pct.unwrap_or(0.0)));
            }
        }
    }

    let title = format!("Range debug — {} ({} regions, {} trades)",
        day.date, day.regions.len(),
        day.trades.iter().filter(|t| matches!(t.event, TradeEvent::Open)).count());
    let params_str = serde_json::to_string_pretty(&day.params).unwrap_or_default();

    let html = format!(
r#"<!DOCTYPE html>
<html><head>
<meta charset="utf-8"><title>{title}</title>
<script src="{cdn}"></script>
<style>
body{{margin:0;background:#1a1a1a;color:#ddd;font-family:system-ui;}}
#meta{{padding:12px 24px;font-size:12px;color:#aaa;}}
pre{{display:inline-block;background:#222;padding:8px;border-radius:4px;font-size:11px;}}
</style>
</head><body>
<div id="meta"><b>{title}</b><br>params: <pre>{params}</pre></div>
<div id="chart" style="width:100%;height:88vh;"></div>
<script>
Plotly.newPlot('chart', [
  {{
    type: 'candlestick',
    x: {times}, open: {opens}, high: {highs}, low: {lows}, close: {closes},
    name: 'OHLC',
    increasing: {{line:{{color:'#26a69a'}}}}, decreasing: {{line:{{color:'#ef5350'}}}},
    xaxis: 'x', yaxis: 'y'
  }},
  {{
    type: 'scatter', mode: 'markers', name: 'Open LONG',
    x: {open_long_x}, y: {open_long_y},
    marker: {{symbol: 'triangle-up', size: 14, color: '#00e676', line:{{width:1,color:'#fff'}}}},
    xaxis: 'x', yaxis: 'y'
  }},
  {{
    type: 'scatter', mode: 'markers', name: 'Open SHORT',
    x: {open_short_x}, y: {open_short_y},
    marker: {{symbol: 'triangle-down', size: 14, color: '#ff5252', line:{{width:1,color:'#fff'}}}},
    xaxis: 'x', yaxis: 'y'
  }},
  {{
    type: 'scatter', mode: 'markers+text', name: 'Close',
    x: {close_x}, y: {close_y}, text: {close_text}, textposition: 'top center',
    marker: {{symbol: 'x', size: 10, color: '#ffeb3b'}},
    textfont: {{size: 9, color: '#ffeb3b'}},
    xaxis: 'x', yaxis: 'y'
  }},
  {{
    type: 'scatter', mode: 'lines', name: 'ADX',
    x: {times}, y: {adx},
    line: {{color: '#ba68c8', width: 1}},
    xaxis: 'x2', yaxis: 'y2'
  }},
  {{
    type: 'scatter', mode: 'lines', name: 'ATR%',
    x: {times}, y: {atr_pct},
    line: {{color: '#ffa726', width: 1}},
    xaxis: 'x2', yaxis: 'y3'
  }}
], {{
  template: 'plotly_dark',
  paper_bgcolor: '#1a1a1a', plot_bgcolor: '#1a1a1a',
  font: {{color: '#ddd'}},
  shapes: {shapes},
  grid: {{rows: 2, columns: 1, pattern: 'independent', roworder: 'top to bottom'}},
  yaxis:  {{title: 'Price', domain: [0.30, 1.00]}},
  yaxis2: {{title: 'ADX',   domain: [0.00, 0.28], side: 'left'}},
  yaxis3: {{title: 'ATR%',  domain: [0.00, 0.28], side: 'right', overlaying: 'y2'}},
  xaxis:  {{anchor: 'y',  rangeslider: {{visible: false}}}},
  xaxis2: {{anchor: 'y2', matches: 'x'}},
  legend: {{orientation: 'h', y: 1.05}},
  margin: {{t: 30, b: 30, l: 60, r: 60}}
}}, {{responsive: true, displaylogo: false}});
</script>
</body></html>
"#,
        title = title,
        cdn = PLOTLY_CDN,
        params = params_str,
        times = json_strings(&times),
        opens = json_floats(&opens),
        highs = json_floats(&highs),
        lows  = json_floats(&lows),
        closes= json_floats(&closes),
        adx   = json_floats(&adx),
        atr_pct = json_floats(&atr_pct),
        shapes = shapes,
        open_long_x  = json_strings(&open_long_x),
        open_long_y  = json_floats(&open_long_y),
        open_short_x = json_strings(&open_short_x),
        open_short_y = json_floats(&open_short_y),
        close_x = json_strings(&close_x),
        close_y = json_floats(&close_y),
        close_text = json_strings(&close_text),
    );

    let mut f = File::create(path).with_context(|| format!("creating {path}"))?;
    f.write_all(html.as_bytes())?;
    Ok(())
}

fn json_floats(v: &[f64]) -> String {
    let mut s = String::from("[");
    for (i, x) in v.iter().enumerate() {
        if i > 0 { s.push(','); }
        if x.is_finite() {
            s.push_str(&format!("{:.4}", x));
        } else {
            s.push_str("null");
        }
    }
    s.push(']');
    s
}
