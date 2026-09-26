import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, putPlan, type Plan } from './api'
import { KEEPALIVE_MAX_BYTES } from './config'
import { PlanEditor, type SendOptions } from './editor.svelte'
import { bigPlanOf, planOf, rowOf, sectionsOf } from './fixtures'

/** A save whose answers are released by the test, so requests can overlap deterministically. */
function deferredSave() {
  const pending: { plan: Plan; resolve: (p: Plan) => void; reject: (e: unknown) => void }[] = []
  const save = vi.fn(
    (plan: Plan, _opts?: SendOptions) =>
      new Promise<Plan>((resolve, reject) => {
        pending.push({ plan, resolve, reject })
      }),
  )
  return { save, pending, release: (i = 0, answer?: Plan) => pending[i]!.resolve(answer ?? pending[i]!.plan) }
}

const echo = vi.fn(async (plan: Plan, _opts?: SendOptions) => plan)

beforeEach(() => {
  vi.useFakeTimers()
  echo.mockClear()
})
afterEach(() => {
  vi.useRealTimers()
})

describe('PlanEditor saving (AC-2)', () => {
  it('PUTs 600 ms after the last edit, once for a burst of edits', async () => {
    const editor = new PlanEditor(planOf(), echo)
    editor.rename(0, 'One')
    await vi.advanceTimersByTimeAsync(500)
    editor.rename(0, 'One two')
    await vi.advanceTimersByTimeAsync(599)
    expect(echo).not.toHaveBeenCalled()
    expect(editor.dirty).toBe(true)
    await vi.advanceTimersByTimeAsync(1)
    expect(echo).toHaveBeenCalledTimes(1)
    expect(echo.mock.calls[0]?.[0].sections[0]?.name).toBe('One two')
    await vi.advanceTimersByTimeAsync(0)
    expect(editor.dirty).toBe(false)
    expect(editor.saving).toBe(false)
  })

  it('keeps one request in flight and sends the latest plan after it', async () => {
    const { save, pending, release } = deferredSave()
    const editor = new PlanEditor(planOf(), save)
    editor.rename(0, 'A')
    await vi.advanceTimersByTimeAsync(600)
    expect(save).toHaveBeenCalledTimes(1)
    expect(editor.saving).toBe(true)
    editor.rename(0, 'AB')
    editor.rename(0, 'ABC')
    await vi.advanceTimersByTimeAsync(2000)
    expect(save).toHaveBeenCalledTimes(1) // never two at once
    release(0)
    await vi.advanceTimersByTimeAsync(0)
    expect(save).toHaveBeenCalledTimes(2)
    expect(pending[1]?.plan.sections[0]?.name).toBe('ABC')
    // The first answer named the OLD text; the local edits made meanwhile survive it.
    expect(editor.plan.sections[0]?.name).toBe('ABC')
    release(1)
    await vi.advanceTimersByTimeAsync(0)
    expect(editor.dirty).toBe(false)
  })

  it('adopts the 200 body as the new truth (the server normalizes names)', async () => {
    const editor = new PlanEditor(planOf({ sections: sectionsOf([['Intro', 1], ['Intro', 3]]) }), async (p) => ({
      ...p,
      sections: p.sections.map((s, i) => ({ ...s, name: i ? `${s.name.trim()} (2)` : s.name.trim() })),
    }))
    editor.rename(0, '  Intro ')
    await vi.advanceTimersByTimeAsync(600)
    expect(editor.plan.sections.map((s) => s.name)).toEqual(['Intro', 'Intro (2)'])
  })

  it('keeps 422 field errors by loc until the next success (AC-5)', async () => {
    const errors = [
      { loc: ['body', 'sections', 1, 'page'], msg: 'Value error, page must be at most 6', type: 'value_error' },
      { loc: ['body', 'settings', 'column_split'], msg: 'Input should be ≤ 0.8', type: 'less_than_equal' },
      { loc: ['body', 'overrides'], msg: "override '3' does not name a section index", type: 'value_error' },
    ]
    const save = vi.fn().mockRejectedValueOnce(new ApiError(422, 'invalid', 'invalid', errors)).mockImplementation(echo)
    const editor = new PlanEditor(planOf(), save)
    editor.setPage(1, 99)
    await vi.advanceTimersByTimeAsync(600)
    expect(editor.errorAt('sections', 1, 'page')).toBe('page must be at most 6')
    expect(editor.errorAt('settings', 'column_split')).toBe('Input should be ≤ 0.8')
    expect(editor.errorAt('sections', 0, 'page')).toBeNull()
    expect(editor.listErrors).toEqual(["override '3' does not name a section index"])
    expect(editor.error).toBeNull()
    expect(editor.dirty).toBe(true)
    editor.setPage(1, 3)
    await vi.advanceTimersByTimeAsync(600)
    expect(editor.fieldErrors).toEqual({})
    expect(editor.dirty).toBe(false)
  })

  it.each([
    [409, 'busy'],
    [0, 'network'],
    [500, 'internal'],
  ] as const)('shows a %i %s and does not retry on its own', async (status, code) => {
    const save = vi.fn().mockRejectedValue(new ApiError(status, code))
    const editor = new PlanEditor(planOf(), save)
    editor.rename(0, 'x')
    await vi.advanceTimersByTimeAsync(600)
    expect(editor.error?.code).toBe(code)
    expect(editor.gone).toBe(false)
    await vi.advanceTimersByTimeAsync(10_000)
    expect(save).toHaveBeenCalledTimes(1)
    // Retry sends the same dirty plan once more.
    await editor.flush()
    expect(save).toHaveBeenCalledTimes(2)
  })

  it('marks the job gone on 410 and stops saving', async () => {
    const save = vi.fn().mockRejectedValue(new ApiError(410, 'expired'))
    const editor = new PlanEditor(planOf(), save)
    editor.rename(0, 'x')
    await vi.advanceTimersByTimeAsync(600)
    expect(editor.gone).toBe(true)
    editor.rename(0, 'y')
    await vi.advanceTimersByTimeAsync(600)
    expect(save).toHaveBeenCalledTimes(1)
  })

  it('a non-ApiError failure reads as a network problem', async () => {
    const editor = new PlanEditor(planOf(), vi.fn().mockRejectedValue(new TypeError('boom')))
    editor.rename(0, 'x')
    await vi.advanceTimersByTimeAsync(600)
    expect(editor.error?.code).toBe('network')
  })
})

