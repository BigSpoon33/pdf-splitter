import { fireEvent, render, screen } from '@testing-library/svelte'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, type Plan, type SectionPlan, type SectionPlanRequest } from '../lib/api'
import { PlanEditor } from '../lib/editor.svelte'
import { analysisOf, planOf } from '../lib/fixtures'
import PagePreview from './PagePreview.svelte'

const ID = 'AbCdEfGhIjKlMnOpQrStUv'
const SIZE = analysisOf().size[0]! // 522.7 × 789.6
const SPLIT = 0.487 * SIZE.W

/**
 * The engine's views for headed_book as tests/test_api_e2e.py::test_section_plan_returns_the_engine_view_with_rects
 * pins section 1 (top of sheet 3 → mid-right of sheet 4, one removed region below the end cut); the others are the
 * same shape. An override in the request is applied the way `apply_overrides` does: the keys present replace the
 * engine's, and the view is flagged `override`.
 */
const VIEWS: SectionPlan[] = [
  { pages: [1, 2], startCut: null, startCol: 'full', endCut: 300, endCol: 'full', flags: [], notes: [], rects: [[2, [0, 300, 522.7, 757.6]]] },
  { pages: [3, 4], startCut: null, startCol: 'full', endCut: 400, endCol: 'right', flags: [], notes: [], rects: [[4, [254.5, 400, 522.7, 757.6]]] },
  { pages: [4, 6], startCut: 400, startCol: 'right', endCut: null, endCol: 'full', flags: ['span-clamped'], notes: [], rects: [[4, [0, 0, 522.7, 400]]] },
]

function viewFor(i: number, body: SectionPlanRequest): SectionPlan {
  const base = VIEWS[i]!
  if (!body.override) return { ...base, flags: [...base.flags] }
  return { ...base, ...body.override, flags: [...base.flags, 'override'] }
}

const page = { addEventListener() {}, removeEventListener() {} }

function mount(over: { plan?: Plan; loadSheet?: (id: string, n: number) => Promise<Blob>; loadSectionPlan?: typeof defaultLoad; single?: boolean } = {}) {
  const save = vi.fn(async (plan: Plan) => plan)
  const plan = over.plan ?? planOf()
  if (over.single) plan.settings.single_column = true
  const editor = new PlanEditor(plan, save, { debounceMs: 20, page })
  const loadSectionPlan = vi.fn(over.loadSectionPlan ?? defaultLoad)
  const loadSheet = vi.fn(over.loadSheet ?? (async () => new Blob(['png'])))
  const utils = render(PagePreview, { editor, analysis: analysisOf(), id: ID, loadSectionPlan, loadSheet, dpi: 72 })
  return { editor, save, loadSectionPlan, loadSheet, ...utils }
}
const defaultLoad = async (_id: string, i: number, body: SectionPlanRequest) => viewFor(i, body)

const slider = (name: string) => screen.getByRole('slider', { name }) as unknown as SVGRectElement
const valueOf = (name: string) => Number(slider(name).getAttribute('aria-valuenow'))
const svgs = () => [...document.querySelectorAll('svg')]
const removed = (svg: Element) => [...svg.querySelectorAll('[data-testid=removed]')].map((r) => ['x', 'y', 'width', 'height'].map((a) => Number(r.getAttribute(a))))
const lastRequest = (fn: ReturnType<typeof vi.fn>) => fn.mock.calls.at(-1)?.[2] as SectionPlanRequest

/** A pointer drag over the (mocked) 1:1 image: client pixels are page points. */
async function drag(el: Element, from: [number, number], to: [number, number]) {
  await fireEvent.pointerDown(el, { clientX: from[0], clientY: from[1], button: 0, pointerId: 1 })
  await fireEvent.pointerMove(el, { clientX: to[0], clientY: to[1], pointerId: 1 })
  await fireEvent.pointerUp(el, { clientX: to[0], clientY: to[1], pointerId: 1 })
}

const urls = { create: vi.fn((_: Blob) => `blob:sheet-${urls.create.mock.calls.length}`), revoke: vi.fn() }

