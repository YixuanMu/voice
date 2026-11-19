import { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './style.css'

type Setting = number | boolean | string[]
type Window = { new: number; repeat: number; entries: number }
type Row = { name: string; pct: number; fast: number; falling: number; limits: number;
             ratio: number; leader?: string; followers?: number }
type Volume = { code: string; name: string; pct: number; ratio: number; minutes: number }
type Point = { ts: number; up: number; down: number }
type Market = Point & {
  auction?: boolean
  rising: number; falling: number; covered: number; expected: number; coverage: number
  reliable: boolean; limit_coverage: number; opened: number; resealed: number; suspended: number
  opened_stocks: number; resealed_stocks: number
  windows: Record<string, { up: Window; down: Window }>
  trend: Point[]; sectors: Row[]; volume: Volume[]
}
type Log = { type: string; received: number; ts?: number; text: string; error?: string }
type State = { source: string; status: string; delay: number | null; market: Market | null
               settings: Record<string, Setting>; log: Log[] }

const time = (seconds: number) => new Date(seconds * 1000).toLocaleTimeString('zh-CN', {
  timeZone: 'Asia/Shanghai', hour12: false,
})
const percent = (value: number) => (value > 0 ? '+' : '') + value.toFixed(2) + '%'
const statuses: Record<string, string> = {
  live: '数据更新中', connecting: '等待数据', closed: '休市', stale: '行情延迟',
  disconnected: '断线重试中', complete: '数据流已结束',
  auction: '开盘集合竞价', auction_wait: '集合竞价 · 等待虚拟撮合行情',
}
const labels: Record<string, string> = {
  gap_seconds: '连续样本最大间隔（秒）', late_seconds: '最大行情延迟（秒）', coverage: '市场最低覆盖率',
  rise_1m: '一分钟拉升（%）', rise_3m: '三分钟拉升（%）', fall_1m: '一分钟急跌（%）',
  fall_3m: '三分钟急跌（%）', amount_1m: '一分钟放量倍数', amount_3m: '三分钟放量倍数',
  min_amount: '异动最低区间成交额（元）', baseline_windows: '历史基线窗口数', sector_pct: '板块涨跌阈值（%）',
  sector_stock_pct: '板块个股涨跌阈值（%）', sector_members: '板块同向异动股数', sector_limits: '板块新增涨停股数',
  sector_amount: '板块放量倍数', sector_coverage: '板块最低覆盖率', sector_overlap: '板块去重重合率',
  sector_flow_wan: '板块主力净流入／流出阈值（万元）',
  sector_accumulation_wan: '板块增仓信号阈值（万元）',
  market_limit_alert: '五分钟首次触板播报阈值', cooldown: '事件冷却（秒）', merge_seconds: '播报合并（秒）',
  rate: '语速（−10 至 10）', volume: '音量（0 至 100）', paused: '暂停播报', watchlist: '自选股代码（逗号分隔）',
}

async function post(path: string, data?: Record<string, Setting>) {
  const response = await fetch('/api/' + path, { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data ?? {}) })
  if (!response.ok) throw new Error('请求失败（' + response.status + '）')
}

function Trend({ points }: { points: Point[] }) {
  const max = Math.max(1, ...points.map(point => Math.max(point.up, point.down)))
  const line = (key: 'up' | 'down') => points.map((point, index) =>
    (index / Math.max(1, points.length - 1) * 700) + ',' + (130 - point[key] / max * 118)).join(' ')
  return <div className="trend">
    <svg viewBox="0 0 700 140" role="img" aria-label="上涨和下跌家数走势图" preserveAspectRatio="none">
      {[20, 55, 90, 125].map(y => <line key={y} x1="0" x2="700" y1={y} y2={y} className="gridline" />)}
      <polyline points={line('up')} className="up-line" />
      <polyline points={line('down')} className="down-line" />
    </svg>
    <div className="time-axis"><span>{points[0] ? time(points[0].ts) : '—'}</span>
      <span>{points.at(-1) ? time(points.at(-1)!.ts) : '—'}</span></div>
  </div>
}

