import { beforeAll, describe, expect, test } from 'bun:test'
import { mkdtempSync, mkdirSync, symlinkSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { createIncidentApi } from '../packages/server/src/incident-api'
import { createSessionToken } from '../packages/server-core/src/webui/auth'
import { permissionsConfigCache } from '../packages/shared/src/agent/permissions-config'

const root=resolve(import.meta.dir,'..')
const temporary=mkdtempSync(join(tmpdir(),'incident-http-'))
mkdirSync(join(temporary,'services'))
symlinkSync(join(root,'services/incident-tools'),join(temporary,'services/incident-tools'))
const secret='isolated-test-secret-incident'
const handle=createIncidentApi(temporary,secret)
let cookie=''
beforeAll(async()=>{cookie=`craft_session=${await createSessionToken(secret)}`})
const request=(path:string,method='GET',body?:string,extra:Record<string,string>={})=>new Request(`http://127.0.0.1:9100/api/incident/${path}`,{
  method,headers:{cookie,...extra},body})
const headers={'origin':'http://127.0.0.1:9100','x-incident-ui':'1','content-type':'application/json'}

describe('Incident browser boundary',()=>{
  test('requires a valid Craft login before review or decisions',async()=>{
    const res=await handle(new Request('http://127.0.0.1:9100/api/incident/investigations'))
    expect(res?.status).toBe(401)
    expect((await handle(request('decisions','POST','{}',{...headers,cookie:'craft_session=forged'})))?.status).toBe(401)
  })
  test('rejects cross-origin or non-UI decisions',async()=>{
    expect((await handle(request('decisions','POST','{}',{...headers,origin:'http://evil.example'})))?.status).toBe(403)
    expect((await handle(request('decisions','POST','{}',{'origin':headers.origin,'content-type':'application/json'})))?.status).toBe(403)
  })
  test('rejects malformed bodies and arbitrary commands',async()=>{
    expect((await handle(request('decisions','POST','not-json',headers)))?.status).toBe(400)
    expect((await handle(request('decisions','POST','{}',{...headers,'content-type':'text/plain'})))?.status).toBe(415)
    expect((await handle(request('execute-shell','POST','{"command":"anything"}',headers)))?.status).toBe(404)
    expect((await handle(request('decisions','POST','{"proposal_id":"../../file"}',headers)))?.status).toBe(400)
  })
  test('rejects unlisted experiment scenarios and unauthorized experiment creation',async()=>{
    expect((await handle(request('lab/experiments','POST','{"scenario":"shell"}',headers)))?.status).toBe(400)
    expect((await handle(request('lab/experiments','POST','{"scenario":"pool"}',{...headers,origin:'http://evil.example'})))?.status).toBe(403)
  })
  test('can read an actual isolated SQLite ledger through the Python bridge',async()=>{
    const res=await handle(request('investigations'))
    expect(res?.status).toBe(200)
    expect(await res?.json()).toEqual({investigations:[]})
    const unknown=await handle(request(`investigations/${'0'.repeat(32)}`))
    expect(unknown?.status).toBe(400)
    expect(await unknown?.json()).toEqual({error:'Unknown investigation_id'})
  })
  test('delegates unrelated Craft routes',async()=>{
    expect(await handle(new Request('http://127.0.0.1:9100/api/config'))).toBeNull()
  })
})

test('actual Craft permission merger admits business tools but no approval or shell tool',()=>{
  const merged=permissionsConfigCache.getMergedConfig({workspaceRootPath:join(root,'.incident/workspaces/incident-demo'),activeSourceSlugs:['incident-observability']})
  for(const name of ['list_cases','start_investigation','query_metrics','search_logs','query_traces','query_deployments','search_history','submit_report','get_investigation','mitigation_actions','propose_mitigation']){
    expect(merged.readOnlyMcpPatterns.some(pattern=>pattern.test(`mcp__incident-observability__${name}`))).toBe(true)
  }
  for(const name of ['mcp__incident-observability__approve_proposal','mcp__incident-observability__execute_shell','mcp__other__propose_mitigation']){
    expect(merged.readOnlyMcpPatterns.some(pattern=>pattern.test(name))).toBe(false)
  }
})