beforeEach(() => {
  urls.create.mockClear()
  urls.revoke.mockClear()
  Object.assign(URL, { createObjectURL: urls.create, revokeObjectURL: urls.revoke })
  // jsdom lays nothing out: the image box is the page at 1:1, so a client position is a page point.
  vi.spyOn(Element.prototype, 'getBoundingClientRect').mockReturnValue({ left: 0, top: 0, width: SIZE.W, height: SIZE.H, right: SIZE.W, bottom: SIZE.H, x: 0, y: 0, toJSON: () => ({}) })
})
afterEach(() => {
  vi.restoreAllMocks()
})

describe('PagePreview (AC-1)', () => {
  it('asks for nothing until a section is selected, then shows its first and last sheet with the overlay', async () => {
    const { editor, loadSectionPlan, loadSheet } = mount()
    expect(screen.getByText('Select a section to preview where it will be cut.')).toBeTruthy()
    expect(loadSectionPlan).not.toHaveBeenCalled()

    editor.select(1)
    await vi.waitFor(() => expect(screen.getByText('Last sheet 4')).toBeTruthy())
    expect(screen.getByText('First sheet 3')).toBeTruthy()
    expect(screen.getByText(/Section 2: 2 Chapter Two/)).toBeTruthy()
    // The local plan is what the engine is asked about: its settings, and `null` for "no override of ours".
    expect(loadSectionPlan).toHaveBeenCalledTimes(1)
    expect(loadSectionPlan.mock.calls[0]?.slice(0, 3)).toEqual([ID, 1, { settings: planOf().settings, override: null }])
    expect(loadSheet.mock.calls.map((c) => c.slice(0, 3))).toEqual([
      [ID, 3, 72],
      [ID, 4, 72],
    ])
    await vi.waitFor(() => expect(document.querySelectorAll('img')).toHaveLength(2))
    expect([...document.querySelectorAll('img')].map((i) => i.getAttribute('src'))).toEqual(['blob:sheet-1', 'blob:sheet-2'])

    const [first, last] = svgs()
    expect(svgs()).toHaveLength(2)
    expect(first!.getAttribute('viewBox')).toBe('0 0 522.7 789.6')
    // Gutter at column_split × W and the bands on both sheets; the removed region only on the sheet it names.
    for (const svg of [first!, last!]) {
      expect(Number(svg.querySelector('.gutter')!.getAttribute('x1'))).toBeCloseTo(SPLIT, 6)
      const bands = [...svg.querySelectorAll('.band')].map((b) => [Number(b.getAttribute('y')), Number(b.getAttribute('height'))])
      expect(bands).toEqual([
        [0, 50],
        [789.6 - 32, 32],
      ])
    }
    expect(removed(first!)).toEqual([])
    expect(removed(last!)).toEqual([[254.5, 400, 522.7 - 254.5, 757.6 - 400]])
    // The start cut is absent (the section starts at the top of sheet 3): a dashed line at 0 that can become one.
    expect(valueOf('Start cut of section 2')).toBe(0)
    expect(slider('Start cut of section 2').getAttribute('aria-valuetext')).toMatch(/^no start cut/)
    expect(first!.querySelector('.cut')!.classList.contains('absent')).toBe(true)
    // The end cut at 400 pt in the right column spans from the gutter to the right edge.
    expect(valueOf('End cut of section 2')).toBe(400)
    expect(slider('End cut of section 2').getAttribute('aria-valuetext')).toBe('400 pt, right column')
    const endLine = last!.querySelector('.cut')!
    expect(Number(endLine.getAttribute('x1'))).toBeCloseTo(SPLIT, 6)
    expect(Number(endLine.getAttribute('x2'))).toBe(522.7)
    expect(screen.getByRole('button', { name: 'Remove end cut' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Remove start cut' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Reset cut' })).toBeNull()
  })

  it('shows one sheet when the section is a single sheet, and no gutter for a one-column book', async () => {
    const { editor } = mount({
      single: true,
      loadSectionPlan: async () => ({ pages: [3, 3], startCut: 100, startCol: 'full', endCut: 500, endCol: 'full', flags: ['heading-not-found'], notes: [], rects: [] }),
    })
    editor.select(1)
    await vi.waitFor(() => expect(screen.getByText('Only sheet 3')).toBeTruthy())
    expect(svgs()).toHaveLength(1)
    expect(document.querySelector('.gutter')).toBeNull()
    expect(valueOf('Start cut of section 2')).toBe(100)
    expect(valueOf('End cut of section 2')).toBe(500)
    expect(screen.getByText('Heading not found on its page')).toBeTruthy()
  })

  it('clears the preview and releases the images when the selection goes', async () => {
    const { editor } = mount()
    editor.select(0)
    await vi.waitFor(() => expect(document.querySelectorAll('img')).toHaveLength(2))
    editor.select(null)
    await vi.waitFor(() => expect(screen.getByText('Select a section to preview where it will be cut.')).toBeTruthy())
    expect(urls.revoke.mock.calls.map((c) => c[0])).toEqual(['blob:sheet-1', 'blob:sheet-2'])
  })
})