function Settings({ settings, notify }: {
  settings: State['settings']; notify: (message: string) => void
}) {
  const [draft, setDraft] = useState(settings)
  return <details className="panel settings">
    <summary>参数设置</summary>
    <form onSubmit={async event => {
      event.preventDefault()
      try { await post('settings', draft); notify('参数已保存') }
      catch (error) { notify((error as Error).message) }
    }}>
      <div className="fields">{Object.entries(draft).map(([key, value]) =>
        <label key={key}>{labels[key] ?? key}
          {typeof value === 'boolean' ?
            <input type="checkbox" checked={value} onChange={event =>
              setDraft({ ...draft, [key]: event.target.checked })} /> :
            Array.isArray(value) ?
              <input defaultValue={value.join(',')} onChange={event => setDraft({
                ...draft, [key]: event.target.value.split(',').map(s => s.trim()).filter(Boolean),
              })} /> :
              <input type="number" step="any" required defaultValue={value} onChange={event =>
                setDraft({ ...draft, [key]: event.target.valueAsNumber })} />}
        </label>)}</div>
      <button type="submit">保存参数</button>
    </form>
  </details>
}

function Timeline({ entries }: { entries: Log[] }) {
  if (!entries.length) return <p className="empty">暂无记录</p>
  return <ul className="timeline">{entries.map((item, index) =>
    <li key={item.received + '-' + index}><time>{time(item.ts ?? item.received)}</time>
      <span>{item.text}{item.error && <small>{item.error}</small>}</span></li>)}</ul>
}

