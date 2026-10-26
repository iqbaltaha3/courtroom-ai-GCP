import { NavLink, Route, Routes } from "react-router-dom";
import { useEffect, useState } from "react";
import { api } from "./api/client";
import SimulationPage from "./pages/SimulationPage";
import StatutesPage from "./pages/StatutesPage";
import ChatPage from "./pages/ChatPage";
import LibraryPage from "./pages/LibraryPage";
import ResearchPage from "./pages/ResearchPage";

export default function App() {
  const [health, setHealth] = useState<{ status: string } | null>(null);

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth(null));
  }, []);

  return (
    <div className="app-shell">
      <aside className="side-rail">
        <div className="brand">
          Courtroom AI
          <small>Argue. Research. Decide.</small>
        </div>
        <nav className="nav">
          <NavLink to="/" end>
            Simulation
          </NavLink>
          <NavLink to="/statutes">Statute Library</NavLink>
          <NavLink to="/research">Legal Research</NavLink>
          <NavLink to="/library">Case Library</NavLink>
          <NavLink to="/drafting">Drafting</NavLink>
          <NavLink to="/chat">Case Chat</NavLink>
        </nav>
        <div style={{ marginTop: "auto", fontSize: "0.8rem", color: "var(--ink-muted)" }}>
          <div>
            <span className={`status-dot ${health?.status === "ok" ? "" : "off"}`} />
            {health?.status === "ok" ? "Service available" : "Service unavailable"}
          </div>
        </div>
      </aside>
      <main className="main">
        <section className="workspace-header" aria-label="Courtroom AI legal workspace">
          <div>
            <div className="workspace-eyebrow">Courtroom AI</div>
            <div className="workspace-title">Legal simulation workspace</div>
          </div>
          <div className="workspace-meta">
            <span className={`status-dot ${health?.status === "ok" ? "" : "off"}`} />
            {health?.status === "ok" ? "Service available" : "Service unavailable"}
          </div>
        </section>
        <Routes>
          <Route path="/" element={<SimulationPage />} />
          <Route path="/statutes" element={<StatutesPage />} />
          <Route path="/research" element={<ResearchPage />} />
          <Route path="/library" element={<LibraryPage />} />
          <Route path="/drafting" element={<DraftingPage />} />
          <Route path="/chat" element={<ChatPage />} />
        </Routes>
      </main>
    </div>
  );
}

function DraftingPage() {
  return (
    <>
      <h1>Drafting</h1>
      <p className="page-kicker">
        Pleadings, notices, affidavits, and structured legal documents are being prepared for Courtroom AI.
      </p>
      <div className="panel coming-soon-panel">
        <h2>Coming Soon</h2>
        <div className="coming-soon-title">Draft with case facts, statutes, and research in one workflow.</div>
        <p className="snippet">
          This workspace will help convert research and case records into polished legal drafts while keeping citations,
          parties, issues, and procedural posture organized.
        </p>
      </div>
    </>
  );
}