describe('PagePreview dragging (AC-2, AC-3)', () => {
  it('a dragged cut follows the pointer, and the release saves the override with the column the drag started in and re-fetches once', async () => {
    const { editor, save, loadSectionPlan } = mount()
    editor.select(1)
    await vi.waitFor(() => expect(screen.getByText('Last sheet 4')).toBeTruthy())
    const end = slider('End cut of section 2')
    await fireEvent.pointerDown(end, { clientX: 100, clientY: 400, button: 0, pointerId: 1 })
    await fireEvent.pointerMove(end, { clientX: 100, clientY: 350.3, pointerId: 1 })
    // Live: the line is at the pointer (half points), in the column the drag started in — nothing saved yet.
    expect(valueOf('End cut of section 2')).toBe(350.5)
    expect(slider('End cut of section 2').getAttribute('aria-valuetext')).toBe('350.5 pt, left column')
    expect(Number(svgs()[1]!.querySelector('.cut')!.getAttribute('x2'))).toBeCloseTo(SPLIT, 6)
    expect(editor.plan.overrides).toEqual({})
    expect(loadSectionPlan).toHaveBeenCalledTimes(1)

    await fireEvent.pointerUp(end, { clientX: 100, clientY: 350.3, pointerId: 1 })
    expect(editor.plan.overrides).toEqual({ '1': { endCut: 350.5, endCol: 'left' } })
    expect(loadSectionPlan).toHaveBeenCalledTimes(2)
    expect(lastRequest(loadSectionPlan)).toEqual({ settings: planOf().settings, override: { endCut: 350.5, endCol: 'left' } })
    await vi.waitFor(() => expect(screen.getByText('Manual cut')).toBeTruthy()) // the view's `override` flag
    await vi.waitFor(() => expect(save).toHaveBeenCalledTimes(1))
    expect(save.mock.calls[0]?.[0].overrides).toEqual({ '1': { endCut: 350.5, endCol: 'left' } })
    // The save's landing asks nothing new: the release's request already showed this override.
    await vi.waitFor(() => expect(editor.saves).toBe(1))
    expect(loadSectionPlan).toHaveBeenCalledTimes(2)
    expect(screen.getByRole('button', { name: 'Reset cut' })).toBeTruthy()
  })

  it('an absent cut becomes one by dragging its line; a second drag keeps the other cut’s keys', async () => {
    const { editor } = mount()
    editor.select(1)
    await vi.waitFor(() => expect(screen.getByText('Last sheet 4')).toBeTruthy())
    await drag(slider('Start cut of section 2'), [300, 0], [300, 120.2])
    expect(editor.plan.overrides).toEqual({ '1': { startCut: 120, startCol: 'right' } })
    await drag(slider('End cut of section 2'), [400, 400], [400, 600])
    expect(editor.plan.overrides).toEqual({ '1': { startCut: 120, startCol: 'right', endCut: 600, endCol: 'right' } })
    // Past the page the value clamps to the page (to the half point, so it never exceeds H); one-column cuts are full width.
    await drag(slider('End cut of section 2'), [400, 600], [400, 5000])
    expect(editor.plan.overrides['1']?.endCut).toBe(789.5)
    editor.setSetting('single_column', true)
    await drag(slider('End cut of section 2'), [400, 789], [400, 700])
    expect(editor.plan.overrides['1']).toMatchObject({ endCut: 700, endCol: 'full' })
  })

  it('the gutter and the bands are book-wide settings (AC-3)', async () => {
    const { editor, loadSectionPlan } = mount()
    editor.select(1)
    await vi.waitFor(() => expect(screen.getByText('Last sheet 4')).toBeTruthy())
    const [gutter] = screen.getAllByRole('slider', { name: 'Gutter' })
    expect(gutter!.getAttribute('aria-valuenow')).toBe(String(Math.round(SPLIT)))
    await drag(gutter!, [SPLIT, 300], [SIZE.W / 2, 300])
    expect(editor.plan.settings.column_split).toBe(0.5)
    expect(lastRequest(loadSectionPlan).settings?.column_split).toBe(0.5)
    // Every sheet's overlay follows the new setting, and the gutter never leaves the API's 20–80 % range.
    for (const svg of svgs()) expect(Number(svg.querySelector('.gutter')!.getAttribute('x1'))).toBeCloseTo(SIZE.W / 2, 6)
    await drag(gutter!, [SIZE.W / 2, 300], [0, 300])
    expect(editor.plan.settings.column_split).toBe(0.2)

    const [header] = screen.getAllByRole('slider', { name: 'Header band' })
    await drag(header!, [100, 50], [100, 72.4])
    expect(editor.plan.settings.header_band).toBe(72.5)
    expect(header!.getAttribute('aria-valuenow')).toBe('72.5')
    const [footer] = screen.getAllByRole('slider', { name: 'Footer band' })
    await drag(footer!, [100, SIZE.H - 32], [100, SIZE.H - 60])
    expect(editor.plan.settings.footer_band).toBe(60)
    // Bands are bounded like the API bounds them (0–200 pt).
    await drag(header!, [100, 72.5], [100, 500])
    expect(editor.plan.settings.header_band).toBe(200)
  })
})

