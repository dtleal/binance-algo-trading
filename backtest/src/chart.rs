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
