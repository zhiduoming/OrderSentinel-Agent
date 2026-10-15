import { useEffect, useRef, useState } from 'react'
import { navigate, routes } from '@/lib/navigate'
import type { LlmConnectionWithStatus } from '@craft-agent/shared/config/llm-connections'
import './incident.css'

type Case = {id:string;title:string;time:string;summary:string;signal:string;details:string[];live?:boolean}
type CaseListing = {lab_status:string;active_case?:string;cases:{case_id:string;title:string;alert:string;data_mode:string}[]}
const syntheticCases: Case[] = [
  { id: 'INC-001', title: '提交订单错误率升高', time: '09月28日 14:03',
    summary: '新旧实例表现不同，需要核对指标、错误日志和近期变更。',
    signal: '18% 错误率', details: ['提交订单接口异常', '新旧实例存在差异', '数据库状态需要核查'] },
  { id: 'INC-002', title: '订单接口持续超时', time: '09月29日 10:15',
    summary: '近期有订单版本发布，同时需要调查下游服务，避免过早归因。',
    signal: '5.2秒 P95', details: ['多个版本均有慢请求', '近期存在发布记录', '下游状态需要核查'] },
  { id: 'INC-003', title: '偶发超时，观测不完整', time: '09月30日 16:00',
    summary: '指标确认异常，但部分数据源不可用；调查应保留不确定性。',
    signal: '8% 错误率', details: ['请求时延升高', '部分观测服务不可用', '需要区分事实与猜测'] },
]

