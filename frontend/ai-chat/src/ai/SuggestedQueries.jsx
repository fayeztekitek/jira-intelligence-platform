import { useState, useEffect, useCallback } from 'react';

const API = import.meta.env.DEV ? '' : '';
const FALLBACK_SUGGESTIONS = [
  'What are the most risky projects?',
  'Show me the executive summary for CORE',
  'What changed during the last sprint?',
  'Which components generate the most bugs?',
  'Compare CORE and MOBILE',
  'What should management focus on this week?',
];

function getAuthToken() {
  try {
    return localStorage.getItem('jira_intel_token') || '';
  } catch {
    return '';
  }
}

export default function SuggestedQueries({ onSelect, context, refreshKey }) {
  const [suggestions, setSuggestions] = useState([]);
  const [loading, setLoading] = useState(false);

  const fetchSuggestions = useCallback(async () => {
    setLoading(true);
    try {
      const token = getAuthToken();
      const headers = { 'Content-Type': 'application/json' };
      if (token) headers['Authorization'] = `Bearer ${token}`;

      const res = await fetch(`${API}/api/ai/suggest`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ context: context || null }),
      });

      if (res.ok) {
        const data = await res.json();
        if (data.suggestions?.length > 0) {
          setSuggestions(data.suggestions);
          return;
        }
      }
    } catch {
      // fall through to fallback
    }
    setSuggestions(FALLBACK_SUGGESTIONS);
    setLoading(false);
  }, [context]);

  useEffect(() => {
    fetchSuggestions();
  }, [fetchSuggestions, refreshKey]);

  if (suggestions.length === 0 && !loading) return null;

  return (
    <div style={{
      display: 'flex',
      flexWrap: 'wrap',
      gap: 8,
      justifyContent: 'center',
      maxWidth: 600,
      margin: '0 auto',
    }}>
      {suggestions.map((q) => (
        <button
          key={q}
          style={{
            background: '#1a1d27',
            border: '1px solid #2e3248',
            borderRadius: 20,
            padding: '6px 14px',
            fontSize: 13,
            color: '#8892a4',
            cursor: 'pointer',
            transition: 'all 0.15s',
            fontFamily: "'Inter', system-ui, sans-serif",
          }}
          onClick={() => onSelect(q)}
          onMouseOver={(e) => {
            e.currentTarget.style.color = '#e2e8f0';
            e.currentTarget.style.borderColor = '#3b82f6';
          }}
          onMouseOut={(e) => {
            e.currentTarget.style.color = '#8892a4';
            e.currentTarget.style.borderColor = '#2e3248';
          }}
        >
          {q}
        </button>
      ))}
    </div>
  );
}
