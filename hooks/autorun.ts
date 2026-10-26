// browser-shield autorun: a Claude Code mod that starts the page shield on YouTube by itself.
//
// After Claude's browser (the desktop Browser pane, or Claude in Chrome) finishes a navigate, preview_start or
// browser_batch, the mod reads where the browser says each tab landed. For every tab that landed on YouTube and has
// no shield yet, it runs page/shield.js there through the same browser's javascript_tool, as a tool call of its own
// that the guard hook, the user's permission rules and auto mode judge like any other. When the shield starts on
// every such tab, the guard's "shield is not active" reminder is replaced by the shield's status; otherwise the
// reminder stays and a line says what went wrong.
//
// Only the browser's own report lines are read (never page titles or page text), the host is decided here and not in
// the page, and no text a page could choose reaches the notes the model reads.
//
// The pure parts are exported for tests/autorun.test.mjs (Node strips the types); `register` is the mod.
import type { Register } from 'claude-code'

/** The browser navigation tools this mod follows, for the pane, the pane on a linked computer, and Chrome. */
export const NAV_TOOLS = /^mcp__(Claude_Browser|remote-devices__Claude_Browser|claude-in-chrome)__(navigate|preview_start|browser_batch)$/

/**
 * Hostnames the shield is started on: youtube.com, youtube-nocookie.com and their subdomains. A browser's hostname is
 * lower-case ASCII; page/shield.js's own `isYouTube` agrees on every such name (pinned by a test).
 */
export const YOUTUBE_HOST = /^(?:[a-z0-9-]+\.)*youtube(?:-nocookie)?\.com$/

/**
 * The first words of the guard's PostToolUse reminder (hooks/guard.py), which this mod replaces once the shield runs.
 * tests/autorun.test.mjs runs the real guard and checks that this removes its reminder and nothing else.
 */
export const REMINDER_PREFIX = 'browser-shield: the page shield is not active on '

/**
 * The longest the navigation's result waits for the shield, all tabs together. A hook's time budget does not run
 * while its tool calls are in flight, so this bound is the mod's own; a call still running past it is left running.
 */
const WAIT_MS = 8_000

/** Where a tool call came from and which server and tool reach that browser's javascript_tool. */
export type Browser = { server: string; jsTool: string }

/** One tab to run the shield on: the YouTube host it landed on, and its id (absent: the browser's front tab). */
export type Target = Browser & { host: string; tabId?: string | number }

/** What one run reported. Every field is the mod's own or validated: nothing here is text a page chose. */
export type Outcome =
  | { kind: 'running'; tabId?: string | number; host: string; adsSkipped: number; enforcement: boolean }
  | { kind: 'skipped'; tabId?: string | number }
  | { kind: 'failed'; tabId?: string | number; reason: string }

/** The server and tool name of the javascript_tool belonging to the browser a navigation tool name names. */
export function browserOf(tool: string): Browser | undefined {
  const m = NAV_TOOLS.exec(tool)
  if (!m) return undefined
  const name = m[1] ?? ''
  const [server, prefix] = name.startsWith('remote-devices__')
    ? ['remote-devices', 'Claude_Browser__']
    : [name, '']
  return { server, jsTool: `${prefix}javascript_tool` }
}

