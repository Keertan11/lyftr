import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import './styles.css';

const API_URL = (import.meta.env.VITE_API_URL || 'http://localhost:8000').replace(/\/$/, '');

function AssistantMessage({ data, isError }) {
  if (isError) return <div className="message assistant error">{data}</div>;
  const m = data.metadata;
  const switched = m.fallback_attempts > 0 && m.key_id;
  return (
    <div className="message assistant" data-testid="assistant-message">
      {switched && <div className="switch-notice" data-testid="api-switch-notice">API switched to {m.key_id}</div>}
      <div className="reply markdown">
        <Markdown remarkPlugins={[remarkGfm]}>{data.reply}</Markdown>
      </div>
      <div className="metadata">
        <span data-testid="meta-source"><b>Source:</b> {m.resolution_source}</span>
        <span data-testid="meta-model"><b>Model:</b> {m.model}</span>
        <span data-testid="meta-key"><b>API key:</b> {m.key_id || 'Cache'}</span>
        <span data-testid="meta-fallback"><b>Fallbacks:</b> {m.fallback_attempts}</span>
        <span data-testid="meta-latency"><b>Latency:</b> {m.latency_ms} ms</span>
        <span data-testid="meta-input-tokens"><b>Input tokens:</b> {m.input_tokens}</span>
        <span data-testid="meta-output-tokens"><b>Output tokens:</b> {m.output_tokens}</span>
      </div>
    </div>
  );
}

function App() {
  const [sessionId] = useState(() => 's-' + crypto.randomUUID().slice(0, 8));
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [systemPrompt, setSystemPrompt] = useState('');
  const [providerDemo, setProviderDemo] = useState('normal');
  const [sending, setSending] = useState(false);

  async function sendMessage() {
    const message = input.trim();
    if (!message || sending) return;
    setSending(true);
    setInput('');
    setMessages(prev => [...prev, { role: 'user', content: message }]);
    try {
      const body = { session_id: sessionId, message };
      if (systemPrompt.trim()) body.system_prompt = systemPrompt.trim();
      const headers = { 'Content-Type': 'application/json' };
      if (providerDemo === 'key-1') headers['X-Simulate-Failure'] = 'key_1=429';
      if (providerDemo === 'key-2') headers['X-Simulate-Failure'] = 'key_2=429';
      if (providerDemo === 'all') headers['X-Simulate-Failure'] = '*=429';
      const response = await fetch(`${API_URL}/chat`, {
        method: 'POST',
        headers,
        body: JSON.stringify(body),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = data.detail || `Request failed (${response.status})`;
        setMessages(prev => [...prev, { role: 'assistant', error: true, content: typeof detail === 'string' ? detail : 'Invalid request' }]);
      } else {
        setMessages(prev => [...prev, { role: 'assistant', data }]);
      }
    } catch (_) {
      setMessages(prev => [...prev, { role: 'assistant', error: true, content: 'Could not connect to the backend.' }]);
    } finally {
      setSending(false);
    }
  }

  function onKeyDown(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      sendMessage();
    }
  }

  return (
    <main>
      <header><div className="brand">Lyftr AI</div><div className="session">Session {sessionId}</div></header>
      <section className="system-row">
        <div className="system-field">
          <label htmlFor="system">System prompt <span>(optional)</span></label>
          <textarea id="system" data-testid="system-prompt-input" value={systemPrompt} onChange={e => setSystemPrompt(e.target.value)} placeholder="You are a helpful assistant..." />
        </div>
        <div className="provider-demo">
          <label htmlFor="provider-demo">API failover demo</label>
          <select id="provider-demo" data-testid="provider-demo" value={providerDemo} onChange={e => setProviderDemo(e.target.value)}>
            <option value="normal">Normal — use available key</option>
            <option value="key-1">Fail API key 1 → switch to key 2</option>
            <option value="key-2">Fail API key 2 → keep key 1</option>
            <option value="all">Fail all API keys</option>
          </select>
        </div>
      </section>
      <section className="chat" aria-live="polite">
        {messages.length === 0 && <div className="empty"><h1>How can I help?</h1><p>Start a conversation below.</p></div>}
        {messages.map((item, index) => item.role === 'user'
          ? <div className="message user" key={index}>{item.content}</div>
          : <AssistantMessage key={index} data={item.error ? item.content : item.data} isError={item.error} />)}
      </section>
      <section className="composer">
        <textarea data-testid="chat-input" value={input} onChange={e => setInput(e.target.value)} onKeyDown={onKeyDown} placeholder="Message Lyftr AI" rows="2" />
        <button data-testid="send-button" onClick={sendMessage} disabled={sending || !input.trim()}>{sending ? 'Sending...' : 'Send'}</button>
      </section>
    </main>
  );
}

createRoot(document.getElementById('root')).render(<App />);
