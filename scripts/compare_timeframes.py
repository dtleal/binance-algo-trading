"""Compare VWAPPullback strategy performance across different timeframes."""

import pandas as pd
from pathlib import Path

TIMEFRAMES = ['1m', '5m', '15m', '30m', '60m']

def analyze_timeframe(tf: str) -> dict:
    """Analyze best VWAPPullback config for a timeframe."""
    csv_file = f"backtest_sweep_{tf}.csv"

    if not Path(csv_file).exists():
        return None

    df = pd.read_csv(csv_file, low_memory=False)
    vwap = df[df['strategy'] == 'VWAPPullback'].copy()

    # Filter for min 30 trades and positive return
    vwap_good = vwap[(vwap['trades'] >= 30) & (vwap['return_pct'] > 0)]

    if len(vwap_good) == 0:
        # Try without 30 trade filter
        vwap_good = vwap[vwap['return_pct'] > 0]
        if len(vwap_good) == 0:
            return None

    best = vwap_good.sort_values('return_pct', ascending=False).iloc[0]

    return {
        'timeframe': tf,
        'return_pct': best['return_pct'],
        'trades': int(best['trades']),
        'win_rate': best['win_rate'] * 100,
        'max_dd': best['max_dd_pct'],
        'return_dd_ratio': best['return_pct'] / best['max_dd_pct'] if best['max_dd_pct'] > 0 else 0,
        'final_capital': best['final_capital'],
        'max_consec_loss': int(best['max_consec_loss']),
        'tp': best['tp_pct'],
        'sl': best['sl_pct'],
        'ema': best['ema_period'],
        'min_bars': int(best['min_bars']),
        'confirm_bars': int(best['confirm_bars']),
        'vwap_prox': best['vwap_prox'] * 100,
        'vwap_window': best['vwap_window'],
        'max_trades_day': int(best['max_trades_per_day']),
        'pos_size': best['pos_size_pct'],
    }

def main():
    print("="*100)
    print("  📊 COMPARAÇÃO: VWAPPullback em BTCUSDT - MÚLTIPLOS TIMEFRAMES")
    print("="*100)

    results = []
    for tf in TIMEFRAMES:
        result = analyze_timeframe(tf)
        if result:
            results.append(result)

    if not results:
        print("\n❌ Nenhum resultado encontrado!")
        return

    # Create DataFrame for easy comparison
    df = pd.DataFrame(results)

    print("\n📈 RESUMO COMPARATIVO")
    print("-"*100)
    print(f"{'Timeframe':<10} {'Retorno':<10} {'Trades':<8} {'Win%':<8} {'DrawDown':<10} {'R/DD':<8} {'Capital Final':<15}")
    print("-"*100)

    for _, row in df.iterrows():
        print(f"{row['timeframe']:<10} {row['return_pct']:>7.2f}%  {row['trades']:>6}  {row['win_rate']:>6.1f}%  {row['max_dd']:>8.2f}%  {row['return_dd_ratio']:>6.2f}x  ${row['final_capital']:>10.2f}")

    # Find best by different metrics
    best_return = df.loc[df['return_pct'].idxmax()]
    best_risk_adj = df.loc[df['return_dd_ratio'].idxmax()]
    best_winrate = df.loc[df['win_rate'].idxmax()]

    print("\n" + "="*100)
    print("  🏆 MELHORES RESULTADOS POR MÉTRICA")
    print("="*100)

    print(f"\n💰 MAIOR RETORNO: {best_return['timeframe']}")
    print(f"   Retorno: +{best_return['return_pct']:.2f}%")
    print(f"   Trades: {best_return['trades']}")
    print(f"   Win Rate: {best_return['win_rate']:.1f}%")
    print(f"   Max DD: {best_return['max_dd']:.2f}%")

    print(f"\n⚖️  MELHOR RISK-ADJUSTED (Return/DD): {best_risk_adj['timeframe']}")
    print(f"   R/DD Ratio: {best_risk_adj['return_dd_ratio']:.2f}x")
    print(f"   Retorno: +{best_risk_adj['return_pct']:.2f}%")
    print(f"   Max DD: {best_risk_adj['max_dd']:.2f}%")

    print(f"\n🎯 MAIOR WIN RATE: {best_winrate['timeframe']}")
    print(f"   Win Rate: {best_winrate['win_rate']:.1f}%")
    print(f"   Retorno: +{best_winrate['return_pct']:.2f}%")
    print(f"   Trades: {best_winrate['trades']}")

    print("\n" + "="*100)
    print("  ⚙️  PARÂMETROS DO MELHOR TIMEFRAME")
    print("="*100)

    best = best_return
    print(f"\n🔧 Configuração Otimizada para {best['timeframe']}:")
    print(f"   ├─ Take Profit:      {best['tp']:.1f}%")
    print(f"   ├─ Stop Loss:        {best['sl']:.1f}%")
    print(f"   ├─ EMA Period:       {best['ema']}")
    print(f"   ├─ Min Bars:         {best['min_bars']}")
    print(f"   ├─ Confirm Bars:     {best['confirm_bars']}")
    print(f"   ├─ VWAP Proximity:   {best['vwap_prox']:.2f}%")
    print(f"   ├─ VWAP Window:      {best['vwap_window']}")
    print(f"   ├─ Max Trades/Day:   {best['max_trades_day']}")
    print(f"   └─ Position Size:    {best['pos_size']}%")

    # Calculate improvement
    tf_1m = df[df['timeframe'] == '1m'].iloc[0] if len(df[df['timeframe'] == '1m']) > 0 else None
    if tf_1m is not None and best_return['timeframe'] != '1m':
        improvement = ((best_return['return_pct'] / tf_1m['return_pct']) - 1) * 100
        print(f"\n💡 MELHORIA vs 1 minuto: {improvement:+.1f}%")
        print(f"   (de +{tf_1m['return_pct']:.2f}% para +{best_return['return_pct']:.2f}%)")

    print("\n" + "="*100)


if __name__ == "__main__":
    main()
