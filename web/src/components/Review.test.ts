import { fireEvent, render, screen } from '@testing-library/svelte'
import { describe, expect, it, vi } from 'vitest'
import { ApiError, type ManifestRow, type Plan, type SaveOptions, type SectionPlan } from '../lib/api'
import { analysisOf, planOf, rowOf } from '../lib/fixtures'
import Review from './Review.svelte'

const ID = 'AbCdEfGhIjKlMnOpQrStUv'

type Save = (id: string, plan: Plan, opts?: SaveOptions) => Promise<Plan>
type LoadPlan = (id: string, signal: AbortSignal) => Promise<Plan>
type LoadManifest = (id: string, signal: AbortSignal) => Promise<ManifestRow[]>

const ROWS = [rowOf(0, '1 Foundations of Testing', ['heading-not-found'])]
const noCut: LoadManifest = async () => Promise.reject(new ApiError(409, 'not_ready'))
const cut: LoadManifest = async () => ROWS

const VIEW: SectionPlan = { pages: [3, 4], startCut: null, startCol: 'full', endCut: 400, endCol: 'right', flags: [], notes: [], rects: [[4, [254.5, 400, 522.7, 757.6]]] }

/** Renders with fake loaders; returns the DEFAULT save mock (a test that passes its own keeps its own handle). */
function mount(over: { jobState?: 'review' | 'done'; save?: Save; loadPlan?: LoadPlan; loadManifest?: LoadManifest } = {}) {
  const save = vi.fn<Save>(async (_id, plan) => plan)
  const loadAnalysis = vi.fn(async () => analysisOf())
  const loadPlan = vi.fn<LoadPlan>(async () => planOf())
  const loadManifest = vi.fn<LoadManifest>(over.loadManifest ?? noCut)
  const loadSectionPlan = vi.fn(async (_id: string, _i: number) => VIEW)
  const loadSheet = vi.fn(async (_id: string, _n: number) => new Blob(['png']))
  const utils = render(Review, {
    id: ID,
    pages: 6,
    jobState: over.jobState ?? 'review',
    loadAnalysis,
    loadPlan: over.loadPlan ?? loadPlan,
    loadManifest,
    loadSectionPlan,
    loadSheet,
    save: over.save ?? save,
    debounceMs: 20,
  })
  return { save, loadAnalysis, loadPlan, loadManifest, loadSectionPlan, loadSheet, ...utils }
}

const names = () => screen.getAllByLabelText(/^Name of section/).map((el) => (el as HTMLInputElement).value)
const badges = () => [...document.querySelectorAll('.badge')].map((b) => b.textContent)
const STALE = /cut again to refresh the files/

