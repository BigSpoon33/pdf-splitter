import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, createJob, errorFromBody, getAnalysis, getJob, getManifest, getPlan, isGone, putPlan, type JobStatus, type Plan } from './api'
import { MESSAGES } from './errors'

// The status shape pinned by tests/test_api_e2e.py::test_job_status_shape_hides_the_requeue_marker_and_shows_failures.
const STATUS: JobStatus = {
  id: 'AbCdEfGhIjKlMnOpQrStUv',
  state: 'running',
  kind: 'analyze',
  progress: 2,
  total: 6,
  queue_position: null,
  message: 'Indexing pages',
  error_code: null,
  expires_at: '2026-09-26T12:00:00+00:00',
  filename: 'My Book.pdf',
  pages: 6,
}

function stubFetch(status: number, body: string) {
  const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => new Response(body, { status }))
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('getJob', () => {
  it('GETs /api/jobs/{id} and returns the status', async () => {
    const fetchMock = stubFetch(200, JSON.stringify(STATUS))
    await expect(getJob(STATUS.id)).resolves.toEqual(STATUS)
    expect(fetchMock.mock.calls[0]?.[0]).toBe(`/api/jobs/${STATUS.id}`)
  })

  it('escapes the id into one path segment', async () => {
    const fetchMock = stubFetch(200, JSON.stringify(STATUS))
    await getJob('../x')
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/jobs/..%2Fx')
  })

  it.each([
    [410, 'expired', true],
    [404, 'not_found', true],
    [500, 'internal', false],
  ])('turns a %i {code: %s} body into an ApiError (gone: %s)', async (status, code, gone) => {
    stubFetch(status, JSON.stringify({ code, message: 'server text', request_id: 'r-12345678' }))
    const err = await getJob('x').catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err).toMatchObject({ status, code, message: 'server text' })
    expect(isGone(err)).toBe(gone)
    expect((err as ApiError).userMessage).toBe(MESSAGES[code as keyof typeof MESSAGES])
  })

  it('maps a network failure to the network code', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(new TypeError('Failed to fetch'))))
    await expect(getJob('x')).rejects.toMatchObject({ status: 0, code: 'network' })
  })
})

describe('errorFromBody', () => {
  it('keeps field errors and the request id', () => {
    const errors = [{ loc: ['body', 'sections', 0, 'page'], msg: 'out of range', type: 'value_error' }]
    const err = errorFromBody(422, JSON.stringify({ code: 'invalid', message: 'm', errors }))
    expect(err).toMatchObject({ status: 422, code: 'invalid', errors })
  })

  it.each([
    [413, 'too_large'],
    [502, 'internal'],
  ])('maps a non-JSON %i to %s', (status, code) => {
    expect(errorFromBody(status, '<html>Bad gateway</html>').code).toBe(code)
  })
})

class FakeXhr {
  static last: FakeXhr
  method = ''
  url = ''
  status = 0
  responseText = ''
  body: unknown
  upload: { onprogress: ((e: ProgressEvent) => void) | null } = { onprogress: null }
  onload: (() => void) | null = null
  onerror: (() => void) | null = null
  onabort: (() => void) | null = null
  constructor() {
    FakeXhr.last = this
  }
  open(method: string, url: string) {
    this.method = method
    this.url = url
  }
  setRequestHeader() {}
  send(body: unknown) {
    this.body = body
  }
  respond(status: number, text: string) {
    this.status = status
    this.responseText = text
    this.onload?.()
  }
}

describe('createJob', () => {
  const pdf = new File(['%PDF-1.7'], 'book.pdf', { type: 'application/pdf' })
  const make = () => new FakeXhr() as unknown as XMLHttpRequest

  it('POSTs the file as multipart `file`, reports progress, resolves with the id', async () => {
    const progress = vi.fn()
    const done = createJob(pdf, progress, make)
    const xhr = FakeXhr.last
    expect([xhr.method, xhr.url]).toEqual(['POST', '/api/jobs'])
    expect((xhr.body as FormData).get('file')).toBeInstanceOf(File)
    xhr.upload.onprogress?.({ lengthComputable: true, loaded: 50, total: 200 } as ProgressEvent)
    expect(progress).toHaveBeenCalledWith(0.25)
    xhr.respond(201, JSON.stringify({ id: 'new-id', state: 'queued' }))
    await expect(done).resolves.toEqual({ id: 'new-id', state: 'queued' })
  })

  it('rejects with the upload error body (tests/test_upload.py::assert_rejected)', async () => {
    const done = createJob(pdf, undefined, make)
    FakeXhr.last.respond(400, JSON.stringify({ code: 'encrypted', message: 'The PDF is password-protected.' }))
    await expect(done).rejects.toMatchObject({ status: 400, code: 'encrypted' })
  })

  it('rejects with the network code when the connection drops', async () => {
    const done = createJob(pdf, undefined, make)
    FakeXhr.last.onerror?.()
    await expect(done).rejects.toMatchObject({ status: 0, code: 'network' })
  })
})

describe('review payloads (STORY-009)', () => {
  it('GETs the analysis and the plan', async () => {
    const fetchMock = stubFetch(200, JSON.stringify({ pages: 6 }))
    await expect(getAnalysis('id-1')).resolves.toEqual({ pages: 6 })
    await getPlan('id-1')
    expect(fetchMock.mock.calls.map((c) => c[0])).toEqual(['/api/jobs/id-1/analysis', '/api/jobs/id-1/plan'])
    expect(fetchMock.mock.calls[0]?.[1]?.method).toBeUndefined()
  })

  it('PUTs the plan as JSON and returns the normalized one', async () => {
    const plan: Plan = { source: 'manual', settings: { column_split: 0.5, single_column: true, header_band: 50, footer_band: 32, heading_min_size: 12.5 }, sections: [{ name: ' A ', page: 1, heading: '' }], overrides: {} }
    const fetchMock = stubFetch(200, JSON.stringify({ ...plan, sections: [{ name: 'A', page: 1, heading: '' }] }))
    const saved = await putPlan('id-1', plan)
    expect(saved.sections[0]?.name).toBe('A')
    const [url, init] = fetchMock.mock.calls[0]!
    expect(url).toBe('/api/jobs/id-1/plan')
    expect(init?.method).toBe('PUT')
    expect(init?.headers).toMatchObject({ 'Content-Type': 'application/json', Accept: 'application/json' })
    expect(JSON.parse(init?.body as string)).toEqual(plan)
  })

  it('a 422 keeps the field errors (tests/test_api_e2e.py::test_put_plan_rejects_bad_plans_with_field_errors)', async () => {
    const errors = [{ loc: ['body', 'settings', 'column_split'], msg: 'Input should be ≤ 0.8', type: 'less_than_equal' }]
    stubFetch(422, JSON.stringify({ code: 'invalid', message: 'The request is not valid.', errors }))
    const err = await putPlan('id-1', {} as Plan).catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err).toMatchObject({ status: 422, code: 'invalid', errors })
  })

  it('GETs the manifest rows, and 409 before a cut is not_ready', async () => {
    const rows = [{ index: 0, name: 'A', file: '001 - A.pdf', flags: ['leak'], notes: [], leaks: ['x'], bytes: 10 }]
    stubFetch(200, JSON.stringify(rows))
    await expect(getManifest('id-1')).resolves.toEqual(rows)
    stubFetch(409, JSON.stringify({ code: 'not_ready', message: 'm' }))
    await expect(getManifest('id-1')).rejects.toMatchObject({ status: 409, code: 'not_ready' })
  })
})
