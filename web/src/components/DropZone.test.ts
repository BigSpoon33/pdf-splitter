import { fireEvent, render, screen } from '@testing-library/svelte'
import { describe, expect, it, vi } from 'vitest'
import { ApiError, type CreatedJob } from '../lib/api'
import { MESSAGES } from '../lib/errors'
import DropZone, { precheck } from './DropZone.svelte'

const MAX = 1000

function file(name: string, size: number, type: string): File {
  return new File([new Uint8Array(size)], name, { type })
}

function setup(upload = vi.fn<(f: File, p: (x: number) => void, mode: string) => Promise<CreatedJob>>()) {
  const oncreated = vi.fn<(id: string) => void>()
  render(DropZone, { oncreated, upload, maxBytes: MAX })
  const input = screen.getByLabelText(/drop a pdf here/i, { selector: 'input' }) as HTMLInputElement
  return { upload, oncreated, input, zone: screen.getByTestId('dropzone') }
}

async function pick(input: HTMLInputElement, f: File) {
  Object.defineProperty(input, 'files', { value: [f], configurable: true })
  await fireEvent.change(input)
}

async function drop(zone: HTMLElement, f: File) {
  await fireEvent.drop(zone, { dataTransfer: { files: [f], dropEffect: 'none' } })
}

describe('precheck', () => {
  it.each([
    ['book.pdf', 10, 'application/pdf', null],
    ['BOOK.PDF', 10, '', null],
    ['book', 10, 'application/pdf', null],
    ['book.png', 10, 'image/png', 'not_pdf'],
    ['book.pdf.exe', 10, '', 'not_pdf'],
    ['empty.pdf', 0, 'application/pdf', 'not_pdf'],
    ['big.pdf', MAX + 1, 'application/pdf', 'too_large'],
    ['limit.pdf', MAX, 'application/pdf', null],
  ])('%s (%i bytes, %s) → %s', (name, size, type, code) => {
    expect(precheck(file(name, size, type), MAX)).toBe(code)
  })
})

describe('DropZone', () => {
  it('rejects a non-PDF picked by click without uploading', async () => {
    const { upload, input } = setup()
    await pick(input, file('cover.png', 10, 'image/png'))
    expect(screen.getByRole('alert').textContent).toBe(MESSAGES.not_pdf)
    expect(upload).not.toHaveBeenCalled()
  })

  it('rejects an oversized PDF dropped on the zone without uploading', async () => {
    const { upload, zone } = setup()
    await drop(zone, file('big.pdf', MAX + 1, 'application/pdf'))
    expect(screen.getByRole('alert').textContent).toBe(MESSAGES.too_large)
    expect(upload).not.toHaveBeenCalled()
  })

  it('uploads a picked PDF, shows the percentage, and hands over the job id', async () => {
    let finish!: (job: CreatedJob) => void
    let report!: (x: number) => void
    const { upload, oncreated, input } = setup(
      vi.fn((_f: File, onProgress: (x: number) => void) => {
        report = onProgress
        return new Promise<CreatedJob>((resolve) => (finish = resolve))
      }),
    )
    await pick(input, file('book.pdf', 10, 'application/pdf'))
    expect(upload).toHaveBeenCalledOnce()
    expect(screen.getByText('Uploading… 0%')).toBeTruthy()

    report(0.42)
    await vi.waitFor(() => expect(screen.getByText('Uploading… 42%')).toBeTruthy())
    expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe('42')

    finish({ id: 'job-id-1234567890abcdef', state: 'queued' })
    await vi.waitFor(() => expect(oncreated).toHaveBeenCalledWith('job-id-1234567890abcdef'))
  })

  it('uploads a dropped PDF', async () => {
    const { upload, oncreated, zone } = setup(vi.fn(async () => ({ id: 'dropped', state: 'queued' as const })))
    await drop(zone, file('book.pdf', 10, 'application/pdf'))
    expect(upload).toHaveBeenCalledOnce()
    await vi.waitFor(() => expect(oncreated).toHaveBeenCalledWith('dropped'))
  })

  it('hands its mode to the upload — chapters unless told otherwise (ADR-009 as built)', async () => {
    const { upload, input } = setup()
    await pick(input, file('book.pdf', 10, 'application/pdf'))
    expect(upload.mock.calls[0]?.[2]).toBe('chapters')

    const ranges = vi.fn<(f: File, p: (x: number) => void, mode: string) => Promise<CreatedJob>>(async () => ({ id: 'r', state: 'queued' }))
    render(DropZone, { oncreated: vi.fn(), upload: ranges, maxBytes: MAX, mode: 'ranges' })
    const inputs = screen.getAllByLabelText(/drop a pdf here/i, { selector: 'input' }) as HTMLInputElement[]
    await pick(inputs[1]!, file('book.pdf', 10, 'application/pdf'))
    expect(ranges.mock.calls[0]?.[2]).toBe('ranges')
  })

  it("renders the API's rejection in place (a PNG renamed .pdf)", async () => {
    const { oncreated, input } = setup(
      vi.fn(async () => {
        throw new ApiError(400, 'not_pdf', 'The file is not a PDF.')
      }),
    )
    await pick(input, file('x.pdf', 10, 'application/pdf'))
    await vi.waitFor(() => expect(screen.getByRole('alert').textContent).toBe(MESSAGES.not_pdf))
    expect(oncreated).not.toHaveBeenCalled()
    expect(screen.getByText('Drop a PDF here')).toBeTruthy()
  })

  it('shows the fallback message for an unknown error code', async () => {
    const { input } = setup(
      vi.fn(async () => {
        throw new ApiError(418, 'teapot')
      }),
    )
    await pick(input, file('x.pdf', 10, 'application/pdf'))
    await vi.waitFor(() => expect(screen.getByRole('alert').textContent).toBe('Something went wrong. Try again.'))
  })
})
