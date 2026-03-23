import { BrowserRouter, Routes, Route, NavLink } from 'react-router-dom';
import { useState, useCallback } from 'react';
import { Camera, Settings, PlayCircle, CheckSquare, History, Blocks, Eye } from 'lucide-react';
import Dashboard from './pages/Dashboard';
import Progress from './pages/Progress';
import Review from './pages/Review';
import HistoryPage from './pages/History';
import Providers from './pages/Providers';
import Watcher from './pages/Watcher';
import SpaceAnalysis from './pages/SpaceAnalysis';
import AiSummary from './pages/AiSummary';
import { useWebSocket } from './hooks/useWebSocket';
import type { WsMessage } from './hooks/useWebSocket';

const navItems = [
  { to: '/', icon: Settings, label: 'Config' },
  { to: '/progress', icon: PlayCircle, label: 'Progreso' },
  { to: '/review', icon: CheckSquare, label: 'Review' },
  { to: '/history', icon: History, label: 'Historial' },
  { to: '/providers', icon: Blocks, label: 'Providers' },
  { to: '/watcher', icon: Eye, label: 'Monitor' },
];

export default function App() {
  const [wsMessages, setWsMessages] = useState<WsMessage[]>([]);
  const [latestMsg, setLatestMsg] = useState<WsMessage | null>(null);

  const handleWsMessage = useCallback((msg: WsMessage) => {
    setLatestMsg(msg);
    setWsMessages((prev) => [...prev.slice(-200), msg]);
  }, []);

  const { connected } = useWebSocket(handleWsMessage);

  return (
    <BrowserRouter>
      <div className="min-h-screen bg-gray-950 text-gray-100">
        {/* Header */}
        <header className="border-b border-gray-800 bg-gray-900/80 backdrop-blur-sm sticky top-0 z-50">
          <div className="max-w-7xl mx-auto px-4 h-14 flex items-center gap-6">
            <div className="flex items-center gap-2 text-purple-400 font-semibold">
              <Camera className="w-5 h-5" />
              <span>NAS Photo Cleaner</span>
            </div>
            <nav className="flex gap-1 ml-4">
              {navItems.map(({ to, icon: Icon, label }) => (
                <NavLink
                  key={to}
                  to={to}
                  end={to === '/'}
                  className={({ isActive }) =>
                    `flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm transition-colors ${
                      isActive
                        ? 'bg-purple-500/20 text-purple-300'
                        : 'text-gray-400 hover:text-gray-200 hover:bg-gray-800'
                    }`
                  }
                >
                  <Icon className="w-4 h-4" />
                  {label}
                </NavLink>
              ))}
            </nav>
            <div className="ml-auto flex items-center gap-2 text-xs">
              <span
                className={`w-2 h-2 rounded-full ${connected ? 'bg-green-400' : 'bg-red-400'}`}
              />
              <span className="text-gray-500">{connected ? 'Conectado' : 'Desconectado'}</span>
            </div>
          </div>
        </header>

        {/* Content */}
        <main className="max-w-7xl mx-auto px-4 py-6">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/progress" element={<Progress latestMsg={latestMsg} messages={wsMessages} />} />
            <Route path="/review" element={<Review />} />
            <Route path="/history" element={<HistoryPage />} />
            <Route path="/providers" element={<Providers />} />
            <Route path="/watcher" element={<Watcher />} />
            <Route path="/analysis/:jobId" element={<SpaceAnalysis />} />
            <Route path="/ai-summary/:jobId" element={<AiSummary />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}