describe('PlanEditor edits (AC-3)', () => {
  it('delete, merge and add shift the selection and the overrides with the list', async () => {
    const editor = new PlanEditor(planOf({ overrides: { '2': { startCut: 5 } } }), echo)
    editor.select(2)
    editor.remove(0)
    expect(editor.plan.sections.map((s) => s.page)).toEqual([3, 4])
    expect(editor.plan.overrides).toEqual({ '1': { startCut: 5 } })
    expect(editor.selected).toBe(1)
    const at = editor.add('Preface', 1)
    expect(at).toBe(0)
    expect(editor.plan.sections.map((s) => s.name)).toEqual(['Preface', '2 Chapter Two: The Middle of the Synthetic Book', '3 Closing Chapter'])
    expect(editor.plan.overrides).toEqual({ '2': { startCut: 5 } })
    expect(editor.selected).toBe(2)
    editor.merge(1)
    expect(editor.plan.sections.map((s) => s.name)).toEqual(['Preface', '2 Chapter Two: The Middle of the Synthetic Book'])
    expect(editor.plan.overrides).toEqual({})
    expect(editor.selected).toBe(1)
    editor.remove(1)
    expect(editor.selected).toBeNull()
    await vi.advanceTimersByTimeAsync(600)
    expect(echo).toHaveBeenCalledTimes(1)
  })

  it('setSetting changes one key and saves', async () => {
    const editor = new PlanEditor(planOf(), echo)
    editor.setSetting('heading_wrap_gap', 30)
    editor.setSetting('single_column', true)
    await vi.advanceTimersByTimeAsync(600)
    expect(echo.mock.calls[0]?.[0].settings).toMatchObject({ heading_wrap_gap: 30, single_column: true, column_split: 0.487 })
  })

  it('setOverride writes the section’s manual cut, null or {} removes it, and each landed save counts (STORY-010 AC-2/AC-4)', async () => {
    const editor = new PlanEditor(planOf(), echo)
    expect(editor.saves).toBe(0)
    editor.setOverride(1, { startCut: 200, startCol: 'left' })
    expect(editor.plan.overrides).toEqual({ '1': { startCut: 200, startCol: 'left' } })
    expect(editor.dirty).toBe(true)
    await vi.advanceTimersByTimeAsync(600)
    expect(echo).toHaveBeenCalledTimes(1)
    expect(echo.mock.calls[0]?.[0].overrides).toEqual({ '1': { startCut: 200, startCol: 'left' } })
    expect(editor.saves).toBe(1)
    // The engine's start cut removed, the end kept: the keys sent are the keys applied.
    editor.setOverride(1, { startCut: null, endCut: 400, endCol: 'right' })
    await vi.advanceTimersByTimeAsync(600)
    expect(echo.mock.calls[1]?.[0].overrides['1']).toEqual({ startCut: null, endCut: 400, endCol: 'right' })
    editor.setOverride(1, null)
    expect(editor.plan.overrides).toEqual({})
    await vi.advanceTimersByTimeAsync(600)
    expect(echo).toHaveBeenCalledTimes(3)
    expect(editor.saves).toBe(3)
    // Nothing to remove and nothing to write: not an edit.
    editor.setOverride(1, null)
    editor.setOverride(1, {})
    editor.setOverride(9, { startCut: 1 })
    expect(editor.dirty).toBe(false)
    await vi.advanceTimersByTimeAsync(600)
    expect(echo).toHaveBeenCalledTimes(3)
  })

  it('a refused save does not count as landed', async () => {
    const editor = new PlanEditor(planOf(), vi.fn(async () => Promise.reject(new ApiError(409, 'busy'))))
    editor.setOverride(0, { endCut: 300 })
    await vi.advanceTimersByTimeAsync(600)
    expect(editor.saves).toBe(0)
    expect(editor.error?.code).toBe('busy')
  })
})

