/**
 * Every error `code` the API can return, mapped to what a visitor reads. The API's own table is
 * `src/pdf_splitter/errors.py:MESSAGES`; `errors.test.ts` fails when a code there (or in Architecture
 * § API Interface) has no entry here.
 */
export const MESSAGES = {
  // POST /api/jobs
  too_large: 'This file is larger than the upload limit.',
  not_pdf: 'This file is not a PDF.',
  encrypted: 'This PDF is password-protected. Remove the password and try again.',
  too_many_pages: 'This PDF has more pages than the limit.',
  no_text_layer: "This PDF has no text layer (it looks scanned). OCR isn't supported yet — run OCR on it first, then upload it again.",
  unreadable: 'This PDF could not be read.',
  // STORY-012 adds these two (Architecture § API Interface)
  rate_limited: 'Too many uploads from your network. Wait a few minutes and try again.',
  disk_full: 'The server is full right now. Try again later.',
  // STORY-013 gate r1: the api's in-flight-uploads cap (503 + Retry-After)
  overloaded: 'The service is busy right now — try again in a few seconds.',
  // STORY-013 gate r2: the body stopped arriving (408)
  too_slow: 'The upload stalled and was abandoned. Check your connection and try again.',
  // /api/jobs/{id}/...
  not_found: 'There is no job at this address. Check the link.',
  expired: 'This job was deleted (files are kept 24 h).',
  not_ready: 'The job is not ready for this yet.',
  busy: 'The job is being processed. Try again when it has finished.',
  invalid: 'The request is not valid.',
  no_section: 'There is no section with that number in the list.',
  preview_failed: 'The preview could not be rendered.',
  internal: 'Something went wrong on our side. Try again.',
  // Client-side only: the request never got an answer.
  network: 'Could not reach the server. Check your connection and try again.',
} as const satisfies Record<string, string>

export type ErrorCode = keyof typeof MESSAGES

export const FALLBACK_MESSAGE = 'Something went wrong. Try again.'

export function isErrorCode(code: unknown): code is ErrorCode {
  return typeof code === 'string' && Object.hasOwn(MESSAGES, code)
}

export function messageFor(code: string | null | undefined): string {
  return isErrorCode(code) ? MESSAGES[code] : FALLBACK_MESSAGE
}
