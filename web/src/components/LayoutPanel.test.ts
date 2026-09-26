import { fireEvent, render, screen } from '@testing-library/svelte'
import { describe, expect, it } from 'vitest'
import type { Plan } from '../lib/api'
import { PlanEditor } from '../lib/editor.svelte'
import { planOf } from '../lib/fixtures'
import LayoutPanel from './LayoutPanel.svelte'

function mount(plan: Plan = planOf()) {
  const editor = new PlanEditor(plan, async (p) => p, { debounceMs: 100_000 })
  render(LayoutPanel, { editor })
  return editor
}

describe('LayoutPanel (AC-4)', () => {
  it('toggles one/two columns and disables the gutter for one column', async () => {
    const editor = mount()
    const gutter = screen.getByLabelText('Gutter (% of the page width)') as HTMLInputElement
    expect(gutter.value).toBe('48.7')
    expect(gutter.disabled).toBe(false)
    await fireEvent.click(screen.getByLabelText('One column'))
    expect(editor.plan.settings.single_column).toBe(true)
    expect(gutter.disabled).toBe(true)
    await fireEvent.click(screen.getByLabelText('Two columns'))
    expect(editor.plan.settings.single_column).toBe(false)
  })

  it('gutter % becomes column_split, bands and sizes are points', async () => {
    const editor = mount()
    await fireEvent.change(screen.getByLabelText('Gutter (% of the page width)'), { target: { value: '55' } })
    await fireEvent.change(screen.getByLabelText('Header band (pt)'), { target: { value: '60' } })
    await fireEvent.change(screen.getByLabelText('Footer band (pt)'), { target: { value: '0' } })
    await fireEvent.change(screen.getByLabelText('Heading size (pt, at least)'), { target: { value: '14' } })
    await fireEvent.change(screen.getByLabelText('Heading wrap gap (pt)'), { target: { value: '30' } })
    expect(editor.plan.settings).toEqual({
      column_split: 0.55,
      single_column: false,
      header_band: 60,
      footer_band: 0,
      heading_min_size: 14,
      heading_wrap_gap: 30,
    })
  })

  it("shows the engine's 16 pt wrap gap until one is set, and ignores a blank field", async () => {
    const editor = mount()
    const gap = screen.getByLabelText('Heading wrap gap (pt)') as HTMLInputElement
    expect(gap.value).toBe('16')
    expect(editor.plan.settings.heading_wrap_gap).toBeUndefined()
    await fireEvent.change(gap, { target: { value: '' } })
    expect(editor.plan.settings.heading_wrap_gap).toBeUndefined()
    expect(editor.dirty).toBe(false)
  })
})
