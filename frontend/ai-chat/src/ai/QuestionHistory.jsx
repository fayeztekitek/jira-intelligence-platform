import { useState, useEffect, useCallback } from 'react';

const STORAGE_KEY = 'jira_intel_ai_history';
const MAX_ITEMS = 50;

function loadHistory() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function saveHistory(items) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(items));
  } catch {
    // storage full or unavailable
  }
}

export default function QuestionHistory({ onSelect, currentQuestion }) {
  const [items, setItems] = useState([]);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    setItems(loadHistory());
  }, []);

  useEffect(() => {
    if (!currentQuestion) return;
    setItems((prev) => {
      const exists = prev.some((i) => i.text === currentQuestion);
      if (exists) return prev;
      const updated = [
        { id: Date.now(), text: currentQuestion, timestamp: Date.now(), pinned: false },
        ...prev,
      ].slice(0, MAX_ITEMS);
      saveHistory(updated);
      return updated;
    });
  }, [currentQuestion]);

  const togglePin = useCallback((id) => {
    setItems((prev) => {
      const updated = prev.map((i) =>
        i.id === id ? { ...i, pinned: !i.pinned } : i,
      );
      saveHistory(updated);
      return updated;
    });
  }, []);

  const clearHistory = useCallback(() => {
    if (!window.confirm('Clear all question history?')) return;
    setItems([]);
    saveHistory([]);
  }, []);

  const pinned = items.filter((i) => i.pinned);
  const recent = items.filter((i) => !i.pinned);

  const sidebarStyle = {
    position: 'fixed',
    right: open ? 0 : -280,
    top: 0,
    width: 280,
    height: '100vh',
    background: '#1a1d27',
    borderLeft: '1px solid #2e3248',
    transition: 'right 0.25s ease',
    zIndex: 1000,
    display: 'flex',
    flexDirection: 'column',
    fontFamily: "'Inter', system-ui, sans-serif",
  };

  const toggleBtnStyle = {
    position: 'fixed',
    right: open ? 290 : 10,
    top: 12,
    zIndex: 1001,
    background: '#1a1d27',
    border: '1px solid #2e3248',
    borderRadius: 8,
    color: '#8892a4',
    width: 36,
    height: 36,
    fontSize: 16,
    cursor: 'pointer',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    transition: 'right 0.25s ease',
  };

  const itemStyle = {
    padding: '10px 14px',
    cursor: 'pointer',
    fontSize: 13,
    color: '#e2e8f0',
    borderBottom: '1px solid #232636',
    transition: 'background 0.12s',
    display: 'flex',
    alignItems: 'flex-start',
    gap: 8,
  };

  const pinBtnStyle = {
    background: 'none',
    border: 'none',
    cursor: 'pointer',
    fontSize: 14,
    padding: 0,
    flexShrink: 0,
    marginTop: 1,
  };

  return (
    <>
      <button
        style={toggleBtnStyle}
        onClick={() => setOpen(!open)}
        title={open ? 'Close history' : 'Open history'}
      >
        {open ? '✕' : '☰'}
      </button>

      <div style={sidebarStyle}>
        <div style={{
          padding: '18px 14px 12px',
          borderBottom: '1px solid #2e3248',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
        }}>
          <div style={{ fontSize: 14, fontWeight: 600 }}>History</div>
          {items.length > 0 && (
            <button
              onClick={clearHistory}
              style={{
                background: 'none',
                border: '1px solid #2e3248',
                borderRadius: 4,
                color: '#8892a4',
                fontSize: 11,
                padding: '3px 8px',
                cursor: 'pointer',
              }}
            >
              Clear all
            </button>
          )}
        </div>

        <div style={{ flex: 1, overflowY: 'auto' }}>
          {pinned.length > 0 && (
            <div style={{ padding: '8px 14px 4px', fontSize: 11, color: '#8892a4', textTransform: 'uppercase', letterSpacing: 0.5 }}>
              Pinned
            </div>
          )}
          {pinned.map((item) => (
            <div key={item.id} style={itemStyle}
              onMouseOver={(e) => e.currentTarget.style.background = '#232636'}
              onMouseOut={(e) => e.currentTarget.style.background = 'transparent'}
            >
              <button
                style={{ ...pinBtnStyle, color: '#f59e0b' }}
                onClick={(e) => { e.stopPropagation(); togglePin(item.id); }}
                title="Unpin"
              >
                ★
              </button>
              <div style={{ flex: 1, minWidth: 0 }} onClick={() => { onSelect(item.text); }}>
                <div style={{
                  whiteSpace: 'nowrap',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                }}>{item.text}</div>
                <div style={{ fontSize: 10, color: '#8892a4', marginTop: 2 }}>
                  {new Date(item.timestamp).toLocaleDateString()}
                </div>
              </div>
            </div>
          ))}

          <div style={{ padding: '8px 14px 4px', fontSize: 11, color: '#8892a4', textTransform: 'uppercase', letterSpacing: 0.5 }}>
            Recent
          </div>
          {recent.slice(0, 30).map((item) => (
            <div key={item.id} style={itemStyle}
              onMouseOver={(e) => e.currentTarget.style.background = '#232636'}
              onMouseOut={(e) => e.currentTarget.style.background = 'transparent'}
            >
              <button
                style={{ ...pinBtnStyle, color: '#8892a4' }}
                onClick={(e) => { e.stopPropagation(); togglePin(item.id); }}
                title="Pin"
              >
                ☆
              </button>
              <div style={{ flex: 1, minWidth: 0 }} onClick={() => { onSelect(item.text); }}>
                <div style={{
                  whiteSpace: 'nowrap',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                }}>{item.text}</div>
                <div style={{ fontSize: 10, color: '#8892a4', marginTop: 2 }}>
                  {new Date(item.timestamp).toLocaleDateString()}
                </div>
              </div>
            </div>
          ))}

          {items.length === 0 && (
            <div style={{ padding: 20, fontSize: 13, color: '#8892a4', textAlign: 'center' }}>
              No questions yet
            </div>
          )}
        </div>
      </div>
    </>
  );
}
