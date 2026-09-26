import { fireEvent, render, screen } from '@testing-library/svelte'
import { describe, expect, it } from 'vitest'
import type { Plan } from '../lib/api'
import { PlanEditor } from '../lib/editor.svelte'
import { analysisOf, planOf, rowOf, sectionsOf } from '../lib/fixtures'
import SectionList from './SectionList.svelte'

const echo = async (p: Plan) => p

function mount(plan: Plan = planOf(), rows = [] as ReturnType<typeof rowOf>[], analysis = analysisOf()) {
  const editor = new PlanEditor(plan, echo, { debounceMs: 100_000 })
  editor.rows = rows
  const utils = render(SectionList, { editor, analysis })
  return { editor, analysis, ...utils }
}

const names = () => screen.getAllByLabelText(/^Name of section/).map((el) => (el as HTMLInputElement).value)

describe('SectionList (AC-3)', () => {
  it('keeps a typed name as a draft and commits it on blur', async () => {
    const { editor } = mount()
    const input = screen.getByLabelText('Name of section 2') as HTMLInputElement
    await fireEvent.focus(input)
    await fireEvent.input(input, { target: { value: 'Part Two ' } })
    expect(editor.plan.sections[1]?.name).toBe('2 Chapter Two: The Middle of the Synthetic Book')
    expect(editor.dirty).toBe(false)
    expect(input.value).toBe('Part Two ')
    // A save that lands meanwhile (normalized names) never rewrites the focused input.
    editor.plan.sections[1]!.name = 'Part Two'
    await Promise.resolve()
    expect(input.value).toBe('Part Two ')
    await fireEvent.blur(input)
    expect(editor.plan.sections[1]?.name).toBe('Part Two ')
    expect(editor.dirty).toBe(true)
  })

  it('Enter commits the draft', async () => {
    const { editor } = mount()
    const input = screen.getByLabelText('Name of section 1')
    await fireEvent.input(input, { target: { value: 'One' } })
    await fireEvent.keyDown(input, { key: 'Enter' })
    expect(editor.plan.sections[0]?.name).toBe('One')
    expect(editor.draft).toBeNull()
  })

  it('changes the page and shows the printed label when the PDF has one', async () => {
    const analysis = analysisOf({ pageLabels: ['i', 'ii', '1', '2', '3', '4'] })
    const { editor } = mount(planOf(), [], analysis)
    expect(screen.getByText('p. i')).toBeTruthy()
    await fireEvent.change(screen.getByLabelText('Start page of section 1'), { target: { value: '5' } })
    expect(editor.plan.sections[0]?.page).toBe(5)
    expect(screen.getByText('p. 3')).toBeTruthy()
    expect(screen.queryByText('p. i')).toBeNull()
  })

  it('shows no label hint when the PDF has none', () => {
    mount()
    expect(screen.queryByText(/^p\. /)).toBeNull()
  })

  it('deletes a section', async () => {
    const { editor } = mount()
    await fireEvent.click(screen.getByLabelText('Delete section 2'))
    expect(names()).toEqual(['1 Foundations of Testing', '3 Closing Chapter'])
    expect(editor.plan.sections).toHaveLength(2)
    expect(screen.getByText('2 sections')).toBeTruthy()
  })

  it('merges with the next section; the last one cannot', async () => {
    const { editor } = mount(planOf({ overrides: { '2': { endCut: 700 } } }))
    const merges = screen.getAllByRole('button', { name: 'Merge ↓' })
    expect((merges[2] as HTMLButtonElement).disabled).toBe(true)
    await fireEvent.click(merges[1]!)
    expect(names()).toEqual(['1 Foundations of Testing', '2 Chapter Two: The Middle of the Synthetic Book'])
    expect(editor.plan.overrides).toEqual({ '1': { endCut: 700 } })
  })

  it('adds a section by name and page, in book order, and validates the form', async () => {
    const { editor } = mount()
    const name = screen.getByLabelText('New section')
    const page = screen.getByLabelText('Starts on sheet')
    await fireEvent.click(screen.getByRole('button', { name: 'Add section' }))
    expect(screen.getByRole('alert').textContent).toBe('Give the section a name.')
    await fireEvent.input(name, { target: { value: 'Interlude' } })
    await fireEvent.input(page, { target: { value: '9' } })
    await fireEvent.click(screen.getByRole('button', { name: 'Add section' }))
    expect(screen.getByRole('alert').textContent).toBe('The page must be between 1 and 6.')
    await fireEvent.input(page, { target: { value: '2' } })
    await fireEvent.click(screen.getByRole('button', { name: 'Add section' }))
    expect(screen.queryByRole('alert')).toBeNull()
    expect(names()).toEqual(['1 Foundations of Testing', 'Interlude', '2 Chapter Two: The Middle of the Synthetic Book', '3 Closing Chapter'])
    expect(editor.plan.sections[1]).toEqual({ name: 'Interlude', page: 2, heading: '' })
    expect((name as HTMLInputElement).value).toBe('')
  })

  it('badges the last cut flags, never span-clamped on the last section, and drops stale rows', async () => {
    const rows = [
      rowOf(0, '1 Foundations of Testing', ['heading-not-found', 'span-clamped']),
      rowOf(2, '3 Closing Chapter', ['span-clamped', 'override']),
    ]
    const { editor } = mount(planOf(), rows)
    const first = screen.getByLabelText('Name of section 1').closest('li')!
    expect([...first.querySelectorAll('.badge')].map((b) => b.textContent)).toEqual(['Heading not found on its page', 'Reached the page limit'])
    const lastRow = screen.getByLabelText('Name of section 3').closest('li')!
    expect([...lastRow.querySelectorAll('.badge')].map((b) => b.textContent)).toEqual(['Manual cut'])
    // Deleting the first section shifts indexes: the rows no longer describe these sections.
    await fireEvent.click(screen.getByLabelText('Delete section 1'))
    expect(editor.plan.sections).toHaveLength(2)
    expect(document.querySelectorAll('.badge')).toHaveLength(0)
  })

  it('a merge drops the badge of the section that absorbed the next one (its row describes the old span)', async () => {
    const rows = [rowOf(0, '1 Foundations of Testing', ['heading-not-found']), rowOf(1, '2 Chapter Two: The Middle of the Synthetic Book', ['leak'])]
    const { editor } = mount(planOf(), rows)
    expect(document.querySelectorAll('.badge')).toHaveLength(2)
    await fireEvent.click(screen.getAllByRole('button', { name: 'Merge ↓' })[0]!)
    expect(names()).toEqual(['1 Foundations of Testing', '3 Closing Chapter'])
    expect(document.querySelectorAll('.badge')).toHaveLength(0)
    expect(editor.rows.map((r) => r.index)).toEqual([1])
  })

  it('selects a section for the preview', async () => {
    const { editor } = mount()
    await fireEvent.click(screen.getByLabelText('Select section 2'))
    expect(editor.selected).toBe(1)
    expect(screen.getByLabelText('Name of section 2').closest('li')!.classList.contains('selected')).toBe(true)
  })

  it('says so when the list is empty', () => {
    mount(planOf({ source: 'manual', sections: sectionsOf([]) }))
    expect(screen.getByText('0 sections')).toBeTruthy()
    expect(screen.getByText(/No sections yet/)).toBeTruthy()
  })
})