describe('PagePreview keyboard (AC-5)', () => {
  it('arrow keys nudge the focused line by 1 pt, 10 with Shift; the view refreshes when the save lands', async () => {
    const { editor, save, loadSectionPlan } = mount()
    editor.select(1)
    await vi.waitFor(() => expect(screen.getByText('Last sheet 4')).toBeTruthy())
    const end = slider('End cut of section 2')
    expect(end.getAttribute('tabindex')).toBe('0')
    await fireEvent.keyDown(end, { key: 'ArrowUp' })
    expect(editor.plan.overrides).toEqual({ '1': { endCut: 399, endCol: 'right' } })
    expect(valueOf('End cut of section 2')).toBe(399)
    await fireEvent.keyDown(slider('End cut of section 2'), { key: 'ArrowDown', shiftKey: true })
    expect(editor.plan.overrides['1']?.endCut).toBe(409)
    await fireEvent.keyDown(slider('End cut of section 2'), { key: 'ArrowLeft' })
    await fireEvent.keyDown(slider('End cut of section 2'), { key: 'Enter' })
    expect(editor.plan.overrides['1']?.endCut).toBe(409)
    // A run of keystrokes is one save and, once it lands, one request with the override.
    expect(loadSectionPlan).toHaveBeenCalledTimes(1)
    await vi.waitFor(() => expect(save).toHaveBeenCalledTimes(1))
    await vi.waitFor(() => expect(loadSectionPlan).toHaveBeenCalledTimes(2))
    expect(lastRequest(loadSectionPlan).override).toEqual({ endCut: 409, endCol: 'right' })

    // An absent start cut starts from the top of the sheet; the gutter moves along x; the footer band grows upward.
    await fireEvent.keyDown(slider('Start cut of section 2'), { key: 'ArrowDown', shiftKey: true })
    expect(editor.plan.overrides['1']).toMatchObject({ startCut: 10, startCol: 'full' })
    const gutter = screen.getAllByRole('slider', { name: 'Gutter' })[0]!
    await fireEvent.keyDown(gutter, { key: 'ArrowRight', shiftKey: true })
    expect(editor.plan.settings.column_split).toBeCloseTo((SPLIT + 10) / SIZE.W, 3)
    const footer = screen.getAllByRole('slider', { name: 'Footer band' })[0]!
    await fireEvent.keyDown(footer, { key: 'ArrowUp' })
    expect(editor.plan.settings.footer_band).toBe(33)
    const header = screen.getAllByRole('slider', { name: 'Header band' })[0]!
    await fireEvent.keyDown(header, { key: 'ArrowUp' })
    expect(editor.plan.settings.header_band).toBe(49)
  })
})