/** The hostname a browser reads from a URL as the navigate tool takes it (a bare host gets https://), else ''. */
export function hostOf(url: unknown): string {
  if (typeof url !== 'string') return ''
  try {
    const u = new URL(/^[a-z][a-z0-9+.-]*:\/\//i.test(url) ? url : `https://${url}`)
    return u.protocol === 'https:' || u.protocol === 'http:' ? u.hostname : ''
  } catch {
    return ''
  }
}

const isShieldCall = (text: unknown): boolean =>
  typeof text === 'string' &&
  (text.trim().replace(/;$/, '') === 'browserShield()' || text.trimStart().startsWith('// browser-shield page script'))

const tabIdOf = (value: unknown): string | number | undefined =>
  typeof value === 'string' || typeof value === 'number' ? value : undefined

/**
 * The browser's own report in one result section: the URL it says it landed on and the tab it says it acted on.
 * Only whole lines the browser writes match: a page's title sits inside an indented `• tabId …: "<title>"` line of
 * the tab list, and a title holds no line break.
 */
export function reportOf(section: string): { landed?: string; tabId?: string } {
  return {
    landed: /^(?:\[navigate\] )?navigated to (\S+)$/m.exec(section)?.[1],
    tabId: /^- Executed on tabId: (\S+)$/m.exec(section)?.[1],
  }
}

/** The JSON value a result starts with (pretty-printed or on one line), or undefined. */
export function leadingJson(text: string): Record<string, unknown> | undefined {
  const m = /^\s*(\{[\s\S]*?\n\}|\{[^\n]*\})/.exec(text)
  if (!m?.[1]) return undefined
  try {
    const value: unknown = JSON.parse(m[1])
    return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : undefined
  } catch {
    return undefined
  }
}

type Step = { name?: unknown; input?: Record<string, unknown> }

/**
 * Each batch step paired with its own section of the result, or undefined for a step that left none (the batch
 * stopped first). Sections are found from the last step backwards, each the last header for its step before the next
 * step's section, so a header-shaped line inside an earlier step's page text is never taken for a later step's.
 */
export function sectionsOf(steps: readonly Step[], text: string): Array<string | undefined> {
  const out: Array<string | undefined> = new Array(steps.length).fill(undefined)
  let end = text.length
  for (let i = steps.length - 1; i >= 0; i--) {
    const name = steps[i]?.name
    if (typeof name !== 'string' || !/^[a-z_]+$/.test(name)) continue
    const header = new RegExp(`^\\[${name}(?::[a-z_]+)?\\] `, 'gm')
    let start = -1
    for (const m of text.slice(0, end).matchAll(header)) start = m.index ?? -1
    if (start < 0) continue
    out[i] = text.slice(start, end)
    end = start
  }
  return out
}

/**
 * The tabs a finished navigation call left on YouTube without the shield, from the browser's own report.
 *
 * `input` is the call as the hooks saw it; `resultText` is what the browser answered. A navigate counts where the
 * browser says it landed (a redirect, back and forward included); with no such line, where it was sent. A batch is
 * followed tab by tab: a later navigate on a tab replaces what an earlier one did, and a `browserShield()` step on a
 * tab clears it.
 */
export function targetsOf(tool: string, input: Record<string, unknown>, resultText: string): Target[] {
  const browser = browserOf(tool)
  if (!browser) return []
  const pending = new Map<string, { host: string; tabId?: string | number }>()
  const keyOf = (tabId: string | number | undefined) => (tabId === undefined ? '' : `${typeof tabId}:${tabId}`)

  if (/(navigate|preview_start)$/.test(tool)) {
    const report = reportOf(resultText)
    const opened = report.landed === undefined && report.tabId === undefined ? leadingJson(resultText) : undefined
    if (opened?.navOk === false) return []
    const host = hostOf(report.landed ?? input.url)
    const tabId = report.tabId ?? tabIdOf(opened?.tabId) ?? tabIdOf(input.tabId)
    if (YOUTUBE_HOST.test(host)) pending.set(keyOf(tabId), { host, tabId })
  } else {
    const steps: Step[] = (Array.isArray(input.actions) ? input.actions : [])
      .map((a: unknown) => (a && typeof a === 'object' ? (a as Step) : {}))
    const sections = sectionsOf(steps, resultText)
    steps.forEach((step, i) => {
      const section = sections[i]
      const args = step.input && typeof step.input === 'object' ? step.input : {}
      if (section === undefined) return
      const report = reportOf(section)
      if (step.name === 'navigate') {
        const tabId = report.tabId ?? tabIdOf(args.tabId)
        const host = hostOf(report.landed ?? args.url)
        if (YOUTUBE_HOST.test(host)) pending.set(keyOf(tabId), { host, tabId })
        else pending.delete(keyOf(tabId))
      } else if (step.name === 'javascript_tool' && isShieldCall(args.text)) {
        pending.delete(keyOf(tabIdOf(args.tabId) ?? report.tabId))
      }
    })
  }
  return [...pending.values()].map(({ host, tabId }) => (tabId === undefined ? { ...browser, host } : { ...browser, host, tabId }))
}

/**
 * The page script behind a hostname check that a page cannot fake: `location.hostname` cannot be replaced, and the
 * check uses only `===`, a string's length and its characters, none of which a page can patch (a page can replace
 * RegExp or String methods). On YouTube it evaluates to the shield's status, anywhere else to `{ skipped: true }`
 * without running any of it. page/shield.js is one expression (pinned by a test).
 */
export function gatedScript(shield: string): string {
  return [
    '((h) => {',
    '  const is = (s) => {',
    '    if (h === s) return true;',
    '    const n = h.length, m = s.length;',
    "    if (n <= m || h[n - m - 1] !== '.') return false;",
    '    for (let i = 1; i <= m; i++) if (h[n - i] !== s[m - i]) return false;',
    '    return true;',
    '  };',
    "  return is('youtube.com') || is('youtube-nocookie.com');",
    '})(location.hostname)',
    '? (',
    shield,
    ')',
    ': ({ skipped: true })',
  ].join('\n')
}

/**
 * What a javascript_tool answer means. The answer comes from the page, so it counts as the shield running only when
 * it has the shield status's shape, says YouTube, and the browser's own origin line (when present) is YouTube; the
 * note then uses the host the browser reported, never the page's words. Failures carry fixed reasons.
 */
export function outcomeOf(target: Pick<Target, 'host' | 'tabId'>, text: string, isError: boolean): Outcome {
  const { tabId } = target
  if (isError) {
    return { kind: 'failed', tabId, reason: /^\s*No tab with id\b/.test(text) ? 'no such tab' : 'the script raised an error' }
  }
  const status = leadingJson(text)
  if (status?.skipped === true) return { kind: 'skipped', tabId }
  const origin = /^\(captured at origin (\S+)\)$/m.exec(text)?.[1]
  if (origin !== undefined && !YOUTUBE_HOST.test(hostOf(origin))) {
    return { kind: 'failed', tabId, reason: 'the script ran on a page that is not YouTube' }
  }
  const ads = status?.adsSkipped
  if (!status || status.youtube !== true || typeof status.host !== 'string' || !YOUTUBE_HOST.test(status.host) ||
      typeof ads !== 'number' || !Number.isInteger(ads) || ads < 0 || typeof status.enforcement !== 'boolean') {
    return { kind: 'failed', tabId, reason: "the page did not answer with the shield's status" }
  }
  return { kind: 'running', tabId, host: target.host, adsSkipped: ads, enforcement: status.enforcement }
}

/** The tool result's context with the guard's reminder lines taken out; other notes stay, empty entries go. */
export function withoutReminder(context: readonly string[] | undefined): string[] {
  return (context ?? [])
    .map((entry) => entry.split('\n').filter((line) => !line.startsWith(REMINDER_PREFIX)).join('\n'))
    .filter((entry) => entry.trim() !== '')
}

/** A tab id as a note shows it: the browser's ids are short words; anything else is not echoed. */
const tabName = (tabId: string | number | undefined): string =>
  tabId === undefined ? 'the front tab' : /^[\w.-]{1,40}$/.test(String(tabId)) ? `tab ${tabId}` : 'the tab'

/** The line the model reads about each run. */
export function noteOf(outcome: Outcome): string | undefined {
  const tab = tabName(outcome.tabId)
  switch (outcome.kind) {
    case 'running':
      return `browser-shield: the page shield started by itself on ${outcome.host} (${tab}); it skips ads as they ` +
        `start, and \`browserShield()\` returns its status (adsSkipped ${outcome.adsSkipped}).` +
        (outcome.enforcement
          ? ' YouTube is showing its ad-blocker notice (enforcement: true): tell the user.'
          : '')
    case 'skipped':
      return undefined
    case 'failed':
      return `browser-shield: the autorun mod could not start the page shield on ${tab} (${outcome.reason}). Run ` +
        'javascript_tool with the text `browserShield()` yourself; where the shield already runs it only returns ' +
        'its status.'
  }
}

/** The text of a tool's answer: a string, content blocks, or `{ content }`. */
export function textOf(response: unknown): string {
  if (typeof response === 'string') return response
  if (Array.isArray(response)) {
    return response
      .map((block) => (block && typeof block === 'object' && typeof (block as { text?: unknown }).text === 'string'
        ? (block as { text: string }).text
        : ''))
      .join('\n')
  }
  if (response && typeof response === 'object' && 'content' in response) return textOf((response as { content: unknown }).content)
  return ''
}

// The hook sits on PostToolUse, not on the tool call: hooks modules run above the settings hooks there (the guard
// is one), so this one can take the guard's reminder out of what the guard answered. On a tool call the engine
// refuses a hook that leaves out a note a hook beneath attached.
export const register: Register = (on) => {
  on('classic.PostToolUse', { tool_name: NAV_TOOLS }, async ($, e, next) => {
    const below = await next(e)
    const input = e.tool_input && typeof e.tool_input === 'object' ? (e.tool_input as Record<string, unknown>) : {}
    const targets = targetsOf(e.tool_name, input, textOf(e.tool_response))
    if (targets.length === 0) return below

    let shield: string | undefined
    try {
      shield = gatedScript(await $.fs.read(`${$.plugin.root}/page/shield.js`))
    } catch {
      shield = undefined
    }
    const outcomes: Outcome[] = shield === undefined
      ? targets.map((t) => ({ kind: 'failed', tabId: t.tabId, reason: 'page/shield.js could not be read' }))
      : await runShield((call) => $.tool.call(call), (ms, options) => $.clock.sleep(ms, options), next.signal, targets, shield)

    const started = outcomes.some((o) => o.kind === 'running')
    const failed = outcomes.some((o) => o.kind === 'failed')
    const kept = started && !failed ? withoutReminder(below.additionalContext) : [...(below.additionalContext ?? [])]
    const notes = outcomes.map(noteOf).filter((n): n is string => n !== undefined)
    if (notes.length === 0 && kept.length === (below.additionalContext ?? []).length) return below
    return { ...below, additionalContext: [...kept, ...notes] }
  }).catch(async ($, e, next) => afterFailure(e, await next(e)))
}

/** What the model reads when the hook itself failed on a navigation that needed the shield. */
export const FAILED_NOTE = 'browser-shield: the autorun mod failed on this navigation. Run javascript_tool with the ' +
  'text `browserShield()` yourself; where the shield already runs it only returns its status.'

/** After the hook failed: the guard's answer stands, plus FAILED_NOTE where the navigation may have needed the shield. */
export function afterFailure<T extends { additionalContext?: readonly string[] }>(
  event: { tool_name: string; tool_input: unknown; tool_response: unknown }, below: T,
): T {
  let needed = true
  try {
    const input = event.tool_input && typeof event.tool_input === 'object' ? (event.tool_input as Record<string, unknown>) : {}
    needed = targetsOf(event.tool_name, input, textOf(event.tool_response)).length > 0
  } catch {
    needed = true
  }
  return needed ? { ...below, additionalContext: [...(below.additionalContext ?? []), FAILED_NOTE] } : below
}

type ToolCall = (input: { tool: `mcp__${string}__${string}`; action: string; text: string; tabId?: string | number }) =>
  Promise<{ deny?: string; text?: string; result?: unknown; isError?: boolean }>
type Sleep = (ms: number, options?: { signal?: AbortSignal }) => Promise<void>

/** Runs the shield on every target at once, all under one deadline; an interrupt ends the wait. */
async function runShield(call: ToolCall, sleep: Sleep, interrupt: AbortSignal, targets: Target[], shield: string): Promise<Outcome[]> {
  const timer = new AbortController()
  const stop = () => timer.abort()
  interrupt.addEventListener('abort', stop, { once: true })
  // Resolves at the deadline; rejects when aborted (every call answered, or the person interrupted).
  const deadline = sleep(WAIT_MS, { signal: timer.signal }).then(() => 'timeout' as const, () => 'interrupted' as const)
  try {
    return await Promise.all(targets.map(async (target): Promise<Outcome> => {
      const { tabId } = target
      try {
        // A tool call of its own, not a direct server call: it passes the PreToolUse hooks (the guard), the
        // permission check and, in auto mode, the classifier, exactly as the model's own call would.
        const answer = await Promise.race([
          call({
            tool: `mcp__${target.server}__${target.jsTool}`,
            action: 'javascript_exec',
            text: shield,
            ...(tabId === undefined ? {} : { tabId }),
          }),
          deadline,
        ])
        if (answer === 'timeout') {
          return { kind: 'failed', tabId, reason: `no answer in ${WAIT_MS / 1000} s; it may be waiting on a permission prompt` }
        }
        if (answer === 'interrupted') return { kind: 'failed', tabId, reason: 'interrupted' }
        if (answer.deny !== undefined) return { kind: 'failed', tabId, reason: `refused: ${answer.deny.slice(0, 300)}` }
        return outcomeOf(target, answer.text ?? textOf(answer.result), answer.isError === true)
      } catch {
        return { kind: 'failed', tabId, reason: 'the tool call was rejected' }
      }
    }))
  } finally {
    timer.abort()
    interrupt.removeEventListener('abort', stop)
  }
}
