import { fireEvent, render, screen } from '@testing-library/svelte'
import { describe, expect, it, vi } from 'vitest'
import type { CreatedJob } from '../lib/api'
import type { JobMode } from '../lib/route'
import Home from './Home.svelte'

function setup() {
  const upload = vi.fn<(f: File, p: (x: number) => void) => Promise<CreatedJob>>(async () => ({ id: 'NewJobId', state: 'queued' }))
  const oncreated = vi.fn<(id: string, mode?: JobMode) => void>()
  render(Home, { oncreated, upload })
  const inputs = screen.getAllByLabelText(/drop a pdf here/i, { selector: 'input' }) as HTMLInputElement[]
  return { upload, oncreated, inputs }
}

async function pick(input: HTMLInputElement) {
  Object.defineProperty(input, 'files', { value: [new File([new Uint8Array(10)], 'book.pdf', { type: 'application/pdf' })], configurable: true })
  await fireEvent.change(input)
}

describe('Home', () => {
  it('offers the two entry points, each with its own drop zone (AC-1)', () => {
    const { inputs } = setup()
    const chapters = screen.getByRole('region', { name: 'Split by chapters' })
    const ranges = screen.getByRole('region', { name: 'Split by page ranges' })
    expect(inputs).toHaveLength(2)
    expect(chapters.contains(inputs[0]!)).toBe(true)
    expect(ranges.contains(inputs[1]!)).toBe(true)
  })

  it('reports which entry point the upload came through', async () => {
    const { inputs, oncreated, upload } = setup()
    await pick(inputs[1]!)
    await vi.waitFor(() => expect(oncreated).toHaveBeenCalledWith('NewJobId', 'ranges'))
    await pick(inputs[0]!)
    await vi.waitFor(() => expect(oncreated).toHaveBeenCalledTimes(2))
    expect(oncreated).toHaveBeenLastCalledWith('NewJobId')
    expect(upload).toHaveBeenCalledTimes(2)
  })
})