function Dashboard() {
  const [state, setState] = useState<State>()
  const [connected, setConnected] = useState(false)
  const [message, setMessage] = useState('')
  useEffect(() => {
    let active = true
    let timer: ReturnType<typeof setTimeout>
    let socket: WebSocket
    function connect() {
      socket = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws')
      socket.onopen = () => setConnected(true)
      socket.onmessage = event => setState(JSON.parse(event.data))
      socket.onclose = () => {
        if (active) {
          setConnected(false)
          timer = setTimeout(connect, 1200)
        }
      }
    }
    connect()
    return () => { active = false; clearTimeout(timer); socket.close() }
  }, [])

  const market = state?.market
  const source = state && ({ mock: '模拟行情', replay: '回放行情', tdx: '通达信实盘' }[state.source])
  async function audio(test: boolean) {
    try {
      if (test) { await post('test'); setMessage('测试语音已入队，结果见播报记录') }
      else if (state) { await post('settings', { paused: !state.settings.paused }); setMessage('语音状态已更新') }
    } catch (error) { setMessage((error as Error).message) }
  }

  return <main>
    <header>
      <div><div className="connection"><span className={connected ? 'online' : 'offline'} />
        {connected && state ? statuses[state.status] ?? state.status : '连接监控服务中'}</div>
        <h1>A 股行情监控</h1></div>
      <div className="header-actions">
        <button disabled={!state || !!state.settings.paused} onClick={() => audio(true)}>测试中文语音</button>
        <button disabled={!state} onClick={() => audio(false)}>{state?.settings.paused ? '恢复播报' : '暂停播报'}</button>
      </div>
    </header>
    <div className="statusbar">
      <strong className={state?.source === 'tdx' ? 'real' : 'sim'}>{source ?? '载入数据源'}</strong>
      <span>行情时间 {market ? time(market.ts) : '—'}</span>
      <span>数据延迟 {state?.delay == null ? '—' : state.delay.toFixed(1) + ' 秒'}</span>
      <span>覆盖 {market ? market.covered + ' / ' + market.expected : '—'}</span>
      {market && <span>涨跌停字段覆盖 {(market.limit_coverage * 100).toFixed(1)}% · 停牌 {market.suspended}</span>}
      {market?.auction && <strong className="sim">虚拟撮合价，涨跌停统计从连续交易开始</strong>}
      {market && !market.auction && (!market.reliable || state?.status !== 'live' || !connected) && <strong className="sim">统计可能不完整或已过期</strong>}
      <span role="status">{message}</span>
    </div>
    <section className="metrics">
      {[
        ['当前涨停', market?.up, 'up'], ['当前跌停', market?.down, 'down'],
        ['五分钟新增涨停', market?.windows[5].up.new, 'up'],
        ['五分钟新增跌停', market?.windows[5].down.new, 'down'],
        ['涨停开板股数', market?.opened_stocks, 'neutral'], ['重新封板股数', market?.resealed_stocks, 'neutral'],
      ].map(([label, value, color]) => <article className="panel metric" key={label}>
        <span>{label}</span><strong className={String(color)}>{value ?? '—'}</strong>
      </article>)}
    </section>
    <div className="grid">
      <section className="panel"><h2>市场情绪</h2>
        <div className="legend"><span className="up">上涨 {market?.rising ?? '—'}</span>
          <span className="down">下跌 {market?.falling ?? '—'}</span></div>
        <Trend points={market?.trend ?? []} />
      </section>
      <section className="panel"><h2>涨跌停变化</h2>
        <div className="legend">日内开板 {market?.opened ?? '—'} 次 · 回封 {market?.resealed ?? '—'} 次</div>
        <table><thead><tr><th>窗口</th><th>涨停首次</th><th>涨停重复</th><th>跌停首次</th><th>跌停重复</th></tr></thead>
          <tbody>{[1, 3, 5, 10].map(m => <tr key={m}><td>{m} 分钟</td>
            <td className="up">{market?.windows[m].up.new ?? '—'}</td><td>{market?.windows[m].up.repeat ?? '—'}</td>
            <td className="down">{market?.windows[m].down.new ?? '—'}</td><td>{market?.windows[m].down.repeat ?? '—'}</td>
          </tr>)}</tbody></table>
      </section>
      <section className="panel"><h2>板块异动排行</h2>
        {market?.sectors.length ? <table><thead><tr><th>板块</th><th>3 分钟</th><th>急涨 / 急跌</th><th>触板</th><th>成交额倍数</th></tr></thead>
          <tbody>{market.sectors.map(row => <tr key={row.name}><td>{row.name}
            {row.leader && <small className="leader">{row.leader}领涨 · {row.followers} 只跟涨</small>}</td>
            <td className={row.pct < 0 ? 'down' : 'up'}>{percent(row.pct)}</td>
            <td>{row.fast} / {row.falling}</td><td>{row.limits}</td><td>{row.ratio ? row.ratio + '×' : '—'}</td>
          </tr>)}</tbody></table> : <p className="empty">等待板块窗口数据</p>}
      </section>
      <section className="panel"><h2>个股异常放量排行</h2>
        {market?.volume.length ? <table><thead><tr><th>股票</th><th>窗口</th><th>涨幅</th><th>成交额倍数</th></tr></thead>
          <tbody>{market.volume.map(row => <tr key={row.code + row.minutes}><td title={row.code}>{row.name}</td>
            <td>{row.minutes} 分钟</td><td className={row.pct < 0 ? 'down' : 'up'}>{percent(row.pct)}</td>
            <td>{row.ratio}×</td>
          </tr>)}</tbody></table> : <p className="empty">当前没有达到阈值的个股</p>}
      </section>
      <section className="panel"><h2>实时事件</h2>
        <Timeline entries={state?.log.filter(item => ['event', 'connection'].includes(item.type)) ?? []} />
      </section>
      <section className="panel"><h2>播报记录</h2>
        <Timeline entries={state?.log.filter(item => item.type.startsWith('speech')) ?? []} />
      </section>
    </div>
    {state && <Settings key={JSON.stringify(state.settings)} settings={state.settings} notify={setMessage} />}
  </main>
}

createRoot(document.getElementById('root')!).render(<Dashboard />)
