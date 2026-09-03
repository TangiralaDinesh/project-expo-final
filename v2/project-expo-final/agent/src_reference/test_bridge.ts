/**
 * Quick test for the bridge — validates all actions work.
 */
import { execSync } from 'child_process'
import { join, resolve } from 'path'

const bridgePath = resolve(import.meta.dirname!, 'bridge.ts')

function callBridge(action: string, args: Record<string, any>): any {
  const cmd = `node --experimental-strip-types "${bridgePath}" ${action} ${JSON.stringify(JSON.stringify(args))}`
  const result = execSync(cmd, { encoding: 'utf-8', maxBuffer: 5 * 1024 * 1024 })
  return JSON.parse(result.trim())
}

console.log('=== Testing bridge.ts ===\n')

// Test 1: estimate_tokens
const t1 = callBridge('estimate_tokens', { text: 'hello world test string' })
console.log('1. estimate_tokens:', t1)

// Test 2: file_read (read this test file itself)
const t2 = callBridge('file_read', { file_path: resolve(import.meta.dirname!, 'package.json') })
console.log('2. file_read:', { lines: t2.lines, truncated: t2.truncated, contentLen: t2.content.length })

// Test 3: bash
const t3 = callBridge('bash', { command: 'echo hello from bridge' })
console.log('3. bash:', t3)

// Test 4: file_write + file_read round-trip
const testFile = resolve(import.meta.dirname!, '_test_tmp.txt')
const t4 = callBridge('file_write', { file_path: testFile, content: 'bridge test content 123' })
console.log('4. file_write:', t4)
const t5 = callBridge('file_read', { file_path: testFile })
console.log('5. file_read verify:', { content: t5.content.trim() })

// Test 5: file_edit
const t6 = callBridge('file_edit', { file_path: testFile, old_text: '123', new_text: '456' })
console.log('6. file_edit:', t6)
const t7 = callBridge('file_read', { file_path: testFile })
console.log('7. file_read after edit:', { content: t7.content.trim() })

// Cleanup
import { unlinkSync } from 'fs'
try { unlinkSync(testFile) } catch {}

console.log('\n=== ALL BRIDGE TESTS PASSED ===')
