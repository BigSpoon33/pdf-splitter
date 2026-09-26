import { describe, expect, it } from 'vitest'
// The expected codes come from the sources of truth, not a copy: the API's table and Architecture § API Interface.
import errorsPy from '../../../src/pdf_splitter/errors.py?raw'
import architecture from '../../../docs/Architecture.md?raw'
import { FALLBACK_MESSAGE, MESSAGES, messageFor } from './errors'

function apiCodes(): string[] {
  const table = /^MESSAGES = \{\n([\s\S]*?)^\}/m.exec(errorsPy)?.[1] ?? ''
  return [...table.matchAll(/^\s+"([a-z_]+)":/gm)].map((m) => m[1]!)
}

function architectureUploadCodes(): string[] {
  // The `201: {id, state:"queued"}   400 not_pdf|… · 413 … · 429 rate_limited · 503 disk_full` line.
  const line = /^\s+201: \{id, state:"queued"\}.*$/m.exec(architecture)?.[0] ?? ''
  return [...line.matchAll(/\b\d{3} ([a-z_|]+)/g)].flatMap((m) => m[1]!.split('|'))
}

describe('errors.ts', () => {
  it('reads the API table (sanity: the parser found all 18 codes)', () => {
    expect(apiCodes()).toHaveLength(18)
    expect(architectureUploadCodes()).toEqual(
      expect.arrayContaining(['not_pdf', 'too_large', 'rate_limited', 'disk_full']),
    )
  })

  it.each([...new Set([...apiCodes(), ...architectureUploadCodes()])])('has a message for %s', (code) => {
    expect(Object.hasOwn(MESSAGES, code)).toBe(true)
    const msg = messageFor(code)
    expect(msg).not.toBe(FALLBACK_MESSAGE)
    expect(msg.length).toBeGreaterThan(10)
  })

  it('says a deleted job is gone the way Architecture § web words it', () => {
    expect(messageFor('expired')).toBe('This job was deleted (files are kept 24 h).')
  })

  it('says plainly that OCR is not supported (PRD AC-8)', () => {
    expect(messageFor('no_text_layer')).toBe(
      "This PDF has no text layer (it looks scanned). OCR isn't supported yet — run OCR on it first, then upload it again.",
    )
  })

  it.each(['no_such_code', '', null, undefined, 'toString', '__proto__'])('falls back for %s', (code) => {
    expect(messageFor(code)).toBe(FALLBACK_MESSAGE)
  })
})
