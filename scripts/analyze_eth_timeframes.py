"""Analyze ETH VWAPPullback performance across timeframes."""

import pandas as pd
from pathlib import Path

TIMEFRAMES = ['1m', '5m', '15m', '30m', '60m']

def analyze_timeframe(tf: str) -> dict:
    """Analyze best VWAPPullback config for ETH timeframe."""
    csv_file = f"backtest_sweep_eth_{tf}.csv"

    if not Path(csv_file).exists():
        return None

    df = pd.read_csv(csv_file, low_memory=False)

    # Analyze all strategies
    strategies = {}
    for strat in ['VWAPPullback', 'MomShort', 'MomLong']:
        strat_df = df[df['strategy'] == strat].copy()

        # Filter for min 30 trades and positive return
        strat_good = strat_df[(strat_df['trades'] >= 30) & (strat_df['return_pct'] > 0)]

        if len(strat_good) == 0:
            strat_good = strat_df[strat_df['return_pct'] > 0]
            if len(strat_good) == 0:
                continue

        best = strat_good.sort_values('return_pct', ascending=False).iloc[0]

        strategies[strat] = {
            'return_pct': best['return_pct'],
            'trades': int(best['trades']),
            'win_rate': best['win_rate'] * 100,
            'max_dd': best['max_dd_pct'],
            'return_dd_ratio': best['return_pct'] / best['max_dd_pct'] if best['max_dd_pct'] > 0 else 0,
            'final_capital': best['final_capital'],
        }

    return strategies

def main():
    print("="*100)
    print("  🚀 ETHUSDT - ANÁLISE COMPLETA POR TIMEFRAME")
    print("="*100)

    all_results = {}
    for tf in TIMEFRAMES:
        strategies = analyze_timeframe(tf)
        if strategies:
            all_results[tf] = strategies

    if not all_results:
        print("\n❌ Nenhum resultado encontrado!")
        return

    # VWAPPullback comparison
    print("\n📊 VWAPPullback - COMPARAÇÃO POR TIMEFRAME")
    print("-"*100)
    print(f"{'TF':<6} {'Retorno':<12} {'Trades':<8} {'Win%':<8} {'DD%':<10} {'R/DD':<8} {'Capital Final':<15}")
    print("-"*100)

    vwap_results = []
    for tf, strategies in all_results.items():
        if 'VWAPPullback' in strategies:
            v = strategies['VWAPPullback']
            print(f"{tf:<6} {v['return_pct']:>9.2f}%  {v['trades']:>6}  {v['win_rate']:>6.1f}%  {v['max_dd']:>8.2f}%  {v['return_dd_ratio']:>6.2f}x  ${v['final_capital']:>10.2f}")
            vwap_results.append({'tf': tf, **v})

    # Find best VWAPPullback
    if vwap_results:
        vwap_df = pd.DataFrame(vwap_results)
        best_vwap = vwap_df.loc[vwap_df['return_pct'].idxmax()]

        print("\n" + "="*100)
        print(f"  🏆 MELHOR VWAPPullback: {best_vwap['tf']}")
        print("="*100)
        print(f"   Retorno:        +{best_vwap['return_pct']:.2f}%")
        print(f"   Trades:         {int(best_vwap['trades'])}")
        print(f"   Win Rate:       {best_vwap['win_rate']:.1f}%")
        print(f"   Max Drawdown:   {best_vwap['max_dd']:.2f}%")
        print(f"   R/DD Ratio:     {best_vwap['return_dd_ratio']:.2f}x")
        print(f"   Capital Final:  ${best_vwap['final_capital']:.2f}")

    # MomShort comparison
    print("\n📊 MomShort - COMPARAÇÃO POR TIMEFRAME")
    print("-"*100)
    print(f"{'TF':<6} {'Retorno':<12} {'Trades':<8} {'Win%':<8} {'DD%':<10} {'R/DD':<8} {'Capital Final':<15}")
    print("-"*100)

    mom_results = []
    for tf, strategies in all_results.items():
        if 'MomShort' in strategies:
            m = strategies['MomShort']
            print(f"{tf:<6} {m['return_pct']:>9.2f}%  {m['trades']:>6}  {m['win_rate']:>6.1f}%  {m['max_dd']:>8.2f}%  {m['return_dd_ratio']:>6.2f}x  ${m['final_capital']:>10.2f}")
            mom_results.append({'tf': tf, **m})

    # Find best MomShort
    if mom_results:
        mom_df = pd.DataFrame(mom_results)
        best_mom = mom_df.loc[mom_df['return_pct'].idxmax()]

        print("\n" + "="*100)
        print(f"  🏆 MELHOR MomShort: {best_mom['tf']}")
        print("="*100)
        print(f"   Retorno:        +{best_mom['return_pct']:.2f}%")
        print(f"   Trades:         {int(best_mom['trades'])}")
        print(f"   Win Rate:       {best_mom['win_rate']:.1f}%")
        print(f"   Max Drawdown:   {best_mom['max_dd']:.2f}%")
        print(f"   R/DD Ratio:     {best_mom['return_dd_ratio']:.2f}x")
        print(f"   Capital Final:  ${best_mom['final_capital']:.2f}")

    # Overall comparison
    print("\n" + "="*100)
    print("  🎯 MELHOR ESTRATÉGIA ABSOLUTA PARA ETH")
    print("="*100)

    all_best = []
    if vwap_results:
        all_best.append(('VWAPPullback', best_vwap))
    if mom_results:
        all_best.append(('MomShort', best_mom))

    if all_best:
        winner = max(all_best, key=lambda x: x[1]['return_pct'])
        print(f"\n🥇 CAMPEÃO: {winner[0]} ({winner[1]['tf']})")
        print(f"   Retorno: +{winner[1]['return_pct']:.2f}%")
        print(f"   Trades: {int(winner[1]['trades'])}")
        print(f"   Win Rate: {winner[1]['win_rate']:.1f}%")
        print(f"   Max DD: {winner[1]['max_dd']:.2f}%")
        print(f"   Capital Final: ${winner[1]['final_capital']:.2f}")

        # Check if it meets the 35% target
        if winner[1]['return_pct'] >= 35:
            print(f"\n✅ META DE 35% ATINGIDA! (+{winner[1]['return_pct']:.2f}%)")
        else:
            gap = 35 - winner[1]['return_pct']
            print(f"\n⚠️  Faltam {gap:.2f}% para atingir a meta de 35%")

            # Calculate leverage needed
            leverage_needed = 35 / winner[1]['return_pct']
            print(f"   Com position size de {leverage_needed*20:.0f}% chegaria a ~35%")

    print("\n" + "="*100)


if __name__ == "__main__":
    main()
