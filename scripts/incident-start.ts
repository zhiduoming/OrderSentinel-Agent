/** Launch the existing Craft headless server with isolated project configuration. */
import { readFileSync, existsSync } from 'node:fs'
import { resolve, dirname, delimiter } from 'node:path'
import { spawn } from 'node:child_process'
const root = resolve(import.meta.dir, '..')
const env = { ...process.env,
  PATH: `${dirname(process.execPath)}${delimiter}${process.env.PATH ?? ''}`,
  CRAFT_INCIDENT_ROOT: root,
  CRAFT_CONFIG_DIR: resolve(root, '.incident/config'),
  CRAFT_BUNDLED_ASSETS_ROOT: resolve(root, 'apps/electron'),
  CRAFT_RPC_HOST: '127.0.0.1',
  CRAFT_RPC_PORT: process.env.INCIDENT_PORT || '9100',
  CRAFT_WEBUI_DIR: resolve(root, 'apps/webui/dist'),
  CRAFT_SERVER_TOKEN: readFileSync(resolve(root, '.incident/server-token'), 'utf8').trim(),
}
if (!existsSync(resolve(root, 'apps/webui/dist/index.html'))) {
  throw new Error('Browser build missing. Run incident:build first.')
}
// Import model credentials before starting; do not log environment or key values.
const credentials = spawn(process.execPath, ['run', 'scripts/incident-credentials.ts'], {cwd: root, env, stdio: 'inherit'})
const code = await new Promise<number | null>(r => credentials.on('exit', r))
if (code !== 0) process.exit(code ?? 1)
console.log(`Open http://127.0.0.1:${env.CRAFT_RPC_PORT}; login password is in .incident/server-token.`)
const child = spawn(process.execPath, ['run', 'packages/server/src/index.ts'], {cwd: root, env, stdio: ['inherit', 'pipe', 'pipe']})
for (const [stream, output] of [[child.stdout, process.stdout], [child.stderr, process.stderr]] as const) {
  let pending = ''
  stream?.setEncoding('utf8')
  stream?.on('data', (part: string) => {
    pending += part
    let newline: number
    while ((newline = pending.indexOf('\n')) !== -1) {
      let line = pending.slice(0, newline)
      pending = pending.slice(newline + 1)
      for (const secret of [env.CRAFT_SERVER_TOKEN, process.env.BAILIAN_API_KEY, process.env.DEEPSEEK_API_KEY]) {
        if (secret) line = line.split(secret).join('[redacted]')
      }
      output.write(line + '\n')
    }
  })
}
process.on('SIGINT', () => child.kill('SIGINT'))
process.on('SIGTERM', () => child.kill('SIGTERM'))
child.on('exit', exitCode => process.exit(exitCode ?? 1))
