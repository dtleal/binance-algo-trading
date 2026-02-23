import { useFilter } from "../contexts/FilterContext";
import { useBotStates } from "../hooks/useApi";

export default function GlobalFilter() {
  const { filter, updateFilter, resetFilter } = useFilter();
  const { bots } = useBotStates();

  const symbols = ["ALL", ...Array.from(new Set(Object.values(bots).map(b => b.symbol)))];
  const strategies = ["ALL", "momshort", "pullback"];
  const dateRanges = [1, 7, 30, 90];
  const today = new Date().toISOString().slice(0, 10);

  const isPresetActive = (d: number) => filter.specificDate === null && filter.dateRange === d;

  const handleDateRangeClick = (d: number) => {
    updateFilter({ dateRange: d, specificDate: null });
  };

  const handleSpecificDate = (val: string) => {
    if (val) {
      updateFilter({ specificDate: val });
    } else {
      updateFilter({ specificDate: null });
    }
  };

  const isFiltered =
    filter.symbol !== "ALL" ||
    filter.strategy !== "ALL" ||
    filter.dateRange !== 30 ||
    filter.specificDate !== null;

  return (
    <div className="bg-gray-800 border border-gray-700 rounded-xl p-3 md:p-4 mb-4 md:mb-6">
      <div className="flex flex-col md:flex-row md:flex-wrap md:items-end gap-3 md:gap-4">

        {/* Symbol filter */}
        <div className="flex-1 md:min-w-[150px]">
          <label className="block text-xs text-gray-400 uppercase tracking-wider mb-2">
            Symbol
          </label>
          <select
            value={filter.symbol}
            onChange={(e) => updateFilter({ symbol: e.target.value })}
            className="w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white
              focus:outline-none focus:border-emerald-600 transition-colors"
          >
            {symbols.map(s => (
              <option key={s} value={s}>{s === "ALL" ? "All Symbols" : s}</option>
            ))}
          </select>
        </div>

        {/* Strategy filter */}
        <div className="flex-1 md:min-w-[150px]">
          <label className="block text-xs text-gray-400 uppercase tracking-wider mb-2">
            Strategy
          </label>
          <select
            value={filter.strategy}
            onChange={(e) => updateFilter({ strategy: e.target.value })}
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

        {/* Date range presets */}
        <div className="flex-1 md:min-w-[180px]">
          <label className="block text-xs text-gray-400 uppercase tracking-wider mb-2">
            Period
          </label>
          <div className="flex gap-1 bg-gray-900 border border-gray-700 rounded-lg p-1">
            {dateRanges.map(d => (
              <button
                key={d}
                onClick={() => handleDateRangeClick(d)}
                className={`flex-1 px-2 md:px-3 py-1.5 rounded text-xs md:text-sm font-medium transition-colors ${
                  isPresetActive(d)
                    ? "bg-emerald-600 text-white"
                    : "text-gray-400 hover:text-white hover:bg-gray-800"
                }`}
              >
                {d === 1 ? "Today" : `${d}d`}
              </button>
            ))}
          </div>
        </div>

        {/* Specific date picker */}
        <div className="flex-1 md:min-w-[160px]">
          <label className="block text-xs text-gray-400 uppercase tracking-wider mb-2">
            Specific Date
          </label>
          <input
            type="date"
            max={today}
            value={filter.specificDate ?? ""}
            onChange={(e) => handleSpecificDate(e.target.value)}
            className={`w-full bg-gray-900 border rounded-lg px-3 py-2 text-sm text-white
              focus:outline-none transition-colors
              ${filter.specificDate ? "border-emerald-600" : "border-gray-700 focus:border-emerald-600"}`}
          />
        </div>

        {/* Reset button */}
        <div className="flex gap-2 w-full md:w-auto">
          <button
            onClick={resetFilter}
            disabled={!isFiltered}
            className="flex-1 md:flex-none px-4 py-2 bg-gray-700 hover:bg-gray-600 disabled:opacity-40
              disabled:cursor-not-allowed text-gray-300 text-sm rounded-lg transition-colors"
          >
            Reset
          </button>
        </div>
      </div>

      {/* Active filter badges */}
      {isFiltered && (
        <div className="mt-3 pt-3 border-t border-gray-700 flex items-center gap-2 text-xs text-gray-400 flex-wrap">
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
          {filter.specificDate ? (
            <span className="px-2 py-1 bg-amber-900/40 text-amber-400 rounded">
              {filter.specificDate}
            </span>
          ) : filter.dateRange !== 30 && (
            <span className="px-2 py-1 bg-amber-900/40 text-amber-400 rounded">
              {filter.dateRange === 1 ? "Today" : `${filter.dateRange} days`}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
