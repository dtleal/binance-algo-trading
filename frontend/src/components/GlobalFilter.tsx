import { useState, useEffect } from "react";
import { useFilter } from "../contexts/FilterContext";
import { useBotStates } from "../hooks/useApi";

export default function GlobalFilter() {
  const { filter, updateFilter, resetFilter } = useFilter();
  const { bots } = useBotStates();

  // Local state for form inputs
  const [symbol, setSymbol] = useState(filter.symbol);
  const [strategy, setStrategy] = useState(filter.strategy);
  const [dateRange, setDateRange] = useState(filter.dateRange);

  // Extract unique symbols from running bots
  const symbols = ["ALL", ...Array.from(new Set(Object.values(bots).map(b => b.symbol)))];
  const strategies = ["ALL", "momshort", "pullback"];
  const dateRanges = [7, 30, 90];

  // Apply button handler
  const handleApply = () => {
    updateFilter({ symbol, strategy, dateRange });
  };

  // Reset button handler
  const handleReset = () => {
    setSymbol("ALL");
    setStrategy("ALL");
    setDateRange(30);
    resetFilter();
  };

  // Sync local state with context when filter changes externally
  useEffect(() => {
    setSymbol(filter.symbol);
    setStrategy(filter.strategy);
    setDateRange(filter.dateRange);
  }, [filter]);

  return (
    <div className="bg-gray-800 border border-gray-700 rounded-xl p-4 mb-6">
      <div className="flex flex-wrap items-end gap-4">
        {/* Symbol filter */}
        <div className="flex-1 min-w-[150px]">
          <label className="block text-xs text-gray-400 uppercase tracking-wider mb-2">
            Symbol
          </label>
          <select
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
            className="w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white
              focus:outline-none focus:border-emerald-600 transition-colors"
          >
            {symbols.map(s => (
              <option key={s} value={s}>{s === "ALL" ? "All Symbols" : s}</option>
            ))}
          </select>
        </div>

        {/* Strategy filter */}
        <div className="flex-1 min-w-[150px]">
          <label className="block text-xs text-gray-400 uppercase tracking-wider mb-2">
            Strategy
          </label>
          <select
            value={strategy}
            onChange={(e) => setStrategy(e.target.value)}
            className="w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white
              focus:outline-none focus:border-emerald-600 transition-colors"
          >
            {strategies.map(s => (
              <option key={s} value={s}>
                {s === "ALL" ? "All Strategies" : s.charAt(0).toUpperCase() + s.slice(1)}
              </option>
            ))}
          </select>
        </div>

        {/* Date range filter */}
        <div className="flex-1 min-w-[150px]">
          <label className="block text-xs text-gray-400 uppercase tracking-wider mb-2">
            Date Range
          </label>
          <div className="flex gap-1 bg-gray-900 border border-gray-700 rounded-lg p-1">
            {dateRanges.map(d => (
              <button
                key={d}
                onClick={() => setDateRange(d)}
                className={`flex-1 px-3 py-1.5 rounded text-sm font-medium transition-colors ${
                  dateRange === d
                    ? "bg-emerald-600 text-white"
                    : "text-gray-400 hover:text-white hover:bg-gray-800"
                }`}
              >
                {d}d
              </button>
            ))}
          </div>
        </div>

        {/* Action buttons */}
        <div className="flex gap-2">
          <button
            onClick={handleApply}
            className="px-6 py-2 bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-medium
              rounded-lg transition-colors"
          >
            Apply
          </button>
          <button
            onClick={handleReset}
            className="px-4 py-2 bg-gray-700 hover:bg-gray-600 text-gray-300 text-sm
              rounded-lg transition-colors"
          >
            Reset
          </button>
        </div>
      </div>

      {/* Active filter indicator */}
      {(filter.symbol !== "ALL" || filter.strategy !== "ALL" || filter.dateRange !== 30) && (
        <div className="mt-3 pt-3 border-t border-gray-700 flex items-center gap-2 text-xs text-gray-400">
          <span>Active filters:</span>
          {filter.symbol !== "ALL" && (
            <span className="px-2 py-1 bg-emerald-900/40 text-emerald-400 rounded">
              {filter.symbol}
            </span>
          )}
          {filter.strategy !== "ALL" && (
            <span className="px-2 py-1 bg-blue-900/40 text-blue-400 rounded">
              {filter.strategy}
            </span>
          )}
          {filter.dateRange !== 30 && (
            <span className="px-2 py-1 bg-amber-900/40 text-amber-400 rounded">
              {filter.dateRange} days
            </span>
          )}
        </div>
      )}
    </div>
  );
}