export default function IncidentLauncher() {
  const [open, setOpen] = useState(false)
  const [selected, setSelected] = useState(0)
  const [cases,setCases] = useState<Case[]>(syntheticCases)
  const [labReady,setLabReady] = useState(false)
  const [connections, setConnections] = useState<LlmConnectionWithStatus[]>([])
  const [connectionSlug, setConnectionSlug] = useState('bailian-token-plan')
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const dialog = useRef<HTMLDialogElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!open) return
    let active = true
    setError('')
    setLoading(true)
    Promise.all([window.electronAPI.listLlmConnectionsWithStatus(), fetch("/api/incident/cases").then(async r => {if(!r.ok)throw Error("实验列表加载失败");return await r.json() as CaseListing})]).then(([items, listing]) => {
      if (!active) return
      setConnections(items)
      setLabReady(listing.lab_status === "ready")
      const live = listing.cases.find(item=>item.case_id===listing.active_case)
      setCases(live?[...syntheticCases,toLive(live)]:syntheticCases)
      setSelected(0)
      const usable = items.find(item => item.isAuthenticated)
      if (usable) setConnectionSlug(current=>items.some(item=>item.slug===current&&item.isAuthenticated)?current:usable.slug)
    }).catch(() => {
      if (active) setError('暂时无法读取模型连接，请等待服务连接后重新打开。')
    }).finally(() => { if (active) setLoading(false) })
    dialog.current?.showModal()
    return () => { active = false; dialog.current?.close() }
  }, [open])

  const toLive = (item:{case_id:string;title:string;alert:string}):Case => ({id:item.case_id,title:item.title,
    time:'本地实测实验',summary:item.alert,signal:'实际请求',
    details:['指标由压测请求采集','错误日志和 trace_id 来自运行服务','审批后修改本地配置并重新压测'],live:true})
  const prepare = async (scenario:'pool'|'inventory') => {
    setBusy(true);setError('')
    try {
      const response=await fetch('/api/incident/lab/experiments',{method:'POST',credentials:'same-origin',
        headers:{'Content-Type':'application/json','X-Incident-UI':'1'},body:JSON.stringify({scenario})})
      const value=await response.json()
      if(!response.ok)throw new Error(value.error||'实验创建失败')
      setCases([...syntheticCases,toLive(value)]);setSelected(syntheticCases.length)
    }catch(err){setError(err instanceof Error?err.message:'实验创建失败')}
    finally{setBusy(false)}
  }
  const selectedCase = cases[selected] || syntheticCases[0]
  const connection = connections.find(item => item.slug === connectionSlug)
  const close = () => { if (!busy) { setOpen(false); trigger.current?.focus() } }

  const start = async () => {
    if (!connection?.isAuthenticated || busy) return
    setBusy(true)
    setError('')
    let createdSessionId: string | undefined
    try {
      const workspaces = await window.electronAPI.getWorkspaces()
      const workspace = workspaces.find(item => item.id === 'incident-demo')
      if (!workspace) throw new Error('未找到故障调查工作区，请先运行 incident:setup。')
      const session = await window.electronAPI.createSession(workspace.id, {
        name: `${selectedCase.id} ${selectedCase.title}`,
        permissionMode: 'safe',
        workingDirectory: 'none',
        llmConnection: connection.slug,
        model: connection.defaultModel,
        enabledSourceSlugs: ['incident-observability'],
      })
      createdSessionId = session.id
      await window.electronAPI.sendMessage(session.id,
        `请使用 [skill:incident-investigator] 调查${selectedCase.live?"实际本地实验":"合成案例"} ${selectedCase.id}：${selectedCase.title}。` +
        '先读取 Skill 和 Source 指南，再通过 incident-observability 工具创建调查并查询数据。' +
        '区分事实、候选原因、反证和缺失信息，调用 submit_report 保存报告并解释证据。' +
        '成功保存后查询固定动作目录并建立待审批提案，提醒用户在报告与审批中复核。' +
        '不要直接读取 fixture 或评测答案。Agent 只能创建提案，配置执行必须由用户通过浏览器审批。使用中文解释。')
      setOpen(false)
      navigate(routes.view.allSessions(session.id))
    } catch (err) {
      const message = err instanceof Error ? err.message : '创建调查失败，请检查服务和模型配置。'
      setError(createdSessionId ? `${message} 会话已创建，请进入会话检查，避免重复提交。` : message)
      if (createdSessionId) navigate(routes.view.allSessions(createdSessionId))
    } finally {
      setBusy(false)
    }
  }

  return <>
    <button ref={trigger} className="incident-trigger" onClick={() => setOpen(true)}>故障调查</button>
    <dialog ref={dialog} className="incident-dialog" onCancel={event => { event.preventDefault(); close() }}>
      <header className="incident-heading">
        <div><span className="incident-service">订单服务 / 故障实验</span><h1>开始一次故障调查</h1></div>
        <button className="incident-close" onClick={close} disabled={busy} aria-label="关闭故障调查">×</button>
      </header>
      <p className="incident-description">从告警开始，查看 Agent 如何查询证据、验证原因并提出下一步建议。</p>
      <section className="incident-lab-controls" aria-label="本地业务实验">
        <h2>本地真实业务实验</h2><p>生成正常基线，注入故障并压测。创建新实验会使旧实验的待审批提案失效。</p>
        <div><button disabled={busy||loading||!labReady} onClick={()=>prepare('pool')}>创建连接池故障实验</button>
          <button disabled={busy||loading||!labReady} onClick={()=>prepare('inventory')}>创建库存并发故障实验</button></div>
        {busy&&<p role="status">正在处理请求，请等待完成…</p>}
        {!labReady&&<p>实验服务未启动。运行 incident:lab 后重新打开面板。</p>}
      </section>
      <div className="incident-layout">
        <nav className="incident-cases" aria-label="故障案例">
          {cases.map((item, index) => <button key={item.id} className={`incident-case ${index === selected ? 'selected' : ''}`}
            onClick={() => setSelected(index)} aria-pressed={index === selected} disabled={busy}>
            <span className="incident-case-top"><strong>{item.live?`LIVE-${item.id.slice(5,13)}`:item.id}</strong><span>{item.signal}</span></span>
            <span className="incident-case-title">{item.title}</span><small>{item.time}</small>
          </button>)}
        </nav>
        <section className="incident-preview" aria-label="案例详情">
          <span className="incident-severity">{selectedCase.live?'本地实验告警':'P1 演示告警'}</span>
          <h2>{selectedCase.title}</h2><p>{selectedCase.summary}</p>
          <ul>{selectedCase.details.map(detail => <li key={detail}>{detail}</li>)}</ul>
          <div className="incident-result"><h3>调查交付</h3><p>影响范围、候选原因、正反证据、缺失信息和处置建议。</p></div>
        </section>
      </div>
      <footer className="incident-footer">
        <label>调查模型<select value={connectionSlug} onChange={event => setConnectionSlug(event.target.value)} disabled={busy || loading}>
          {connections.length ? connections.map(item => <option key={item.slug} value={item.slug}>{item.name} · {item.defaultModel}{item.isAuthenticated ? '' : '（未配置密钥）'}</option>) : <option>正在读取连接…</option>}
        </select></label>
        <button className="incident-start" onClick={start} disabled={busy || loading || !connection?.isAuthenticated}>
          {busy ? '正在创建调查…' : '开始调查'}
        </button>
      </footer>
      {!loading && !connection?.isAuthenticated && <p className="incident-notice">先在模型设置中配置密钥，或填入本地 .env.local 后重启。密钥不会进入浏览器页面。</p>}
      {error && <p className="incident-error" role="alert">{error}</p>}
      <p className="incident-disclosure">INC 案例使用合成数据；LIVE 实验使用实际本地请求。LIVE 提案批准后会修改本地实验配置并重新压测。</p>
    </dialog>
  </>
}