describe('PlanEditor source switch + undo (AC-2, AC-6)', () => {
  it('replaces the list and overrides, offers Undo back to the list before the run, then saves', async () => {
    const editor = new PlanEditor(planOf({ overrides: { '0': { startCut: 5 } } }), echo)
    editor.replaceSections('headings', sectionsOf([['H1', 1], ['H2', 2]]), 'the detected headings')
    expect(editor.plan.source).toBe('headings')
    expect(editor.plan.sections.map((s) => s.name)).toEqual(['H1', 'H2'])
    expect(editor.plan.overrides).toEqual({})
    expect(editor.undo?.label).toBe('the detected headings')
    // A second pick in the same run (a slider drag) keeps the ORIGINAL snapshot.
    editor.replaceSections('headings', sectionsOf([['H1', 1]]), 'the detected headings')
    await vi.advanceTimersByTimeAsync(600)
    expect(echo).toHaveBeenCalledTimes(1)
    expect(echo.mock.calls[0]?.[0].sections).toHaveLength(1)
    editor.undoLast()
    expect(editor.plan.source).toBe('outline')
    expect(editor.plan.sections).toHaveLength(3)
    expect(editor.plan.overrides).toEqual({ '0': { startCut: 5 } })
    expect(editor.undo).toBeNull()
    await vi.advanceTimersByTimeAsync(600)
    expect(echo).toHaveBeenCalledTimes(2)
    expect(echo.mock.calls[1]?.[0].source).toBe('outline')
  })

  it('Undo restores the source, list, overrides and picker controls — never a setting changed since (gate r1 F6, F8)', async () => {
    const picker = { outlineLevel: 1, headingLevel: 1, threshold: 1, maxLength: 90 }
    const editor = new PlanEditor(planOf({ overrides: { '0': { startCut: 5 } } }), echo, { picker })
    editor.replaceSections('outline', sectionsOf([['1.1', 1]]), 'the outline (level 2)', { ...picker, outlineLevel: 2 })
    expect(editor.picker.outlineLevel).toBe(2)
    editor.setSetting('header_band', 60)
    editor.setSetting('single_column', true)
    editor.undoLast()
    expect(editor.plan.source).toBe('outline')
    expect(editor.plan.sections).toHaveLength(3)
    expect(editor.plan.overrides).toEqual({ '0': { startCut: 5 } })
    expect(editor.picker).toEqual(picker)
    expect(editor.plan.settings).toMatchObject({ header_band: 60, single_column: true })
    await vi.advanceTimersByTimeAsync(600)
    expect(echo.mock.lastCall?.[0].settings).toMatchObject({ header_band: 60, single_column: true })
  })

  it('the Undo offer expires', async () => {
    const editor = new PlanEditor(planOf(), echo, { undoMs: 1000 })
    editor.replaceSections('manual', [], 'the pasted list')
    await vi.advanceTimersByTimeAsync(999)
    expect(editor.undo).not.toBeNull()
    await vi.advanceTimersByTimeAsync(1)
    expect(editor.undo).toBeNull()
    editor.undoLast() // nothing to undo: a no-op
    expect(editor.plan.source).toBe('manual')
  })
})

