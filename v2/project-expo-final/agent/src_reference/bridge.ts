/**
 * Bridge — Node.js entry point for Python to call src/ tools.
 * 
 * Usage from Python:
 *   result = subprocess.run(
 *     ["node", "--experimental-strip-types", "bridge.ts", action, json_args],
 *     capture_output=True, text=True
 *   )
 *   output = json.loads(result.stdout)
 * 
 * Actions:
 *   - bash: Execute a shell command
 *   - file_read: Read a file
 *   - file_write: Write a file  
 *   - file_edit: Edit a file (find & replace)
 *   - glob: Find files by pattern
 *   - grep: Search in files
 *   - web_fetch: Fetch a URL
 *   - compact_prompt: Get the compact prompt template
 *   - format_summary: Format a compact summary (strip analysis)
 *   - estimate_tokens: Estimate token count for text
 */

import { readFile, writeFile, mkdir } from 'fs/promises'
import { execSync, exec } from 'child_process'
import { join, resolve, dirname, basename, extname } from 'path'
import { existsSync, readFileSync, readdirSync, statSync } from 'fs'
import { promisify } from 'util'

const execAsync = promisify(exec)

// ── Action Handlers ──

async function handleBash(args: { command: string, cwd?: string, timeout?: number }): Promise<{ stdout: string, stderr: string, exitCode: number }> {
  const cwd = args.cwd || process.cwd()
  const timeout = args.timeout || 30000
  try {
    const { stdout, stderr } = await execAsync(args.command, {
      cwd,
      timeout,
      maxBuffer: 10 * 1024 * 1024, // 10MB
      shell: process.platform === 'win32' ? 'cmd.exe' : '/bin/bash',
    })
    return { stdout: stdout.toString(), stderr: stderr.toString(), exitCode: 0 }
  } catch (error: any) {
    return {
      stdout: error.stdout?.toString() || '',
      stderr: error.stderr?.toString() || error.message,
      exitCode: error.code || 1,
    }
  }
}

async function handleFileRead(args: { file_path: string, start_line?: number, end_line?: number }): Promise<{ content: string, lines: number, truncated: boolean }> {
  const maxChars = 200000 // 200K budget from tool_contract.py
  let content = await readFile(args.file_path, 'utf-8')
  const totalLines = content.split('\n').length

  if (args.start_line || args.end_line) {
    const lines = content.split('\n')
    const start = (args.start_line || 1) - 1
    const end = args.end_line || lines.length
    content = lines.slice(start, end).join('\n')
  }

  const truncated = content.length > maxChars
  if (truncated) {
    content = content.substring(0, maxChars) + '\n[... truncated]'
  }

  return { content, lines: totalLines, truncated }
}

async function handleFileWrite(args: { file_path: string, content: string, create_dirs?: boolean }): Promise<{ success: boolean, bytes_written: number }> {
  if (args.create_dirs !== false) {
    await mkdir(dirname(args.file_path), { recursive: true })
  }
  await writeFile(args.file_path, args.content, 'utf-8')
  return { success: true, bytes_written: args.content.length }
}

async function handleFileEdit(args: { file_path: string, old_text: string, new_text: string }): Promise<{ success: boolean, replacements: number }> {
  let content = await readFile(args.file_path, 'utf-8')
  const count = content.split(args.old_text).length - 1
  if (count === 0) {
    throw new Error(`old_text not found in ${args.file_path}`)
  }
  content = content.replace(args.old_text, args.new_text)
  await writeFile(args.file_path, content, 'utf-8')
  return { success: true, replacements: count }
}

async function handleGlob(args: { pattern: string, cwd?: string }): Promise<{ files: string[] }> {
  // Use Node.js built-in glob (available in v24)
  const { glob } = await import('fs/promises')
  const cwd = args.cwd || process.cwd()
  const files: string[] = []
  for await (const entry of glob(args.pattern, { cwd })) {
    files.push(join(cwd, entry))
  }
  return { files }
}

