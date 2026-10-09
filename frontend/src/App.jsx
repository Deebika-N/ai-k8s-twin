import { useRef, useState } from 'react'
import './App.css'

const environments = [
  ['Pod Kill', 'pod-kill', 'Terminate a pod and observe recovery.'],
  ['CPU Stress', 'cpu-stress', 'Saturate processor capacity.'],
  ['Memory Stress', 'memory-stress', 'Apply memory pressure.'],
  ['Network Delay', 'network-delay', 'Add latency between workloads.'],
  ['Network Loss', 'network-packet-loss', 'Drop packets and measure retries.'],
  ['Network Partition', 'network-partition', 'Isolate a service.'],
  ['Node Failure', 'node-failure', 'Simulate a node outage.'],
]

const defaultForm = {
  github_url: '',
  cpu: '2',
  memory: '4Gi',
  vus: '10',
  duration: '30s',
}

function App() {
  const [form, setForm] = useState(defaultForm)
  const [selected, setSelected] = useState(environments.map(([name]) => name))
  const [logs, setLogs] = useState([['info', 'Console ready. Configure an environment run to begin.']])
  const [running, setRunning] = useState(false)
  const controllerRef = useRef(null)

  const addLog = (type, message) => {
    const lines = message.split(/\r?\n/).filter(Boolean)
    setLogs((current) => [...current, ...lines.map((line) => [type, line])])
  }

  const update = (event) => {
    const { name, value } = event.target
    setForm((current) => ({ ...current, [name]: value }))
  }

  const toggleEnvironment = (name) => {
    setSelected((current) => current.includes(name)
      ? current.filter((item) => item !== name)
      : [...current, name])
  }

  const runExperiment = async (event) => {
    event.preventDefault()
    if (running) return
    setRunning(true)
    controllerRef.current = new AbortController()
    setLogs([])
    addLog('command', '$ POST /api/environment-orchestrator/run')

    try {
      const response = await fetch('/api/environment-orchestrator/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...form, environments: selected }),
        signal: controllerRef.current.signal,
      })
      if (!response.ok) throw new Error(`Backend returned HTTP ${response.status}`)
      if (!response.body) throw new Error('Backend did not return a stream')

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      while (true) {
        const { value, done } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const messages = buffer.split('\n\n')
        buffer = messages.pop() || ''
        messages.forEach((message) => {
          const data = message.split('\n').find((line) => line.startsWith('data: '))
          const event = message.split('\n').find((line) => line.startsWith('event: '))?.slice(7) || 'output'
          if (!data) return
          const payload = JSON.parse(data.slice(6))
          if (event === 'output') addLog('info', payload.text)
          if (event === 'result') addLog('success', 'Environment simulation completed.')
          if (event === 'error') addLog('error', `Environment simulation failed: ${payload.message}`)
        })
      }
    } catch (error) {
      if (error.name !== 'AbortError') addLog('error', error.message)
    } finally {
      controllerRef.current = null
      setRunning(false)
    }
  }

  const stopExperiment = () => {
    if (!controllerRef.current) return
    addLog('command', '$ STOP environment orchestrator')
    controllerRef.current.abort()
  }

  return (
    <main className="app-shell">
      <header className="topbar"><div className="brand-lockup"><div className="brand-mark">AK</div><div><strong>AI K8s Twin</strong><span>resilience control plane</span></div></div><div className="connection"><span className="pulse" />LOCAL CLUSTER <b>dev</b></div></header>
      <section className="intro"><div><p className="eyebrow">ENVIRONMENT ORCHESTRATOR / 01</p><h1>Chaos, made <em>observable.</em></h1><p className="lede">Provide the workload inputs, run the orchestrator, and watch every terminal message arrive from the backend.</p></div></section>
      <form className="workspace" onSubmit={runExperiment}>
        <div className="experiments-panel panel"><div className="panel-heading"><div><span className="section-number">01</span><h2>Run configuration</h2></div></div>
          <label>GitHub repository URL<input name="github_url" value={form.github_url} onChange={update} placeholder="https://github.com/org/repository" required /></label>
          <div className="input-grid"><label>Available CPU<input name="cpu" value={form.cpu} onChange={update} required /></label><label>Available memory<input name="memory" value={form.memory} onChange={update} required /></label><label>k6 VUs<input name="vus" type="number" min="1" value={form.vus} onChange={update} required /></label><label>Duration<input name="duration" value={form.duration} onChange={update} required /></label></div>
          <div className="panel-heading environment-heading"><div><span className="section-number">02</span><h2>Environment sequence</h2></div><span className="count-label">{selected.length} selected</span></div>
          <div className="environment-options">{environments.map(([name, shortName, description]) => <label className="environment-option" key={name}><input type="checkbox" checked={selected.includes(name)} onChange={() => toggleEnvironment(name)} /><span><strong>{name}</strong><small>{description}</small></span><code>{shortName}.yaml</code></label>)}</div>
          <div className="run-actions">
            <button className="run-button" type="submit" disabled={running || selected.length === 0}><span>{running ? 'Orchestrator running' : 'Run environment orchestrator'}</span><b>{running ? '...' : '->'}</b></button>
            {running && <button className="stop-button" type="button" onClick={stopExperiment}>Stop execution <b>×</b></button>}
          </div>
        </div>
      </form>
      <section className="terminal-panel panel"><div className="terminal-heading"><div><span className="section-number">03</span><h2>Terminal output</h2></div><span className="terminal-path">backend stream <i>{running ? '● connected' : '● ready'}</i></span></div><div className="terminal-window"><div className="terminal-bar"><span /><span /><span /><strong>environment-orchestrator</strong></div><div className="terminal-content">{logs.map(([type, message], index) => <div className={`log-line log-${type}`} key={`${index}-${message}`}><span className="log-symbol">{type === 'command' ? '$' : type === 'success' ? '+' : type === 'error' ? '!' : '>'}</span><span>{message}</span></div>)}{running && <div className="log-line log-cursor"><span className="log-symbol">&gt;</span><span className="typing">waiting for backend output<span>_</span></span></div>}</div></div></section>
      <footer><span>AI K8s Twin</span><span>environment orchestration laboratory</span><span>v0.1 / local</span></footer>
    </main>
  )
}

export default App
