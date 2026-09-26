import { fireEvent, render, screen } from '@testing-library/svelte'
import { describe, expect, it, vi } from 'vitest'
import type { Analysis, Section, Source } from '../lib/api'
import { analysisOf, sectionsOf } from '../lib/fixtures'
import { headingSections, initialPicker, type PickerState } from '../lib/plan'
import SourcePicker from './SourcePicker.svelte'

const BANDS = { header_band: 50, footer_band: 32 }

function mount(
  analysis: Analysis = analysisOf(),
  source: Source = 'outline',
  sections: Section[] = sectionsOf([['Saved', 2]]),
  picker: PickerState = initialPicker(analysis),
) {
  const onpick = vi.fn()
  const { rerender } = render(SourcePicker, { analysis, pages: 6, source, sections, picker, settings: BANDS, onpick })
  return { onpick, rerender }
}

const textbox = () => screen.getByRole('textbox') as HTMLTextAreaElement
const level = () => screen.getByLabelText('Level') as HTMLSelectElement

describe('SourcePicker (AC-1)', () => {
  it('lists every outline level with its count and sample titles, suggested first', () => {
    mount()
    const options = [...level().options].map((o) => o.text.replace(/\s+/g, ' ').trim())
    expect(options).toEqual([
      'Level 1 — 3 items: 1 Foundations of Testing · 2 Chapter Two: The Middle… · 3 Closing Chapter',
      'Level 2 — 3 items: 1.1 First Principles · 2.1 Second Principles of… · 3.1 Middle Matters',
    ])
    expect(level().value).toBe('1')
  })

  it('picking another outline level replaces the list with that level and reports the controls', async () => {
    const { onpick } = mount()
    await fireEvent.change(level(), { target: { value: '2' } })
    expect(onpick).toHaveBeenCalledTimes(1)
    const [source, sections, label, picker] = onpick.mock.calls[0]!
    expect(source).toBe('outline')
    expect((sections as Section[]).map((s) => s.name)).toEqual(['1.1 First Principles', '2.1 Second Principles of Wrapped Section Headings', '3.1 Middle Matters'])
    expect(label).toBe('the outline (level 2)')
    expect(picker).toEqual({ ...initialPicker(analysisOf()), outlineLevel: 2 })
  })

  it('disables Outline with an explanation when the PDF has none', () => {
    mount(analysisOf({ outline: { levels: [], items: [] }, suggested: { source: 'headings', level: 1 } }), 'headings')
    expect((screen.getByLabelText('Outline') as HTMLInputElement).disabled).toBe(true)
    expect(screen.getByText(/This PDF has no outline/)).toBeTruthy()
    expect(screen.queryByLabelText('Level')).toBeTruthy() // the headings level select is there instead
  })

  it('headings: threshold slider and level select filter with a live count', async () => {
    const { onpick, rerender } = mount(analysisOf(), 'headings')
    expect(screen.getByTestId('heading-count').textContent).toBe('3 sections')
    const slider = screen.getByRole('slider') as HTMLInputElement
    expect(slider.min).toBe('1')
    expect(slider.max).toBe('2')
    await fireEvent.change(level(), { target: { value: '0' } })
    expect(onpick).toHaveBeenLastCalledWith('headings', expect.any(Array), 'the detected headings', expect.objectContaining({ headingLevel: 0 }))
    expect((onpick.mock.lastCall?.[1] as Section[]).length).toBe(7)
    // The controls are the editor's: the count follows once the parent hands the new state back.
    await rerender({ picker: onpick.mock.lastCall?.[3] as PickerState })
    expect(screen.getByTestId('heading-count').textContent).toBe('7 sections')
    await fireEvent.input(slider, { target: { value: '1.5' } })
    expect((onpick.mock.lastCall?.[1] as Section[]).map((s) => s.page)).toEqual([1, 3, 4])
    expect(onpick.mock.lastCall?.[3]).toEqual(expect.objectContaining({ headingLevel: 0, threshold: 1.5 }))
    await rerender({ picker: onpick.mock.lastCall?.[3] as PickerState })
    expect(screen.getByTestId('heading-count').textContent).toBe('3 sections')
    expect(screen.getByText(/At least 1\.5× the body size \(14\.3 pt\)/)).toBeTruthy()
    expect(onpick).toHaveBeenCalledTimes(2)
  })

  it('headings: a max length and the current bands narrow the count; "Use these headings" re-applies (PRD scope 2)', async () => {
    const analysis = analysisOf()
    const picker = { ...initialPicker(analysis), headingLevel: 0 }
    const { onpick, rerender } = mount(analysis, 'headings', headingSections(analysis, { level: 0, threshold: 1 }), picker)
    expect(screen.getByTestId('heading-count').textContent).toBe('7 sections')
    expect(screen.queryByRole('button', { name: 'Use these headings' })).toBeNull()
    const maxLength = screen.getByLabelText('Max heading length (characters)') as HTMLInputElement
    expect(maxLength.value).toBe('90')
    await fireEvent.change(maxLength, { target: { value: '44' } })
    expect(onpick).toHaveBeenCalledTimes(1)
    expect((onpick.mock.lastCall?.[1] as Section[]).length).toBe(5)
    expect(onpick.mock.lastCall?.[3]).toEqual(expect.objectContaining({ maxLength: 44 }))
    // A header band grown over the chapters' y (90) drops them from the count, without touching the list.
    await rerender({ settings: { header_band: 100, footer_band: 32 } })
    expect(screen.getByTestId('heading-count').textContent).toBe('4 sections')
    expect(onpick).toHaveBeenCalledTimes(1)
    await fireEvent.click(screen.getByRole('button', { name: 'Use these headings' }))
    expect(onpick).toHaveBeenCalledTimes(2)
    expect((onpick.mock.lastCall?.[1] as Section[]).map((s) => s.page)).toEqual([1, 2, 3, 5])
  })

  it('disables Headings with an explanation when none were detected', () => {
    mount(analysisOf({ headings: { body_size: 9.5, levels: [], candidates: [] } }))
    expect((screen.getByLabelText('Headings') as HTMLInputElement).disabled).toBe(true)
    expect(screen.getByText(/No headings bigger than the body text/)).toBeTruthy()
  })

  it('shows the restored controls after an Undo, and choosing the old option again re-applies it (gate r1 F8)', async () => {
    const { onpick, rerender } = mount()
    await fireEvent.change(level(), { target: { value: '2' } })
    await rerender({ picker: { ...initialPicker(analysisOf()), outlineLevel: 2 } })
    expect(level().value).toBe('2')
    // Undo: the editor puts the controls back with the list.
    await rerender({ picker: initialPicker(analysisOf()) })
    expect(level().value).toBe('1')
    await fireEvent.change(level(), { target: { value: '2' } })
    expect(onpick).toHaveBeenCalledTimes(2)
    expect((onpick.mock.lastCall?.[1] as Section[]).map((s) => s.page)).toEqual([1, 2, 3])
  })

  it('switching to the paste list changes nothing and always seeds the box from the current list (gate r1 F5)', async () => {
    const { onpick, rerender } = mount()
    await fireEvent.click(screen.getByLabelText('Paste a list'))
    expect(onpick).not.toHaveBeenCalled()
    expect((screen.getByLabelText('Paste a list') as HTMLInputElement).checked).toBe(true)
    expect(textbox().value).toBe('Saved, 2')
    // Text left behind (with a bad line) is not what a later switch applies: back to the outline, then to paste again.
    await fireEvent.input(textbox(), { target: { value: 'Stale, 1\noops' } })
    await fireEvent.click(screen.getByLabelText('Outline'))
    expect(onpick).toHaveBeenCalledTimes(1)
    expect(onpick.mock.lastCall?.[0]).toBe('outline')
    await rerender({ sections: sectionsOf([['1 Foundations of Testing', 1]]) })
    expect(screen.queryByRole('textbox')).toBeNull()
    await fireEvent.click(screen.getByLabelText('Paste a list'))
    expect(onpick).toHaveBeenCalledTimes(1)
    expect(textbox().value).toBe('1 Foundations of Testing, 1')
  })

  it('the paste view yields to the saved source when that moves on (a pick elsewhere, an Undo)', async () => {
    const { rerender } = mount()
    await fireEvent.click(screen.getByLabelText('Paste a list'))
    expect(screen.getByRole('textbox')).toBeTruthy()
    await rerender({ source: 'headings' })
    expect(screen.queryByRole('textbox')).toBeNull()
    expect((screen.getByLabelText('Headings') as HTMLInputElement).checked).toBe(true)
  })

  it('shows parse errors per line and only applies a clean, non-empty list', async () => {
    const { onpick } = mount(analysisOf(), 'manual', [])
    const box = textbox()
    const use = () => screen.getByRole('button', { name: /Use this list/ }) as HTMLButtonElement
    expect(box.value).toBe('')
    expect(use().disabled).toBe(true)
    await fireEvent.input(box, { target: { value: 'Intro, 1\noops\nEnd, 9' } })
    const errors = screen.getAllByRole('listitem').map((li) => li.textContent)
    expect(errors).toEqual(['Line 2: Expected "Name, page" — oops', 'Line 3: Page 9 is past the last page (6) — End, 9'])
    expect(box.getAttribute('aria-invalid')).toBe('true')
    expect(use().disabled).toBe(true)
    expect(onpick).not.toHaveBeenCalled()
    await fireEvent.input(box, { target: { value: 'Intro, 1\nEnd, 5' } })
    expect(screen.queryAllByRole('listitem')).toEqual([])
    expect(use().textContent).toContain('Use this list (2)')
    await fireEvent.click(use())
    expect(onpick).toHaveBeenCalledWith(
      'manual',
      [
        { name: 'Intro', page: 1, heading: '' },
        { name: 'End', page: 5, heading: '' },
      ],
      'the pasted list',
      initialPicker(analysisOf()),
    )
  })
})