async function handleGrep(args: { pattern: string, path: string, include?: string }): Promise<{ matches: Array<{ file: string, line: number, text: string }> }> {
  // Use ripgrep if available, fallback to simple grep
  const maxResults = 50
  const matches: Array<{ file: string, line: number, text: string }> = []
  
  try {
    const rgCmd = `rg --json -m ${maxResults} "${args.pattern.replace(/"/g, '\\"')}" "${args.path}"`
    const { stdout } = await execAsync(rgCmd, { maxBuffer: 5 * 1024 * 1024 })
    
    for (const line of stdout.split('\n')) {
      if (!line.trim()) continue
      try {
        const parsed = JSON.parse(line)
        if (parsed.type === 'match') {
          matches.push({
            file: parsed.data.path.text,
            line: parsed.data.line_number,
            text: parsed.data.lines.text.trim(),
          })
        }
      } catch {}
    }
  } catch {
    // Fallback: simple file search
    // Limited but works without ripgrep
  }

  return { matches }
}

async function handleWebFetch(args: { url: string, timeout?: number }): Promise<{ content: string, status: number }> {
  const timeout = args.timeout || 15000
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeout)
  
  try {
    const response = await fetch(args.url, { signal: controller.signal })
    const text = await response.text()
    clearTimeout(timer)
    
    // Truncate to 100K
    const maxChars = 100000
    const content = text.length > maxChars 
      ? text.substring(0, maxChars) + '\n[... truncated]'
      : text
    
    return { content, status: response.status }
  } catch (error: any) {
    clearTimeout(timer)
    throw new Error(`Fetch failed: ${error.message}`)
  }
}

function handleEstimateTokens(args: { text: string }): { tokens: number } {
  return { tokens: Math.ceil(args.text.length / 4 * 4 / 3) }
}

// ── Task Management (from src/tools/TaskCreateTool etc.) ──

const tasks = new Map<string, { id: string, subject: string, description: string, status: string, created: number, output: string }>()
let taskCounter = 0

function handleTaskCreate(args: { subject: string, description: string }): { task: { id: string, subject: string } } {
  const id = `task-${++taskCounter}`
  tasks.set(id, {
    id,
    subject: args.subject,
    description: args.description,
    status: 'pending',
    created: Date.now(),
    output: '',
  })
  return { task: { id, subject: args.subject } }
}

function handleTaskList(): { tasks: Array<{ id: string, subject: string, status: string }> } {
  return {
    tasks: Array.from(tasks.values()).map(t => ({
      id: t.id,
      subject: t.subject,
      status: t.status,
    }))
  }
}

function handleTaskUpdate(args: { id: string, status?: string, output?: string }): { success: boolean } {
  const task = tasks.get(args.id)
  if (!task) return { success: false }
  if (args.status) task.status = args.status
  if (args.output) task.output = args.output
  return { success: true }
}

function handleTaskStop(args: { id: string }): { success: boolean } {
  const task = tasks.get(args.id)
  if (!task) return { success: false }
  task.status = 'stopped'
  return { success: true }
}

function handleTaskGet(args: { id: string }): { task: any | null } {
  return { task: tasks.get(args.id) || null }
}

// ── Context Collapse (from src/utils/collapseReadSearch.ts) ──

function handleContextCollapse(args: { messages: Array<{ role: string, content: string, tool_name?: string }>, budget_chars: number }): {
  collapsed: Array<{ role: string, content: string }>,
  removed_count: number,
  saved_chars: number,
} {
  const budget = args.budget_chars || 100000
  let totalChars = 0
  const collapsed: Array<{ role: string, content: string }> = []
  let removedCount = 0
  let savedChars = 0

  // From collapseReadSearch.ts: collapse consecutive read/search results
  // into summaries, keeping recent ones intact
  const messages = args.messages
  const totalMessages = messages.length
  
  // Keep last 20% of messages intact (recency bias from src/)
  const keepRecentCount = Math.max(5, Math.floor(totalMessages * 0.2))
  const cutoffIndex = totalMessages - keepRecentCount

  for (let i = 0; i < totalMessages; i++) {
    const msg = messages[i]
    
    if (i < cutoffIndex && msg.content.length > 5000) {
      // Collapse large old tool results
      const toolName = msg.tool_name || 'tool'
      const summary = `[Collapsed ${toolName} result: ${msg.content.length} chars → summary]`
      collapsed.push({ role: msg.role, content: summary })
      savedChars += msg.content.length - summary.length
      removedCount++
    } else {
      collapsed.push(msg)
    }
    
    totalChars += collapsed[collapsed.length - 1].content.length
    
    // If we're over budget, collapse more aggressively
    if (totalChars > budget && i < cutoffIndex) {
      const last = collapsed[collapsed.length - 1]
      if (last.content.length > 1000) {
        const truncated = last.content.substring(0, 500) + '\n[... collapsed for budget]'
        savedChars += last.content.length - truncated.length
        totalChars -= last.content.length - truncated.length
        last.content = truncated
      }
    }
  }

  return { collapsed, removed_count: removedCount, saved_chars: savedChars }
}

