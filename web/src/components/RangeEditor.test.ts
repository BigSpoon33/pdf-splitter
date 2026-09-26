import { fireEvent, render, screen } from '@testing-library/svelte'
import { describe, expect, it, vi } from 'vitest'
import type { Plan } from '../lib/api'
import { PlanEditor } from '../lib/editor.svelte'
import { planOf } from '../lib/fixtures'
import RangeEditor from './RangeEditor.svelte'

const PAGES = 30

function mount(plan: Plan = planOf({ source: 'ranges', sections: [] })) {
  const save = vi.fn(async (p: Plan) => p)
  const editor = new PlanEditor(plan, save, { debounceMs: 10 })
  const utils = render(RangeEditor, { editor, pages: PAGES })
  const field = () => screen.getByLabelText(/pages to keep/i) as HTMLInputElement
  const errors = () => [...document.querySelectorAll('#ranges-errors li')].map((li) => li.textContent?.replace(/\s+/g, ' ').trim())
  const type = (text: string) => fireEvent.input(field(), { target: { value: text } })
  return { save, editor, field, errors, type, ...utils }
}

const spans = (editor: PlanEditor) => editor.plan.sections.map((s) => [s.page, s.endPage])
const names = (editor: PlanEditor) => editor.plan.sections.map((s) => s.name)

describe('RangeEditor', () => {
  it('turns the typed ranges into ranges sections with default names and saves them (AC-4)', async () => {
    const { editor, save, type, errors } = mount()
    await type('1-10, 15-20, 5-7')
    expect(errors()).toEqual([])
    expect(editor.plan.source).toBe('ranges')
    expect(spans(editor)).toEqual([
      [1, 10],
      [15, 20],
      [5, 7],
    ])
    expect(names(editor)).toEqual(['Pages 1–10', 'Pages 15–20', 'Pages 5–7'])
    expect(editor.plan.overrides).toEqual({})
    await vi.waitFor(() => expect(save).toHaveBeenCalledTimes(1))
    expect(save.mock.calls[0]?.[0].sections[0]).toEqual({ name: 'Pages 1–10', page: 1, heading: '', endPage: 10 })
    await type('1-10, 15-20, 5-7, 40')
    expect(errors()).toEqual(['40 — Page 40 is past the last page (30)'])
    // A single page: "Page 40", not "Pages 40–40".
    await type('1-10, 30')
    expect(names(editor)).toEqual(['Pages 1–10', 'Page 30'])
  })

  it('shows one error per bad token as you type and leaves the plan at the last good text', async () => {
    const { editor, save, type, field, errors } = mount()
    await type('1-10')
    await vi.waitFor(() => expect(save).toHaveBeenCalledTimes(1))
    await type('1-10, 15-2')
    expect(errors()).toEqual(['15-2 — "15-2" is reversed: the first page must come first'])
    expect(field().getAttribute('aria-invalid')).toBe('true')
    expect(spans(editor)).toEqual([[1, 10]])
    await type('1-10, 15-2x, 0, 31')
    expect(errors()).toEqual([
      '15-2x — Expected a page (40) or a range (1-10)',
      '0 — Pages start at 1',
      '31 — Page 31 is past the last page (30)',
    ])
    expect(spans(editor)).toEqual([[1, 10]])
    await type('1-10, 15-20')
    expect(errors()).toEqual([])
    expect(field().getAttribute('aria-invalid')).toBe('false')
    expect(spans(editor)).toEqual([
      [1, 10],
      [15, 20],
    ])
    await vi.waitFor(() => expect(save).toHaveBeenCalledTimes(2))
    // The text stays as typed (spacing included): only its spans went to the plan.
    expect(field().value).toBe('1-10, 15-20')
  })

  it('"every N pages" fills the field and the list (PRD AC-14: every 10 of 30 → 3)', async () => {
    const { editor, field } = mount()
    const every = screen.getByLabelText('Pages per file') as HTMLInputElement
    const fill = () => screen.getByRole('button', { name: 'Fill the ranges' }) as HTMLButtonElement
    expect(fill().disabled).toBe(true)
    await fireEvent.input(every, { target: { value: '10' } })
    expect(fill().disabled).toBe(false)
    await fireEvent.click(fill())
    expect(field().value).toBe('1-10, 11-20, 21-30')
    expect(spans(editor)).toEqual([
      [1, 10],
      [11, 20],
      [21, 30],
    ])
    await fireEvent.input(every, { target: { value: '7' } })
    await fireEvent.click(fill())
    expect(field().value).toBe('1-7, 8-14, 15-21, 22-28, 29-30')
    expect(editor.plan.sections).toHaveLength(5)
    await fireEvent.input(every, { target: { value: '0' } })
    expect(fill().disabled).toBe(true)
  })

  it('shows the saved spans on load and follows a row deleted from the list; a rename survives retyping', async () => {
    const plan = planOf({
      source: 'ranges',
      sections: [
        { name: 'Intro', page: 1, heading: '', endPage: 10 },
        { name: 'Pages 15–20', page: 15, heading: '', endPage: 20 },
        { name: 'Page 30', page: 30, heading: '', endPage: 30 },
      ],
    })
    const { editor, field, type, save } = mount(plan)
    expect(field().value).toBe('1-10, 15-20, 30')
    expect(save).not.toHaveBeenCalled()
    // Retyping around the first span keeps its rename; the changed span gets the default name.
    await type('1-10, 15-21, 30')
    expect(names(editor)).toEqual(['Intro', 'Pages 15–21', 'Page 30'])
    // A delete from the list (SectionList's button in the app) changes the plan behind the field: it re-reads.
    editor.remove(1)
    await vi.waitFor(() => expect(field().value).toBe('1-10, 30'))
    expect(names(editor)).toEqual(['Intro', 'Page 30'])
  })

  it('does not save when the text is the same spans written differently', async () => {
    const { editor, save, type } = mount(planOf({ source: 'ranges', sections: [{ name: 'Intro', page: 1, heading: '', endPage: 10 }] }))
    await type('1 - 10')
    await type('1–10,')
    expect(editor.dirty).toBe(false)
    expect(save).not.toHaveBeenCalled()
    expect(names(editor)).toEqual(['Intro'])
  })
})