describe('Review', () => {
  it('loads the analysis and plan for the job and renders the saved list; 409 on the manifest is just "no badges"', async () => {
    const { loadAnalysis, loadPlan, loadManifest } = mount()
    await vi.waitFor(() => expect(screen.getByText('3 sections')).toBeTruthy())
    expect(loadAnalysis).toHaveBeenCalledWith(ID, expect.any(AbortSignal))
    expect(loadPlan).toHaveBeenCalledWith(ID, expect.any(AbortSignal))
    expect(loadManifest).toHaveBeenCalledWith(ID, expect.any(AbortSignal))
    expect(names()).toEqual(['1 Foundations of Testing', '2 Chapter Two: The Middle of the Synthetic Book', '3 Closing Chapter'])
    expect((screen.getByLabelText('Outline') as HTMLInputElement).checked).toBe(true)
    expect(badges()).toEqual([])
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.queryByText(STALE)).toBeNull()
  })

  it('switching source replaces the list with an Undo toast; Undo restores it; both persist (AC-2, AC-6)', async () => {
    const { save } = mount()
    await vi.waitFor(() => expect(screen.getByText('3 sections')).toBeTruthy())
    await fireEvent.click(screen.getByLabelText('Headings'))
    expect(names()).toEqual(['Foundations of Testing', 'Chapter Two: The Middle of the Synthetic Book', 'Closing Chapter'])
    expect(screen.getByText('Section list replaced from the detected headings.')).toBeTruthy()
    await vi.waitFor(() => expect(save).toHaveBeenCalledTimes(1))
    expect(save.mock.calls[0]?.[1]).toMatchObject({ source: 'headings' })
    expect(save.mock.calls[0]?.[1].sections.map((s) => s.name)).toEqual(['Foundations of Testing', 'Chapter Two: The Middle of the Synthetic Book', 'Closing Chapter'])

    await fireEvent.click(screen.getByRole('button', { name: 'Undo' }))
    expect(names()).toEqual(['1 Foundations of Testing', '2 Chapter Two: The Middle of the Synthetic Book', '3 Closing Chapter'])
    expect(screen.queryByRole('button', { name: 'Undo' })).toBeNull()
    expect((screen.getByLabelText('Outline') as HTMLInputElement).checked).toBe(true)
    await vi.waitFor(() => expect(save).toHaveBeenCalledTimes(2))
    expect(save.mock.calls[1]?.[1]).toMatchObject({ source: 'outline' })
  })

  it('Undo puts the picker controls back and keeps a setting changed after the switch (gate r1 F6, F8)', async () => {
    const { save } = mount()
    await vi.waitFor(() => expect(screen.getByText('3 sections')).toBeTruthy())
    const level = () => screen.getByLabelText('Level') as HTMLSelectElement
    await fireEvent.change(level(), { target: { value: '2' } })
    expect(names()).toEqual(['1.1 First Principles', '2.1 Second Principles of Wrapped Section Headings', '3.1 Middle Matters'])
    expect(level().value).toBe('2')
    await fireEvent.change(screen.getByLabelText('Header band (pt)'), { target: { value: '60' } })
    await fireEvent.click(screen.getByRole('button', { name: 'Undo' }))
    expect(names()).toEqual(['1 Foundations of Testing', '2 Chapter Two: The Middle of the Synthetic Book', '3 Closing Chapter'])
    expect(level().value).toBe('1')
    expect((screen.getByLabelText('Header band (pt)') as HTMLInputElement).value).toBe('60')
    await vi.waitFor(() => expect(save).toHaveBeenLastCalledWith(ID, expect.objectContaining({ source: 'outline', settings: expect.objectContaining({ header_band: 60 }) }), expect.anything()))
    // Level 2 is a change again, so choosing it re-applies it.
    await fireEvent.change(level(), { target: { value: '2' } })
    expect(names()).toEqual(['1.1 First Principles', '2.1 Second Principles of Wrapped Section Headings', '3.1 Middle Matters'])
  })

  it('renders a 422 next to the offending control and clears it on the next success (AC-5)', async () => {
    const save = vi
      .fn()
      .mockRejectedValueOnce(
        new ApiError(422, 'invalid', 'invalid', [
          { loc: ['body', 'sections', 1, 'page'], msg: 'page must be at most 6, the last page of the book', type: 'value_error' },
          { loc: ['body', 'settings', 'header_band'], msg: 'Input should be less than or equal to 200', type: 'less_than_equal' },
        ]),
      )
      .mockImplementation(async (_id: string, plan: Plan) => plan)
    mount({ save })
    await vi.waitFor(() => expect(screen.getByText('3 sections')).toBeTruthy())
    const page = screen.getByLabelText('Start page of section 2') as HTMLInputElement
    await fireEvent.change(page, { target: { value: '99' } })
    await vi.waitFor(() => expect(save).toHaveBeenCalledTimes(1))
    await vi.waitFor(() => expect(page.getAttribute('aria-invalid')).toBe('true'))
    expect(document.getElementById(page.getAttribute('aria-describedby')!)?.textContent).toBe('page must be at most 6, the last page of the book')
    const band = screen.getByLabelText('Header band (pt)') as HTMLInputElement
    expect(band.getAttribute('aria-invalid')).toBe('true')
    expect(document.getElementById(band.getAttribute('aria-describedby')!)?.textContent).toBe('Input should be less than or equal to 200')
    expect(screen.getByLabelText('Start page of section 1').getAttribute('aria-invalid')).toBe('false')

    await fireEvent.change(page, { target: { value: '5' } })
    await vi.waitFor(() => expect(save).toHaveBeenCalledTimes(2))
    await vi.waitFor(() => expect(page.getAttribute('aria-invalid')).toBe('false'))
    expect(band.getAttribute('aria-invalid')).toBe('false')
  })

  it('shows the last cut flags as badges after a cut, and a busy answer with a Retry', async () => {
    const save = vi.fn().mockRejectedValue(new ApiError(409, 'busy'))
    const { loadManifest } = mount({ jobState: 'done', save, loadManifest: cut })
    await vi.waitFor(() => expect(screen.getByText('Heading not found on its page')).toBeTruthy())
    expect(loadManifest).toHaveBeenCalledWith(ID, expect.any(AbortSignal))
    expect(screen.queryByText(STALE)).toBeNull() // the files match the plan until an edit
    const name = screen.getByLabelText('Name of section 3')
    await fireEvent.input(name, { target: { value: 'End' } })
    await fireEvent.blur(name)
    await vi.waitFor(() => expect(screen.getByText('The job is being processed. Try again when it has finished.')).toBeTruthy())
    expect(screen.getByText(STALE)).toBeTruthy()
    await fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    await vi.waitFor(() => expect(save).toHaveBeenCalledTimes(2))
  })

  it('a job back in review after an edit still shows the last cut badges and the stale note on reload (gate r1 F4)', async () => {
    mount({ jobState: 'review', loadManifest: cut })
    await vi.waitFor(() => expect(screen.getByText('Heading not found on its page')).toBeTruthy())
    expect(badges()).toEqual(['Heading not found on its page'])
    expect(screen.getByText(STALE)).toBeTruthy()
  })

  it("shows the load error's message in place", async () => {
    mount({ loadPlan: vi.fn(async () => Promise.reject(new ApiError(410, 'expired'))) })
    await vi.waitFor(() => expect(screen.getByRole('alert').textContent).toBe('This job was deleted (files are kept 24 h).'))
  })

  it('selecting a section in the list previews it: the engine is asked with the local settings, both sheets load (STORY-010 AC-1)', async () => {
    Object.assign(URL, { createObjectURL: () => 'blob:sheet', revokeObjectURL: () => {} })
    const { loadSectionPlan, loadSheet } = mount()
    await vi.waitFor(() => expect(screen.getByText('3 sections')).toBeTruthy())
    expect(screen.getByText('Select a section to preview where it will be cut.')).toBeTruthy()
    await fireEvent.click(screen.getByLabelText('Select section 2'))
    await vi.waitFor(() => expect(screen.getByText('Last sheet 4')).toBeTruthy())
    expect(loadSectionPlan).toHaveBeenCalledWith(ID, 1, { settings: planOf().settings, override: null }, expect.any(AbortSignal))
    expect(loadSheet.mock.calls.map((c) => c[1])).toEqual([3, 4])
    expect(screen.getByRole('slider', { name: 'End cut of section 2' }).getAttribute('aria-valuenow')).toBe('400')
  })

  it('sends a pending edit when the page is left rather than dropping it (gate r1 F7)', async () => {
    const { save, unmount } = mount()
    await vi.waitFor(() => expect(screen.getByText('3 sections')).toBeTruthy())
    await fireEvent.change(screen.getByLabelText('Start page of section 1'), { target: { value: '2' } })
    expect(save).not.toHaveBeenCalled() // the debounce has not elapsed
    unmount()
    expect(save).toHaveBeenCalledTimes(1)
    expect(save.mock.calls[0]?.[1].sections[0]?.page).toBe(2)
  })
})