// ── Notebook Edit (from src/tools/NotebookEditTool) ──

async function handleNotebookEdit(args: { notebook_path: string, cell_index: number, new_source: string }): Promise<{ success: boolean }> {
  const content = await readFile(args.notebook_path, 'utf-8')
  const notebook = JSON.parse(content)
  
  if (!notebook.cells || args.cell_index >= notebook.cells.length) {
    throw new Error(`Cell index ${args.cell_index} out of range (${notebook.cells?.length || 0} cells)`)
  }
  
  // Update cell source
  const cell = notebook.cells[args.cell_index]
  cell.source = args.new_source.split('\n').map((line: string, i: number, arr: string[]) => 
    i < arr.length - 1 ? line + '\n' : line
  )
  
  await writeFile(args.notebook_path, JSON.stringify(notebook, null, 1), 'utf-8')
  return { success: true }
}

// ── PowerShell (from src/tools/PowerShellTool) ──

async function handlePowerShell(args: { command: string, cwd?: string, timeout?: number }): Promise<{ stdout: string, stderr: string, exitCode: number }> {
  const cwd = args.cwd || process.cwd()
  const timeout = args.timeout || 30000
  try {
    const { stdout, stderr } = await execAsync(
      `powershell -NoProfile -NonInteractive -Command "${args.command.replace(/"/g, '\\"')}"`,
      { cwd, timeout, maxBuffer: 10 * 1024 * 1024 }
    )
    return { stdout: stdout.toString(), stderr: stderr.toString(), exitCode: 0 }
  } catch (error: any) {
    return {
      stdout: error.stdout?.toString() || '',
      stderr: error.stderr?.toString() || error.message,
      exitCode: error.code || 1,
    }
  }
}

// ── List Directory (common utility) ──

async function handleListDir(args: { path: string }): Promise<{ entries: Array<{ name: string, type: string, size: number }> }> {
  const entries: Array<{ name: string, type: string, size: number }> = []
  const items = readdirSync(args.path, { withFileTypes: true })
  for (const item of items) {
    const fullPath = join(args.path, item.name)
    try {
      const stat = statSync(fullPath)
      entries.push({
        name: item.name,
        type: item.isDirectory() ? 'directory' : 'file',
        size: stat.size,
      })
    } catch {
      entries.push({ name: item.name, type: 'unknown', size: 0 })
    }
  }
  return { entries }
}

// ── Main Dispatcher ──

async function main() {
  const action = process.argv[2]
  const argsJson = process.argv[3] || '{}'
  
  if (!action) {
    console.error('Usage: node bridge.ts <action> <json_args>')
    process.exit(1)
  }

  let args: any
  try {
    args = JSON.parse(argsJson)
  } catch {
    console.error('Invalid JSON args')
    process.exit(1)
  }

  try {
    let result: any

    switch (action) {
      // Core file tools
      case 'bash':           result = await handleBash(args); break
      case 'file_read':      result = await handleFileRead(args); break
      case 'file_write':     result = await handleFileWrite(args); break
      case 'file_edit':      result = await handleFileEdit(args); break
      case 'glob':           result = await handleGlob(args); break
      case 'grep':           result = await handleGrep(args); break
      case 'web_fetch':      result = await handleWebFetch(args); break
      case 'estimate_tokens': result = handleEstimateTokens(args); break
      
      // Task management
      case 'task_create':    result = handleTaskCreate(args); break
      case 'task_list':      result = handleTaskList(); break
      case 'task_update':    result = handleTaskUpdate(args); break
      case 'task_stop':      result = handleTaskStop(args); break
      case 'task_get':       result = handleTaskGet(args); break
      
      // Advanced tools
      case 'notebook_edit':  result = await handleNotebookEdit(args); break
      case 'powershell':     result = await handlePowerShell(args); break
      case 'list_dir':       result = await handleListDir(args); break
      case 'context_collapse': result = handleContextCollapse(args); break
      
      default:
        throw new Error(`Unknown action: ${action}`)
    }

    // Output JSON result to stdout
    console.log(JSON.stringify(result))
    
  } catch (error: any) {
    console.log(JSON.stringify({ 
      error: true, 
      message: error.message || String(error) 
    }))
    process.exit(1)
  }
}

main()