describe('PlanEditor never loses a save (gate r1 F7)', () => {
  /** A stand-in for the window: the editor listens here, the test fires the events. */
  type Listener = (event: { preventDefault(): void }) => void
  function fakePage(visibility: DocumentVisibilityState = 'visible') {
    const listeners = new Map<string, Listener>()
    return {
      document: { visibilityState: visibility },
      addEventListener: (type: string, fn: Listener) => void listeners.set(type, fn),
      removeEventListener: (type: string) => void listeners.delete(type),
      fire: (type: string, event = { preventDefault: vi.fn() }) => {
        listeners.get(type)?.(event)
        return event
      },
      listening: () => [...listeners.keys()].sort(),
    }
  }

  it('destroy() sends a pending edit without awaiting it', async () => {
    const page = fakePage()
    const editor = new PlanEditor(planOf(), echo, { page })
    expect(page.listening()).toEqual(['beforeunload', 'pagehide', 'visibilitychange'])
    editor.setPage(0, 2)
    editor.destroy()
    expect(echo).toHaveBeenCalledTimes(1)
    expect(echo.mock.calls[0]?.[0].sections[0]?.page).toBe(2)
    expect(echo.mock.calls[0]?.[1]).toEqual({})
    expect(page.listening()).toEqual([])
    // Nothing pending: destroy() sends nothing.
    const idle = new PlanEditor(planOf(), echo, { page: fakePage() })
    idle.destroy()
    expect(echo).toHaveBeenCalledTimes(1)
  })

  it('destroy() commits a name still being typed before it sends', () => {
    const editor = new PlanEditor(planOf(), echo, { page: fakePage() })
    editor.setDraft(1, 'Typed')
    editor.destroy()
    expect(echo).toHaveBeenCalledTimes(1)
    expect(echo.mock.calls[0]?.[0].sections[1]?.name).toBe('Typed')
  })

  it('pagehide sends the unsent edit with keepalive, even while an older request is still out', async () => {
    const { save, pending } = deferredSave()
    const page = fakePage()
    const editor = new PlanEditor(planOf(), save, { page })
    editor.rename(0, 'A')
    await vi.advanceTimersByTimeAsync(600)
    expect(save).toHaveBeenCalledTimes(1)
    editor.rename(0, 'AB')
    page.fire('pagehide')
    expect(save).toHaveBeenCalledTimes(2)
    expect(pending[1]?.plan.sections[0]?.name).toBe('AB')
    expect(save.mock.calls[1]?.[1]).toEqual({ keepalive: true })
    // The debounce that was pending is not sent a third time.
    await vi.advanceTimersByTimeAsync(600)
    expect(save).toHaveBeenCalledTimes(2)
    // Nothing new since: a second pagehide sends nothing.
    page.fire('pagehide')
    expect(save).toHaveBeenCalledTimes(2)
  })

  it('a tab going hidden sends the pending edit at once as an ordinary PUT; going visible with nothing pending does not', () => {
    const page = fakePage('visible')
    const editor = new PlanEditor(planOf(), echo, { page })
    editor.rename(0, 'A')
    page.fire('visibilitychange')
    expect(echo).not.toHaveBeenCalled()
    page.document.visibilityState = 'hidden'
    page.fire('visibilitychange')
    expect(echo).toHaveBeenCalledTimes(1)
    expect(echo.mock.calls[0]?.[1]).toEqual({})
    page.document.visibilityState = 'visible'
    page.fire('visibilitychange')
    expect(echo).toHaveBeenCalledTimes(1)
  })

  it('a gone job sends nothing on unload', async () => {
    const page = fakePage()
    const save = vi.fn().mockRejectedValue(new ApiError(410, 'expired'))
    const editor = new PlanEditor(planOf(), save, { page })
    editor.rename(0, 'x')
    await vi.advanceTimersByTimeAsync(600)
    expect(editor.gone).toBe(true)
    editor.rename(0, 'y')
    page.fire('pagehide')
    editor.destroy()
    expect(save).toHaveBeenCalledTimes(1)
  })
})

