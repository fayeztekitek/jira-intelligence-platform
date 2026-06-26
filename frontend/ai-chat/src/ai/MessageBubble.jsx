import { useMemo } from 'react';
import Markdown from 'react-markdown';

const styles = {
  wrapper: {
    display: 'flex',
    marginBottom: 16,
  },
  bubble: {
    maxWidth: '80%',
    padding: '12px 16px',
    borderRadius: 12,
    lineHeight: 1.55,
    fontSize: 14,
    wordBreak: 'break-word',
  },
  userBubble: {
    background: '#3b82f6',
    color: '#fff',
    borderBottomRightRadius: 4,
  },
  botBubble: {
    background: '#1a1d27',
    color: '#e2e8f0',
    border: '1px solid #2e3248',
    borderBottomLeftRadius: 4,
  },
  meta: {
    fontSize: 11,
    color: '#8892a4',
    marginTop: 6,
  },
  avatar: {
    width: 30,
    height: 30,
    borderRadius: '50%',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontSize: 13,
    fontWeight: 600,
    flexShrink: 0,
  },
  userAvatar: {
    background: '#1d4ed8',
    color: '#fff',
    marginLeft: 10,
  },
  botAvatar: {
    background: '#232636',
    color: '#8892a4',
    marginRight: 10,
    border: '1px solid #2e3248',
  },
  markdownTable: {
    borderCollapse: 'collapse',
    width: '100%',
    marginTop: 10,
    marginBottom: 10,
    fontSize: 13,
  },
};

function MarkdownWrapper({ content }) {
  return (
    <Markdown
      components={{
        table: ({ children }) => <table style={styles.markdownTable}>{children}</table>,
        th: ({ children }) => (
          <th style={{ border: '1px solid #2e3248', padding: '6px 10px', background: '#232636', textAlign: 'left' }}>
            {children}
          </th>
        ),
        td: ({ children }) => {
          const text = typeof children === 'string' ? children : '';
          let cellColor = null;
          if (text.includes('↑')) cellColor = '#22c55e';
          else if (text.includes('↓')) cellColor = '#ef4444';
          const cellStyle = {
            border: '1px solid #2e3248',
            padding: '6px 10px',
            ...(cellColor ? { color: cellColor, fontWeight: 600 } : {}),
          };
          return <td style={cellStyle}>{children}</td>;
        },
        code: ({ children }) => (
          <code style={{ background: '#232636', padding: '2px 6px', borderRadius: 4, fontSize: 13 }}>
            {children}
          </code>
        ),
      }}
    >
      {content}
    </Markdown>
  );
}

export default function MessageBubble({ role, content, toolUsed, latencyMs }) {
  const isUser = role === 'user';

  const bubbleStyle = useMemo(
    () => ({ ...styles.bubble, ...(isUser ? styles.userBubble : styles.botBubble) }),
    [isUser],
  );

  return (
    <div style={{ ...styles.wrapper, justifyContent: isUser ? 'flex-end' : 'flex-start' }}>
      {!isUser && <div style={{ ...styles.avatar, ...styles.botAvatar }}>AI</div>}
      <div>
        <div style={bubbleStyle}>
          {isUser ? content : <MarkdownWrapper content={content} />}
        </div>
        {!isUser && (toolUsed || latencyMs != null) && (
          <div style={styles.meta}>
            {toolUsed && <span>Tool: {toolUsed}</span>}
            {toolUsed && latencyMs != null && <span> · </span>}
            {latencyMs != null && <span>{latencyMs}ms</span>}
          </div>
        )}
      </div>
      {isUser && <div style={{ ...styles.avatar, ...styles.userAvatar }}>U</div>}
    </div>
  );
}
