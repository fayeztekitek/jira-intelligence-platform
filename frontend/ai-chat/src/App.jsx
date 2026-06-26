import ChatInterface from './ai/ChatInterface';

const styles = {
  app: {
    minHeight: '100vh',
    background: '#0f1117',
    fontFamily: "'Inter', system-ui, sans-serif",
    color: '#e2e8f0',
  },
};

export default function App() {
  return (
    <div style={styles.app}>
      <ChatInterface />
    </div>
  );
}
