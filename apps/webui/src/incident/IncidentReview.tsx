import { useEffect, useRef, useState } from 'react'
import './incident.css'

type Hypothesis = {description:string;confidence:string;supporting_evidence:string[];counter_evidence:string[]}
type Report = {verdict:string;summary:string;impact:string;recommended_next_step:string;missing_information:string[];hypotheses:Hypothesis[]}
type Measurement = {error_rate:number;p95_ms:number;requests?:number;errors?:number}
type Simulation = {mode?:string;execution_status?:string;verification:string;recovered:boolean;before:Measurement|null;after:Measurement|null;message:string}
type Proposal = {mode?:string;proposal_id:string;report_id:string;action:string;name:string;service:string;change:Record<string,number>;risk:string;rationale:string;evidence_ids:string[];status:string;result:Simulation|null}
type Investigation = {data_mode?:string;investigation_id:string;case_id:string;reports:{report_id:string;report:Report}[];proposals:Proposal[];calls:{sequence:number;tool:string;payload:{result?:{records?:Record<string,unknown>[]}};created_at:string}[]}
type Row = {investigation_id:string;case_id:string;created_at:string;reports:number;pending_proposals:number}
type Action = {action:string;name:string;risk:string}

async function api<T>(path:string, body?:unknown, signal?:AbortSignal):Promise<T> {
  const response = await fetch(`/api/incident/${path}`, {method:body?'POST':'GET',
    headers:body?{'Content-Type':'application/json','X-Incident-UI':'1'}:undefined,
    body:body?JSON.stringify(body):undefined, credentials:'same-origin', signal})
  const value = await response.json()
  if (!response.ok) throw new Error(value.error || '报告服务暂时不可用')
  return value as T
}

