import { Routes, Route, useLocation } from "react-router-dom";
import Sidebar from "./components/Sidebar";
import Overview from "./pages/Overview";
import Bots from "./pages/Bots";
import Positions from "./pages/Positions";
import History from "./pages/History";
import Commissions from "./pages/Commissions";
import { useFeedWebSocket } from "./hooks/useWebSocket";
import { FilterProvider } from "./contexts/FilterContext";
import { AlertProvider } from "./contexts/AlertContext";
import GlobalFilter from "./components/GlobalFilter";
import AlertBanner from "./components/AlertBanner";
import ChatBubble from "./components/ChatBubble";

const FILTER_PAGES = ["/", "/positions", "/history", "/commissions"];

function AppInner({ events, connected }: { events: ReturnType<typeof useFeedWebSocket>["events"]; connected: boolean }) {
  const location = useLocation();
  const showFilter = FILTER_PAGES.includes(location.pathname);

  return (
    <div className="flex h-screen bg-gray-950 text-gray-100 overflow-hidden">
      <Sidebar connected={connected} />
      <div className="flex-1 flex flex-col overflow-hidden">
        <AlertBanner />
        <main className="flex-1 overflow-y-auto p-4 md:p-6 pt-16 lg:pt-6">
          {showFilter && <GlobalFilter />}
          <Routes>
            <Route path="/"            element={<Overview />} />
            <Route path="/bots"        element={<Bots events={events} />} />
            <Route path="/positions"   element={<Positions />} />
            <Route path="/history"     element={<History />} />
            <Route path="/commissions" element={<Commissions />} />
          </Routes>
        </main>
      </div>
      <ChatBubble />
    </div>
  );
}

export default function App() {
  const { events, connected } = useFeedWebSocket();

  return (
    <FilterProvider>
      <AlertProvider>
        <AppInner events={events} connected={connected} />
      </AlertProvider>
    </FilterProvider>
  );
}
