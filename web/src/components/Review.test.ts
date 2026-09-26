import { fireEvent, render, screen } from '@testing-library/svelte'
import { describe, expect, it, vi } from 'vitest'
import { ApiError, type Plan } from '../lib/api'
import { analysisOf, planOf, rowOf } from '../lib/fixtures'
import Review from './Review.svelte'

const ID = 'AbCdEfGhIjKlMnOpQrStUv'

type Save = (id: string, plan: Plan) => Promise<Plan>
type LoadPlan = (id: string, signal: AbortSignal) => Promise<Plan>

/** Renders with fake loaders; returns the DEFAULT save mock (a test that passes its own keeps its own handle). */
function mount(over: { done?: boolean; save?: Save; loadPlan?: LoadPlan } = {}) {
  const save = vi.fn<Save>(async (_id, plan) => plan)
  const loadAnalysis = vi.fn(async () => analysisOf())
  const loadPlan = vi.fn<LoadPlan>(async () => planOf())
  const loadManifest = vi.fn(async () => [rowOf(0, '1 Foundations of Testing', ['heading-not-found'])])
  render(Review, {
    id: ID,
    pages: 6,
    done: over.done ?? false,
    loadAnalysis,
    loadPlan: over.loadPlan ?? loadPlan,
    loadManifest,
    save: over.save ?? save,
    debounceMs: 20,
  })
  return { save, loadAnalysis, loadPlan, loadManifest }
}

const names = () => screen.getAllByLabelText(/^Name of section/).map((el) => (el as HTMLInputElement).value)

describe('Review', () => {
  it('loads the analysis and plan for the job and renders the saved list', async () => {
    const { loadAnalysis, loadPlan, loadManifest } = mount()
    await vi.waitFor(() => expect(screen.getByText('3 sections')).toBeTruthy())
    expect(loadAnalysis).toHaveBeenCalledWith(ID, expect.any(AbortSignal))
    expect(loadPlan).toHaveBeenCalledWith(ID, expect.any(AbortSignal))
    expect(loadManifest).not.toHaveBeenCalled() // no cut yet
    expect(names()).toEqual(['1 Foundations of Testing', '2 Chapter Two: The Middle of the Synthetic Book', '3 Closing Chapter'])
    expect((screen.getByLabelText('Outline') as HTMLInputElement).checked).toBe(true)
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
    const { loadManifest } = mount({ done: true, save })
    await vi.waitFor(() => expect(screen.getByText('Heading not found on its page')).toBeTruthy())
    expect(loadManifest).toHaveBeenCalledWith(ID, expect.any(AbortSignal))
    await fireEvent.input(screen.getByLabelText('Name of section 3'), { target: { value: 'End' } })
    await vi.waitFor(() => expect(screen.getByText('The job is being processed. Try again when it has finished.')).toBeTruthy())
    expect(screen.getByText(/cut again to refresh the files/)).toBeTruthy()
    await fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    await vi.waitFor(() => expect(save).toHaveBeenCalledTimes(2))
  })

  it("shows the load error's message in place", async () => {
    mount({ loadPlan: vi.fn(async () => Promise.reject(new ApiError(410, 'expired'))) })
    await vi.waitFor(() => expect(screen.getByRole('alert').textContent).toBe('This job was deleted (files are kept 24 h).'))
  })
})