export default function IncidentReview() {
  const [open,setOpen]=useState(false)
  const [rows,setRows]=useState<Row[]>([])
  const [selected,setSelected]=useState('')
  const [data,setData]=useState<Investigation|null>(null)
  const [actions,setActions]=useState<Action[]>([])
  const [action,setAction]=useState('')
  const [loading,setLoading]=useState(false)
  const [busy,setBusy]=useState(false)
  const [error,setError]=useState('')
  const dialog=useRef<HTMLDialogElement>(null)
  const trigger=useRef<HTMLButtonElement>(null)
  const latest=data?.reports.at(-1)
  const refresh=async (signal?:AbortSignal) => {
    const [listing,catalogue]=await Promise.all([
      api<{investigations:Row[]}>('investigations',undefined,signal),
      api<{actions:Action[]}>('actions',undefined,signal)])
    if (signal?.aborted) return
    setRows(listing.investigations);setActions(catalogue.actions)
    setSelected(value=>listing.investigations.some(row=>row.investigation_id===value)
      ?value:listing.investigations[0]?.investigation_id||'')
  }
  useEffect(()=>{
    if (!open) return
    const controller=new AbortController()
    dialog.current?.showModal();setError('');setLoading(true)
    refresh(controller.signal).catch(err=>{if(!controller.signal.aborted)setError(String(err))})
      .finally(()=>{if(!controller.signal.aborted)setLoading(false)})
    return ()=>{controller.abort();dialog.current?.close()}
  },[open])
  useEffect(()=>{
    if (!open || !selected) {setData(null);return}
    const controller=new AbortController()
    setData(null);setAction('');setError('')
    api<Investigation>(`investigations/${selected}`,undefined,controller.signal)
      .then(value=>{if(!controller.signal.aborted)setData(value)})
      .catch(err=>{if(!controller.signal.aborted)setError(String(err))})
    return ()=>controller.abort()
  },[open,selected])
  const close=()=>{if(!busy){setOpen(false);trigger.current?.focus()}}
  const mutate=async (path:string,body:unknown)=>{
    setBusy(true);setError('')
    try {
      await api(path,body)
      setData(await api<Investigation>(`investigations/${selected}`))
      await refresh()
    }catch(err){setError(err instanceof Error?err.message:'操作失败，请刷新确认状态后再重试')}
    finally{setBusy(false)}
  }
  const evidence=new Map<string,Record<string,unknown>>()
  data?.calls.forEach(call=>call.payload.result?.records?.forEach(record=>{
    if(typeof record.evidence_id==='string')evidence.set(record.evidence_id,record)
  }))
  const showEvidence=(id:string)=><details key={id} className="incident-evidence"><summary>{id}</summary>
    <pre>{JSON.stringify(evidence.get(id)??{error:'未在查询记录中找到'},null,2)}</pre></details>

  return <>
    <button ref={trigger} className="incident-review-trigger" onClick={()=>setOpen(true)}>报告与审批</button>
    <dialog ref={dialog} className="incident-dialog incident-review" onCancel={e=>{e.preventDefault();close()}}>
      <header className="incident-heading"><div><span className="incident-service">调查记录 / 证据与执行</span><h1>证据报告与处置审批</h1></div>
        <button className="incident-close" aria-label="关闭报告面板" disabled={busy} onClick={close}>×</button></header>
      <p className="incident-description">复核证据和候选原因。INC 执行合成模拟；LIVE 修改本地实验服务并用实际请求验证。</p>
      <div className="incident-review-selector"><label>调查记录<select value={selected} disabled={busy||loading}
        onChange={e=>setSelected(e.target.value)}>
        {!rows.length&&<option value="">暂无调查</option>}
        {rows.map(row=><option key={row.investigation_id} value={row.investigation_id}>
          {row.case_id} · {row.investigation_id.slice(0,8)} · {row.reports}份报告 · {row.pending_proposals}项待审批</option>)}
      </select></label><button disabled={busy||loading} onClick={async()=>{
        setLoading(true);setError('')
        try{await refresh();if(selected)setData(await api<Investigation>(`investigations/${selected}`))}
        catch(err){setError(String(err))}finally{setLoading(false)}
      }}>刷新记录</button></div>
      {error&&<p role="alert" className="incident-error">{error}</p>}
      {!rows.length&&!loading&&<p className="incident-notice">先完成一次故障调查，保存的报告会出现在这里。</p>}
      {data&&!latest&&<p className="incident-notice">调查已建立，尚无已保存报告。可返回会话继续调查后刷新。</p>}
      {latest&&<section className="incident-report-body" aria-label="已保存调查报告">
        <div className="incident-report-meta"><strong>{data?.case_id}</strong><span>
          {latest.report.verdict==='insufficient_evidence'?'证据不足':'存在较可信候选原因'}</span></div>
        <h2>调查结论</h2><p>{latest.report.summary}</p><h3>影响范围</h3><p>{latest.report.impact}</p>
        {latest.report.hypotheses.map((hypothesis,index)=><section key={index} className="incident-hypothesis">
          <h3>候选 {index+1} · 置信度 {({high:'高',medium:'中',low:'低'} as Record<string,string>)[hypothesis.confidence]}</h3>
          <p>{hypothesis.description}</p><h4>支持证据 · 点击查看原始观测</h4>
          {hypothesis.supporting_evidence.map(showEvidence)}
          <h4>反证</h4>{hypothesis.counter_evidence.length?hypothesis.counter_evidence.map(showEvidence):<p>此假设未提交反证引用，需要人工核查。</p>}
        </section>)}
        <h3>缺失信息</h3>{latest.report.missing_information.length?<ul>{latest.report.missing_information.map((item,i)=><li key={i}>{item}</li>)}</ul>:<p>报告未列出缺失项，仍需核查结论。</p>}
        <h3>下一步建议</h3><p>{latest.report.recommended_next_step}</p>
        <p className="incident-disclosure">报告 ID：{latest.report_id}。引用门禁检查证据存在与读取记录，不自动证明因果正确。</p>
        {!data?.proposals.some(p=>p.report_id===latest.report_id)&&<div className="incident-draft">
          <h3>创建待审批提案</h3><p>可以由 Agent 创建，也可以在复核报告后选择动作。此处只保存提案，不执行。</p>
          <select aria-label="处置动作" value={action} disabled={busy} onChange={e=>setAction(e.target.value)}>
            <option value="">选择与证据相符的动作</option>
            {actions.filter(a=>latest.report.verdict!=='insufficient_evidence'||a.action==='collect_missing_observations')
              .map(a=><option key={a.action} value={a.action}>{a.name}</option>)}
          </select><button disabled={busy||!action} onClick={()=>mutate('proposals',{
            investigation_id:selected,report_id:latest.report_id,action,
            rationale:latest.report.recommended_next_step,
            evidence_ids:latest.report.hypotheses.flatMap(h=>h.supporting_evidence)})}>保存待审批提案</button>
        </div>}
      </section>}
      {data?.proposals.map(proposal=><section key={proposal.proposal_id} className="incident-proposal" aria-label="处置提案">
        <h2>{proposal.name}</h2><p><strong>目标服务：</strong>{proposal.service} · <strong>状态：</strong>
          {({pending:'待审批',approved:'已批准',rejected:'已拒绝'} as Record<string,string>)[proposal.status]}</p>
        <p><strong>变更：</strong>{Object.keys(proposal.change).length?JSON.stringify(proposal.change):'建立补证任务'}</p>
        <p><strong>提案依据：</strong>{proposal.rationale}</p><p><strong>风险：</strong>{proposal.risk}</p>
        <p className="incident-disclosure">报告 {proposal.report_id} · 引用 {proposal.evidence_ids.join('、')}</p>
        {proposal.status==='pending'&&<><p className="incident-notice">{proposal.mode==='local_live'?'批准后将修改本地实验配置，并重新发送 120 次请求验证。若实验已变化，提案会被拒绝执行。':'批准后运行合成数据模拟。'}</p>
          <div className="incident-decision"><button className="incident-start" disabled={busy} onClick={()=>mutate('decisions',{proposal_id:proposal.proposal_id,decision:'approve'})}>{proposal.mode==='local_live'?'批准并执行本地实验':'批准并运行本地模拟'}</button>
            <button disabled={busy} onClick={()=>mutate('decisions',{proposal_id:proposal.proposal_id,decision:'reject'})}>拒绝提案</button></div></>}
        {proposal.status==='approved'&&proposal.mode==='local_live'&&['waiting','running','unknown'].includes(proposal.result?.execution_status||'')&&
          <button disabled={busy} onClick={()=>mutate('decisions',{proposal_id:proposal.proposal_id,decision:'approve'})}>核对同一操作的执行结果</button>}
        {proposal.result&&<div className="incident-simulation"><h3>{proposal.mode==='local_live'?'本地实测验证结果':'模拟验证结果'}</h3>
          <p>{proposal.result.verification==='improved'?'指标改善':proposal.result.verification==='no_improvement'?'未改善，不能声明恢复':proposal.result.verification==='stale_proposal'?'实验已变化，提案未执行':proposal.result.message}</p>
          {proposal.result.before&&proposal.result.after&&<table><thead><tr><th>指标</th><th>处置前</th><th>处置后</th></tr></thead><tbody>
            <tr><td>错误率</td><td>{(proposal.result.before.error_rate*100).toFixed(1)}%</td><td>{(proposal.result.after.error_rate*100).toFixed(1)}%</td></tr>
            <tr><td>P95 延迟</td><td>{proposal.result.before.p95_ms} ms</td><td>{proposal.result.after.p95_ms} ms</td></tr>
            {proposal.result.after.requests!==undefined&&<tr><td>请求数</td><td>{proposal.result.before.requests}</td><td>{proposal.result.after.requests}</td></tr>}
          </tbody></table>}<p className="incident-disclosure">{proposal.result.message}</p></div>}

      </section>)}
      {data&&<details className="incident-call-history"><summary>查看 {data.calls.length} 条业务工具及审批记录</summary>
        <ol>{data.calls.map(call=><li key={call.sequence}>{call.tool} · {call.created_at}</li>)}</ol></details>}
    </dialog>
  </>
}
