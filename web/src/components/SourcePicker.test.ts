import { fireEvent, render, screen } from '@testing-library/svelte'
import { describe, expect, it, vi } from 'vitest'
import type { Analysis, Section, Source } from '../lib/api'
import { analysisOf, sectionsOf } from '../lib/fixtures'
import SourcePicker from './SourcePicker.svelte'

function mount(analysis: Analysis = analysisOf(), source: Source = 'outline', sections: Section[] = sectionsOf([['Saved', 2]])) {
  const onpick = vi.fn()
  const { rerender } = render(SourcePicker, { analysis, pages: 6, source, sections, onpick })
  return { onpick, rerender }
}

describe('SourcePicker (AC-1)', () => {
  it('lists every outline level with its count and sample titles, suggested first', () => {
    mount()
    const options = [...(screen.getByLabelText('Level') as HTMLSelectElement).options].map((o) => o.text.replace(/\s+/g, ' ').trim())
    expect(options).toEqual([
      'Level 1 — 3 items: 1 Foundations of Testing · 2 Chapter Two: The Middle… · 3 Closing Chapter',
      'Level 2 — 3 items: 1.1 First Principles · 2.1 Second Principles of… · 3.1 Middle Matters',
    ])
    expect((screen.getByLabelText('Level') as HTMLSelectElement).value).toBe('1')
  })

  it('picking another outline level replaces the list with that level', async () => {
    const { onpick } = mount()
    await fireEvent.change(screen.getByLabelText('Level'), { target: { value: '2' } })
    expect(onpick).toHaveBeenCalledTimes(1)
    const [source, sections, label] = onpick.mock.calls[0]!
    expect(source).toBe('outline')
    expect((sections as Section[]).map((s) => s.name)).toEqual(['1.1 First Principles', '2.1 Second Principles of Wrapped Section Headings', '3.1 Middle Matters'])
    expect(label).toBe('the outline (level 2)')
  })

  it('disables Outline with an explanation when the PDF has none', () => {
    mount(analysisOf({ outline: { levels: [], items: [] }, suggested: { source: 'headings', level: 1 } }), 'headings')
    expect((screen.getByLabelText('Outline') as HTMLInputElement).disabled).toBe(true)
    expect(screen.getByText(/This PDF has no outline/)).toBeTruthy()
    expect(screen.queryByLabelText('Level')).toBeTruthy() // the headings level select is there instead
  })

  it('headings: threshold slider and level select filter with a live count', async () => {
    const { onpick } = mount(analysisOf(), 'headings')
    expect(screen.getByTestId('heading-count').textContent).toBe('3 sections')
    const slider = screen.getByRole('slider') as HTMLInputElement
    expect(slider.min).toBe('1')
    expect(slider.max).toBe('2')
    await fireEvent.change(screen.getByLabelText('Level'), { target: { value: '0' } })
    expect(screen.getByTestId('heading-count').textContent).toBe('7 sections')
    expect(onpick).toHaveBeenLastCalledWith('headings', expect.any(Array), 'the detected headings')
    expect((onpick.mock.lastCall?.[1] as Section[]).length).toBe(7)
    await fireEvent.input(slider, { target: { value: '1.5' } })
    expect(screen.getByTestId('heading-count').textContent).toBe('3 sections')
    expect(screen.getByText(/At least 1\.5× the body size \(14\.3 pt\)/)).toBeTruthy()
    expect((onpick.mock.lastCall?.[1] as Section[]).map((s) => s.page)).toEqual([1, 3, 4])
    expect(onpick).toHaveBeenCalledTimes(2)
  })

  it('disables Headings with an explanation when none were detected', () => {
    mount(analysisOf({ headings: { body_size: 9.5, levels: [], candidates: [] } }))
    expect((screen.getByLabelText('Headings') as HTMLInputElement).disabled).toBe(true)
    expect(screen.getByText(/No headings bigger than the body text/)).toBeTruthy()
  })

  it('switching to the paste list pre-fills it with the current sections and picks them as manual', async () => {
    const { onpick, rerender } = mount()
    await fireEvent.click(screen.getByLabelText('Paste a list'))
    expect(onpick).toHaveBeenCalledWith('manual', [{ name: 'Saved', page: 2, heading: '' }], 'the pasted list')
    // The parent saves the pick and hands the new source back; the box then shows the list it was filled with.
    await rerender({ source: 'manual' })
    expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe('Saved, 2')
  })

  it('shows parse errors per line and only applies a clean, non-empty list', async () => {
    const { onpick } = mount(analysisOf(), 'manual', [])
    const box = screen.getByRole('textbox')
    const use = () => screen.getByRole('button', { name: /Use this list/ }) as HTMLButtonElement
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
    )
  })
})
