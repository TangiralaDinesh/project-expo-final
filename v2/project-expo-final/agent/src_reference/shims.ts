/**
 * Shims for external dependencies — replaces bun:bundle, @anthropic-ai/sdk,
 * zod, lodash, and analytics with no-op stubs.
 * 
 * This file is imported by all src_reference/ files that previously
 * depended on Anthropic-specific infrastructure.
 */

// ── bun:bundle feature flags → always return defaults ──
export function feature(name: string): boolean {
  // Disable all Anthropic-internal feature flags
  const ENABLED_FLAGS: Record<string, boolean> = {
    // Enable features that matter for us:
    'CACHED_MICROCOMPACT': false,  // We don't use API cache editing
  }
  return ENABLED_FLAGS[name] ?? false
}

// ── Analytics → no-op ──
export type AnalyticsMetadata_I_VERIFIED_THIS_IS_NOT_CODE_OR_FILEPATHS = string
export function logEvent(name: string, data: Record<string, unknown>): void {
  // No-op in our bridge — we use Python logging
}

export function getFeatureValue_CACHED_MAY_BE_STALE<T>(key: string, defaultValue: T): T {
  return defaultValue
}

export function getDynamicConfig_CACHED_MAY_BE_STALE<T>(key: string, defaultValue: T): T {
  return defaultValue
}

// ── Logging → console ──
export function logForDebugging(msg: string, opts?: { level?: string }): void {
  if (opts?.level === 'error') {
    console.error(`[DEBUG] ${msg}`)
  } else {
    console.log(`[DEBUG] ${msg}`)
  }
}

export function logError(error: unknown): void {
  console.error('[ERROR]', error)
}

// ── Token estimation ──
export function roughTokenCountEstimation(text: string): number {
  return Math.ceil(text.length / 4)
}

export function roughTokenCountEstimationForMessages(messages: any[]): number {
  let total = 0
  for (const msg of messages) {
    if (msg.message?.content) {
      if (typeof msg.message.content === 'string') {
        total += roughTokenCountEstimation(msg.message.content)
      } else if (Array.isArray(msg.message.content)) {
        for (const block of msg.message.content) {
          if (block.type === 'text') total += roughTokenCountEstimation(block.text)
          else if (block.type === 'tool_result' && typeof block.content === 'string') {
            total += roughTokenCountEstimation(block.content)
          }
        }
      }
    }
  }
  return Math.ceil(total * 4 / 3) // 4/3 conservative padding
}

// ── Error utilities ──
export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

export function getErrnoCode(error: unknown): string | undefined {
  return (error as any)?.code
}

export function hasExactErrorMessage(msg: any, text: string): boolean {
  return errorMessage(msg) === text
}

export class TelemetrySafeError_I_VERIFIED_THIS_IS_NOT_CODE_OR_FILEPATHS extends Error {
  telemetryMessage: string
  constructor(message: string) {
    super(message)
    this.telemetryMessage = message
  }
}

// ── Lodash replacements ──
export function memoize<T extends (...args: any[]) => any>(fn: T): T {
  let cached: any
  let called = false
  return ((...args: any[]) => {
    if (!called) { cached = fn(...args); called = true }
    return cached
  }) as T
}

export function uniqBy<T>(array: T[], key: (item: T) => any): T[] {
  const seen = new Set()
  return array.filter(item => {
    const k = key(item)
    if (seen.has(k)) return false
    seen.add(k)
    return true
  })
}

// ── JSON stringify (safe version) ──
export function jsonStringify(obj: unknown): string {
  try {
    return JSON.stringify(obj)
  } catch {
    return String(obj)
  }
}

// ── Zod replacement — simple runtime validation ──
export const z = {
  string: () => ({ parse: (v: any) => String(v), optional: () => ({ parse: (v: any) => v ? String(v) : undefined }) }),
  number: () => ({ parse: (v: any) => Number(v), optional: () => ({ parse: (v: any) => v != null ? Number(v) : undefined }) }),
  boolean: () => ({ parse: (v: any) => Boolean(v) }),
  object: (shape: any) => ({
    parse: (v: any) => v,
    passthrough: () => ({ parse: (v: any) => v }),
  }),
  array: (inner: any) => ({ parse: (v: any) => Array.isArray(v) ? v : [] }),
  enum: (values: string[]) => ({ parse: (v: any) => values.includes(v) ? v : values[0] }),
  union: (...args: any[]) => ({ parse: (v: any) => v }),
  literal: (val: any) => ({ parse: (v: any) => val }),
}

// ── Sleep utility ──
export function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms))
}

// ── Compact warning suppression ──
let _compactWarningSuppressed = false
export function suppressCompactWarning(): void { _compactWarningSuppressed = true }
export function clearCompactWarningSuppression(): void { _compactWarningSuppressed = false }
export function isCompactWarningSuppressed(): boolean { return _compactWarningSuppressed }

// ── Prompt cache break detection → no-op ──
export function notifyCacheDeletion(source: string): void {}
export function notifyCompaction(source: string): void {}

// ── System prompt type ──
export function asSystemPrompt(text: string): string { return text }

// ── Bootstrap state stubs ──
export function markPostCompaction(): void {}
export function getSdkBetas(): string[] { return [] }
export function getInvokedSkillsForAgent(): string[] { return [] }

// ── Context window ──
export function getContextWindowForModel(model: string, betas: string[]): number {
  return 128000  // Default to 128K for NIM models
}

export function getMaxOutputTokensForModel(model: string): number {
  return 8192
}

// ── Type stubs for Anthropic SDK types ──
export type ContentBlockParam = any
export type ToolResultBlockParam = any  
export type ToolUseBlock = any
export type Message = any
export type UUID = string
export type QuerySource = string
export type ToolUseContext = any
export type CanUseToolFn = (tool: any, input: any) => Promise<any>
export type CacheSafeParams = any