describe('PagePreview reset and removal (AC-4)', () => {
  it('Reset deletes the override and previews the engine’s own plan; Remove sends an explicit null for one cut', async () => {
    const { editor, loadSectionPlan, save } = mount({ plan: planOf({ overrides: { '1': { endCut: 350, endCol: 'left' } } }) })
    editor.select(1)
    await vi.waitFor(() => expect(screen.getByText('Last sheet 4')).toBeTruthy())
    expect(lastRequest(loadSectionPlan).override).toEqual({ endCut: 350, endCol: 'left' })
    expect(valueOf('End cut of section 2')).toBe(350)
    await fireEvent.click(screen.getByRole('button', { name: 'Reset cut' }))
    expect(editor.plan.overrides).toEqual({})
    expect(loadSectionPlan).toHaveBeenCalledTimes(2)
    expect(lastRequest(loadSectionPlan).override).toBeNull()
    await vi.waitFor(() => expect(valueOf('End cut of section 2')).toBe(400))
    expect(screen.queryByRole('button', { name: 'Reset cut' })).toBeNull()
    await vi.waitFor(() => expect(save).toHaveBeenCalledTimes(1))
    expect(save.mock.calls[0]?.[0].overrides).toEqual({})

    await fireEvent.click(screen.getByRole('button', { name: 'Remove end cut' }))
    expect(editor.plan.overrides).toEqual({ '1': { endCut: null } })
    expect(lastRequest(loadSectionPlan).override).toEqual({ endCut: null })
    await vi.waitFor(() => expect(slider('End cut of section 2').getAttribute('aria-valuetext')).toMatch(/^no end cut/))
    expect(screen.queryByRole('button', { name: 'Remove end cut' })).toBeNull()
  })
})

describe('PagePreview refresh and failures', () => {
  it('asks again after a save that changed the section list, not after one that only renamed', async () => {
    const { editor, loadSectionPlan } = mount()
    editor.select(1)
    await vi.waitFor(() => expect(loadSectionPlan).toHaveBeenCalledTimes(1))
    editor.rename(0, 'Intro')
    await vi.waitFor(() => expect(editor.saves).toBe(1))
    expect(loadSectionPlan).toHaveBeenCalledTimes(1)
    editor.remove(2)
    await vi.waitFor(() => expect(editor.saves).toBe(2))
    await vi.waitFor(() => expect(loadSectionPlan).toHaveBeenCalledTimes(2))
    // A layout change made elsewhere reaches the preview the same way, with the new settings.
    editor.setSetting('header_band', 80)
    await vi.waitFor(() => expect(loadSectionPlan).toHaveBeenCalledTimes(3))
    expect(lastRequest(loadSectionPlan).settings?.header_band).toBe(80)
  })

  it('a 410 from a sheet render means the job is gone: the expired message, no Retry, and the editor stops', async () => {
    const { editor } = mount({ loadSheet: async () => Promise.reject(new ApiError(410, 'expired')) })
    editor.select(0)
    await vi.waitFor(() => expect(screen.getByRole('alert').textContent).toContain('This job was deleted (files are kept 24 h).'))
    expect(screen.queryByRole('button', { name: 'Retry' })).toBeNull()
    expect(editor.gone).toBe(true)
    expect(editor.error?.code).toBe('expired')
  })

  it('a failed render is preview_failed with a Retry that asks once more; another section’s sheets never stay up under it', async () => {
    const loadSectionPlan = vi.fn(defaultLoad).mockImplementationOnce(defaultLoad).mockRejectedValueOnce(new ApiError(500, 'preview_failed'))
    const { editor } = mount({ loadSectionPlan })
    editor.select(0)
    await vi.waitFor(() => expect(screen.getByText('Last sheet 2')).toBeTruthy())
    editor.select(1)
    await vi.waitFor(() => expect(screen.getByRole('alert').textContent).toContain('The preview could not be rendered.'))
    expect(editor.gone).toBe(false)
    expect(screen.queryByText('Last sheet 2')).toBeNull()
    expect(svgs()).toHaveLength(0)
    await fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    await vi.waitFor(() => expect(screen.getByText('Last sheet 4')).toBeTruthy())
    expect(loadSectionPlan).toHaveBeenCalledTimes(3)
    expect(screen.queryByRole('alert')).toBeNull()
  })
})
