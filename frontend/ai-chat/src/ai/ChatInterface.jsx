import { useState, useRef, useEffect, useCallback } from 'react';
import MessageBubble from './MessageBubble';
import SuggestedQueries from './SuggestedQueries';
import QuestionHistory from './QuestionHistory';

const API = import.meta.env.DEV ? '' : '';

const styles = {
  container: {
    display: 'flex',
    flexDirection: 'column',
    height: '100vh',
    maxWidth: 860,
    margin: '0 auto',
    padding: '0 20px',
  },
  header: {
    padding: '20px 0 12px',
    borderBottom: '1px solid #2e3248',
    marginBottom: 16,
  },
  headerTitle: {
    fontSize: 18,
    fontWeight: 600,
  },
  headerSub: {
    fontSize: 12,
    color: '#8892a4',
    marginTop: 4,
  },
  messages: {
    flex: 1,
    overflowY: 'auto',
    padding: '8px 0',
    display: 'flex',
    flexDirection: 'column',
  },
  empty: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    color: '#8892a4',
  },
  emptyIcon: {
    fontSize: 48,
    marginBottom: 12,
  },
  emptyTitle: {
    fontSize: 18,
    fontWeight: 600,
    color: '#e2e8f0',
    marginBottom: 8,
  },
  inputRow: {
    display: 'flex',
    gap: 10,
    padding: '12px 0 24px',
    borderTop: '1px solid #2e3248',
  },
  input: {
    flex: 1,
    background: '#1a1d27',
    border: '1px solid #2e3248',
    borderRadius: 10,
    padding: '12px 16px',
    color: '#e2e8f0',
    fontSize: 14,
    fontFamily: 'inherit',
    outline: 'none',
    resize: 'none',
  },
  sendBtn: {
    background: '#3b82f6',
    border: 'none',
    borderRadius: 10,
    color: '#fff',
    padding: '12px 20px',
    fontSize: 14,
    fontWeight: 600,
    cursor: 'pointer',
    alignSelf: 'flex-end',
  },
  sendBtnDisabled: {
    background: '#232636',
    color: '#8892a4',
    cursor: 'not-allowed',
  },
  typing: {
    display: 'flex',
    gap: 4,
    padding: '12px 16px',
    background: '#1a1d27',
    border: '1px solid #2e3248',
    borderRadius: 12,
    borderBottomLeftRadius: 4,
    alignSelf: 'flex-start',
    marginBottom: 16,
  },
  typingDot: {
    width: 8,
    height: 8,
    borderRadius: '50%',
    background: '#8892a4',
    animation: 'pulse 1.2s infinite',
  },
  suggestionBar: {
    padding: '0 0 10px',
  },
};

function getAuthToken() {
  try {
    return localStorage.getItem('jira_intel_token') || '';
  } catch {
    return '';
  }
}

function extractContext(messages) {
  const lastBot = messages.filter((m) => m.role === 'assistant').slice(-1)[0];
  const intent = lastBot?.toolUsed ? 'operational' : null;
  const question = messages.filter((m) => m.role === 'user').slice(-1)[0]?.content || '';
  const q = question.toUpperCase();
  const found = ['CORE', 'MOBILE', 'INFRA'].find((p) => q.includes(p));
  return { project: found || null, recent_intent: intent };
}

export default function ChatInterface() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [suggestKey, setSuggestKey] = useState(0);
  const [lastQuestion, setLastQuestion] = useState('');
  const messagesEndRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, loading]);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const sendMessage = useCallback(async (question) => {
    const text = (question || input).trim();
    if (!text || loading) return;

    setInput('');
    setLastQuestion(text);
    const userMsg = { role: 'user', content: text };
    setMessages((prev) => [...prev, userMsg]);
    setLoading(true);

    try {
      const token = getAuthToken();
      const headers = { 'Content-Type': 'application/json' };
      if (token) headers['Authorization'] = `Bearer ${token}`;

      const res = await fetch(`${API}/api/ai/ask`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ question: text }),
      });

      if (!res.ok) {
        const errText = await res.text();
        throw new Error(errText || `Error ${res.status}`);
      }

      const data = await res.json();
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: data.answer,
          toolUsed: data.tool_used,
          latencyMs: data.latency_ms,
        },
      ]);
      setSuggestKey((k) => k + 1);
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: `**Error**: ${err.message}`,
        },
      ]);
    } finally {
      setLoading(false);
    }
  }, [input, loading]);

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  const context = extractContext(messages);

  return (
    <div style={styles.container}>
      <div style={styles.header}>
        <div style={styles.headerTitle}>AI Assistant</div>
        <div style={styles.headerSub}>
          Ask questions about your Jira projects, risks, and trends
        </div>
      </div>

      <div style={styles.messages}>
        {messages.length === 0 && !loading && (
          <div style={styles.empty}>
            <div style={styles.emptyIcon}>💬</div>
            <div style={styles.emptyTitle}>Ask anything about your projects</div>
            <div style={{ fontSize: 13, color: '#8892a4', textAlign: 'center' }}>
              Try one of these questions:
            </div>
          </div>
        )}

        {messages.map((msg, i) => (
          <MessageBubble
            key={i}
            role={msg.role}
            content={msg.content}
            toolUsed={msg.toolUsed}
            latencyMs={msg.latencyMs}
          />
        ))}

        {loading && (
          <div style={styles.typing}>
            <div style={{ ...styles.typingDot, animationDelay: '0s' }} />
            <div style={{ ...styles.typingDot, animationDelay: '0.2s' }} />
            <div style={{ ...styles.typingDot, animationDelay: '0.4s' }} />
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {messages.length > 0 && !loading && (
        <div style={styles.suggestionBar}>
          <SuggestedQueries
            key={suggestKey}
            onSelect={sendMessage}
            context={context}
            refreshKey={suggestKey}
          />
        </div>
      )}

      <QuestionHistory onSelect={sendMessage} currentQuestion={lastQuestion} />

      <div style={styles.inputRow}>
        <textarea
          ref={inputRef}
          style={styles.input}
          rows={1}
          placeholder="Type your question here..."
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
        />
        <button
          style={{ ...styles.sendBtn, ...(loading || !input.trim() ? styles.sendBtnDisabled : {}) }}
          disabled={loading || !input.trim()}
          onClick={() => sendMessage()}
        >
          Send
        </button>
      </div>

      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 0.3; }
          50% { opacity: 1; }
        }
        textarea:focus { border-color: #3b82f6; }
        ::-webkit-scrollbar { width: 6px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: #2e3248; border-radius: 3px; }
      `}</style>
    </div>
  );
}
