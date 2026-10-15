/** Import optional local env keys into Craft's existing encrypted credential store. */
import { resolve } from 'node:path'
import { readFileSync, writeFileSync } from 'node:fs'
process.env.CRAFT_CONFIG_DIR = resolve(import.meta.dir, '../.incident/config')
const { getCredentialManager } = await import('../packages/shared/src/credentials/index')
const credentials = getCredentialManager()
for (const [slug, variable] of [['bailian-token-plan', 'BAILIAN_API_KEY'], ['deepseek-official', 'DEEPSEEK_API_KEY']] as const) {
  const value = process.env[variable]?.trim()
  if (value) {
    await credentials.setLlmApiKey(slug, value)
    console.log(`Imported credentials for ${slug}; value omitted.`)
  }
}

// Prefer a configured connection when the project default has no credential.
// Keep an existing authenticated user choice intact.
const configPath = resolve(import.meta.dir, '../.incident/config/config.json')
const workspacePath = resolve(import.meta.dir, '../.incident/workspaces/incident-demo/config.json')
const config = JSON.parse(readFileSync(configPath, 'utf8'))
if (!await credentials.getLlmApiKey(config.defaultLlmConnection ?? '')) {
  for (const slug of ['bailian-token-plan', 'deepseek-official']) {
    if (await credentials.getLlmApiKey(slug)) {
      const connection = config.llmConnections.find((item: {slug:string}) => item.slug === slug)
      if (!connection) continue
      config.defaultLlmConnection = slug
      writeFileSync(configPath, JSON.stringify(config, null, 2) + '\n')
      const workspace = JSON.parse(readFileSync(workspacePath, 'utf8'))
      if (!await credentials.getLlmApiKey(workspace.defaults?.defaultLlmConnection ?? '')) {
        workspace.defaults = {...workspace.defaults, defaultLlmConnection:slug, model:connection.defaultModel}
        writeFileSync(workspacePath, JSON.stringify(workspace, null, 2) + '\n')
      }
      console.log(`Project default selected: ${slug}; key value omitted.`)
      break
    }
  }
}