describe('PlanEditor large plans on leaving (gate r2)', () => {
  type Listener = (event: { preventDefault(): void }) => void
  function fakePage(visibility: DocumentVisibilityState = 'visible') {
    const listeners = new Map<string, Listener>()
    return {
      document: { visibilityState: visibility },
      addEventListener: (type: string, fn: Listener) => void listeners.set(type, fn),
      removeEventListener: (type: string) => void listeners.delete(type),
      fire: (type: string, event = { preventDefault: vi.fn() }) => {
        listeners.get(type)?.(event)
        return event
      },
    }
  }

  /** Chromium's rule: a keepalive body over 64 KiB is refused before it leaves (`TypeError: Failed to fetch`). */
  function browserFetch(seen: RequestInit[]) {
    return vi.fn(async (_url: string, init: RequestInit) => {
      seen.push(init)
      if (init.keepalive && new TextEncoder().encode(String(init.body)).byteLength > 65_536) {
        throw new TypeError('Failed to fetch')
      }
      return new Response(String(init.body), { status: 200 })
    })
  }

  it('the fixture is past the budget', () => {
    expect(new TextEncoder().encode(JSON.stringify(bigPlanOf())).byteLength).toBeGreaterThan(65_536)
    expect(KEEPALIVE_MAX_BYTES).toBeLessThan(65_536)
  })

  it('a >64 KB plan and a hidden tab: an ordinary PUT is made and the edit persists', async () => {
    const page = fakePage('visible')
    const editor = new PlanEditor(bigPlanOf(), echo, { page })
    editor.rename(0, 'Renamed while big')
    page.document.visibilityState = 'hidden'
    page.fire('visibilitychange')
    expect(echo).toHaveBeenCalledTimes(1)
    expect(echo.mock.calls[0]?.[1]).toEqual({})
    expect(echo.mock.calls[0]?.[0].sections[0]?.name).toBe('Renamed while big')
    await vi.advanceTimersByTimeAsync(0)
    expect(editor.dirty).toBe(false)
    expect(editor.error).toBeNull()
    expect(editor.plan.sections[0]?.name).toBe('Renamed while big')
  })

  it('a >64 KB plan on pagehide goes without keepalive, so the browser does not refuse it', async () => {
    const seen: RequestInit[] = []
    vi.stubGlobal('fetch', browserFetch(seen))
    try {
      const page = fakePage()
      const editor = new PlanEditor(bigPlanOf(), (p, o) => putPlan('job-1', p, o), { page })
      editor.setPage(0, 3)
      page.fire('pagehide')
      expect(seen).toHaveLength(1)
      expect(seen[0]?.keepalive).toBeFalsy()
      await vi.advanceTimersByTimeAsync(0)
      expect(editor.dirty).toBe(false)
      expect(editor.error).toBeNull()
      // A small plan still gets keepalive: the browser carries it after unload.
      const smallPage = fakePage()
      const small = new PlanEditor(planOf(), (p, o) => putPlan('job-1', p, o), { page: smallPage })
      small.setPage(0, 3)
      seen.length = 0
      smallPage.fire('pagehide')
      expect(seen[0]?.keepalive).toBe(true)
      await vi.advanceTimersByTimeAsync(0)
      expect(small.dirty).toBe(false)
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('a refused keepalive leaves the edit pending, and the next flush sends it again', async () => {
    const refusing = vi.fn(async (plan: Plan, opts?: SendOptions) => {
      if (opts?.keepalive) throw new TypeError('Failed to fetch')
      return plan
    })
    const page = fakePage('visible')
    const editor = new PlanEditor(planOf(), refusing, { page })
    editor.rename(0, 'Kept')
    page.fire('pagehide')
    expect(refusing).toHaveBeenCalledTimes(1)
    expect(refusing.mock.calls[0]?.[1]).toEqual({ keepalive: true })
    await vi.advanceTimersByTimeAsync(0)
    expect(editor.dirty).toBe(true)
    expect(editor.error?.code).toBe('network')
    // The page was not unloaded after all (bfcache, a cancelled close): the edit still goes out.
    page.fire('pagehide')
    expect(refusing).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(0)
    expect(editor.dirty).toBe(true)
    // The tab is back: the pending edit is flushed as an ordinary PUT and lands.
    page.fire('visibilitychange')
    expect(refusing).toHaveBeenCalledTimes(3)
    expect(refusing.mock.calls[2]?.[1]).toEqual({})
    await vi.advanceTimersByTimeAsync(0)
    expect(editor.dirty).toBe(false)
    expect(editor.plan.sections[0]?.name).toBe('Kept')
  })

  it('an unload sends once: the hidden-tab flush that follows pagehide on unload does not repeat it', async () => {
    const page = fakePage('visible')
    const editor = new PlanEditor(bigPlanOf(), echo, { page })
    editor.rename(0, 'Once')
    page.fire('pagehide')
    page.document.visibilityState = 'hidden'
    page.fire('visibilitychange')
    expect(echo).toHaveBeenCalledTimes(1)
    expect(echo.mock.calls[0]?.[1]).toEqual({ keepalive: false })
    await vi.advanceTimersByTimeAsync(0)
    expect(editor.dirty).toBe(false)
  })

  it('beforeunload asks the browser to warn while an edit is unsaved or still out, not when clean', async () => {
    const { save, release } = deferredSave()
    const page = fakePage()
    const editor = new PlanEditor(planOf(), save, { page })
    expect(page.fire('beforeunload').preventDefault).not.toHaveBeenCalled()
    editor.rename(0, 'Unsaved')
    expect(page.fire('beforeunload').preventDefault).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(600)
    expect(save).toHaveBeenCalledTimes(1)
    // Sent but not yet accepted: still at risk.
    expect(page.fire('beforeunload').preventDefault).toHaveBeenCalledTimes(1)
    release(0)
    await vi.advanceTimersByTimeAsync(0)
    expect(editor.dirty).toBe(false)
    expect(page.fire('beforeunload').preventDefault).not.toHaveBeenCalled()
    // A name still being typed counts as unsaved.
    editor.setDraft(1, 'Typing')
    expect(page.fire('beforeunload').preventDefault).toHaveBeenCalledTimes(1)
    editor.destroy()
  })
})

describe('PlanEditor name drafts', () => {
  it('a draft reaches the plan on commit only, and a same name is not an edit', async () => {
    const editor = new PlanEditor(planOf(), echo)
    editor.setDraft(0, '1 Foundations of Testing')
    editor.commitDraft()
    expect(editor.dirty).toBe(false)
    editor.setDraft(0, '1 Foundations')
    expect(editor.plan.sections[0]?.name).toBe('1 Foundations of Testing')
    expect(editor.dirty).toBe(false)
    // Focus moving to another row commits the first draft.
    editor.setDraft(1, 'Two')
    expect(editor.plan.sections[0]?.name).toBe('1 Foundations')
    expect(editor.draft).toEqual({ i: 1, name: 'Two' })
    editor.commitDraft()
    expect(editor.draft).toBeNull()
    await vi.advanceTimersByTimeAsync(600)
    expect(echo.mock.lastCall?.[0].sections.map((s) => s.name)).toEqual(['1 Foundations', 'Two', '3 Closing Chapter'])
  })
})

describe('PlanEditor manifest rows', () => {
  it('a merge drops the row of the section that absorbed the next; other edits leave the rows alone', () => {
    const editor = new PlanEditor(planOf(), echo)
    editor.rows = [rowOf(0, 'a'), rowOf(1, 'b'), rowOf(2, 'c')]
    editor.rename(2, 'C')
    editor.setPage(2, 5)
    expect(editor.rows).toHaveLength(3)
    editor.merge(1)
    expect(editor.rows.map((r) => r.index)).toEqual([0, 2])
    expect(editor.edited).toBe(true)
  })
})
