/** Browser review API. Uses Craft login, same-origin POSTs, and a fixed Python bridge. */
import { spawn } from 'node:child_process'
import { join } from 'node:path'
import { validateSession } from '@craft-agent/server-core/webui'

type Operation = Record<string, unknown> & { operation: string }
type Envelope = { ok: true; result: unknown } | { ok: false; error: string }
const identifier = /^[a-f0-9]{32}$/

export function createIncidentApi(root: string, secret: string) {
  let active = 0
  const execute = async (operation: Operation): Promise<Envelope> => {
    if (active >= 8) return {ok: false, error: 'Too many concurrent review requests'}
    active++
    try {
      return await new Promise<Envelope>((resolve) => {
        const child = spawn(join(root, 'services/incident-tools/.venv/bin/python'),
          [join(root, 'services/incident-tools/admin.py')], {
            cwd: root, stdio: ['pipe', 'pipe', 'pipe'],
            // Admin bridge has no model credentials and never executes arbitrary commands.
            env: {PATH: process.env.PATH, PYTHONDONTWRITEBYTECODE: '1',
                  INCIDENT_DATABASE: join(root, '.incident/incident-ledger.sqlite3')},
          })
        let output = '', settled = false
        const finish = (value: Envelope) => {
          if (settled) return
          settled = true
          clearTimeout(timer)
          resolve(value)
        }
        const timer = setTimeout(() => {child.kill(); finish({ok:false,error:'Review operation timed out'})}, 45_000)
        child.on('error', () => finish({ok:false,error:'Review service unavailable'}))
        child.stdin.on('error', () => finish({ok:false,error:'Review service unavailable'}))
        child.stderr.resume()
        child.stdout.setEncoding('utf8')
        child.stdout.on('data', part => {
          output += part
          if (output.length > 2_000_000) { child.kill(); finish({ok:false,error:'Review result exceeds limit'}) }
        })
        child.on('exit', () => {
          try { finish(JSON.parse(output) as Envelope) }
          catch { finish({ok:false,error:'Invalid review service response'}) }
        })
        child.stdin.end(JSON.stringify(operation))
      })
    } finally { active-- }
  }
  return async (request: Request): Promise<Response | null> => {
    const url = new URL(request.url)
    if (!url.pathname.startsWith('/api/incident/')) return null
    const json = (value: unknown, status=200) => Response.json(value, {status, headers:{'Cache-Control':'no-store'}})
    if (!await validateSession(request.headers.get('cookie') ?? '', secret)) return json({error:'Login required'},401)
    let operation: Operation
    if (request.method === 'GET' && url.pathname === '/api/incident/cases') operation={operation:'cases'}
    else if (request.method === 'GET' && url.pathname === '/api/incident/investigations') operation={operation:'list'}
    else if (request.method === 'GET' && url.pathname === '/api/incident/actions') operation={operation:'actions'}
    else if (request.method === 'GET' && /^\/api\/incident\/investigations\/[a-f0-9]{32}$/.test(url.pathname)) {
      operation={operation:'get', investigation_id:url.pathname.split('/').at(-1)}
    } else if (request.method === 'POST') {
      if (request.headers.get('origin') !== url.origin || request.headers.get('x-incident-ui') !== '1') {
        return json({error:'Same-origin browser decision required'},403)
      }
      if (!request.headers.get('content-type')?.startsWith('application/json')) return json({error:'JSON required'},415)
      const raw = await request.text()
      if (raw.length > 45000) return json({error:'Request too large'},413)
      let body: Record<string,unknown>
      try {body=JSON.parse(raw); if (!body || Array.isArray(body) || typeof body !== 'object') throw Error()}
      catch {return json({error:'Invalid JSON object'},400)}
      if (url.pathname === '/api/incident/lab/experiments') {
        if (body.scenario !== 'pool' && body.scenario !== 'inventory') return json({error:'Unknown local experiment'},400)
        operation={operation:'prepare_lab',scenario:body.scenario}
      } else if (url.pathname === '/api/incident/proposals') {
        if (typeof body.investigation_id !== 'string' || !identifier.test(body.investigation_id)
            || typeof body.report_id !== 'string' || !identifier.test(body.report_id)) return json({error:'Invalid report reference'},400)
        operation={operation:'propose', investigation_id:body.investigation_id, report_id:body.report_id,
          action:body.action, rationale:body.rationale, evidence_ids:body.evidence_ids}
      } else if (url.pathname === '/api/incident/decisions') {
        if (typeof body.proposal_id !== 'string' || !identifier.test(body.proposal_id)) return json({error:'Invalid proposal ID'},400)
        operation={operation:'decide', proposal_id:body.proposal_id, decision:body.decision}
      } else return json({error:'Unknown incident route'},404)
    } else return json({error:'Unknown incident route'},404)
    const result = await execute(operation)
    return result.ok ? json(result.result) : json({error:result.error},400)
  }
}
