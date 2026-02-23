import { createContext, useContext, useState, ReactNode } from "react";

export interface GlobalFilter {
  symbol: string;      // "ALL" or specific symbol like "AXSUSDT"
  strategy: string;    // "ALL", "momshort", "pullback"
  dateRange: number;   // days: 1, 7, 30, 90 — ignored when dateFrom/dateTo are set
  dateFrom: string | null;  // "YYYY-MM-DD" — overrides dateRange when set
  dateTo: string | null;    // "YYYY-MM-DD" — inclusive upper bound
}

interface FilterContextType {
  filter: GlobalFilter;
  setFilter: (filter: GlobalFilter) => void;
  updateFilter: (partial: Partial<GlobalFilter>) => void;
  resetFilter: () => void;
}

const DEFAULT_FILTER: GlobalFilter = {
  symbol: "ALL",
  strategy: "ALL",
  dateRange: 30,
  dateFrom: null,
  dateTo: null,
};

const FilterContext = createContext<FilterContextType | undefined>(undefined);

export function FilterProvider({ children }: { children: ReactNode }) {
  const [filter, setFilter] = useState<GlobalFilter>(DEFAULT_FILTER);

  const updateFilter = (partial: Partial<GlobalFilter>) => {
    setFilter(prev => ({ ...prev, ...partial }));
  };

  const resetFilter = () => {
    setFilter(DEFAULT_FILTER);
  };

  return (
    <FilterContext.Provider value={{ filter, setFilter, updateFilter, resetFilter }}>
      {children}
    </FilterContext.Provider>
  );
}

export function useFilter() {
  const context = useContext(FilterContext);
  if (!context) {
    throw new Error("useFilter must be used within FilterProvider");
  }
  return context;
}
