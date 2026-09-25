/** Typed client for the pdf-splitter API. Every non-2xx answer becomes an `ApiError` carrying the API's `code`. */
import { messageFor } from './errors'

export type JobState = 'queued' | 'running' | 'review' | 'done' | 'failed'
export type JobKind = 'analyze' | 'cut'
/** The worker's failure class; a different namespace from `ApiError.code`. */
export type JobErrorCode = 'timeout' | 'resources' | 'internal'

/** `GET /api/jobs/{id}` (`routes/plan.py:status_of`). */
export interface JobStatus {
  id: string
  state: JobState
  kind: JobKind
  /** Pages indexed while analyzing, sections written while cutting. */
  progress: number
  total: number
  /** null until the API reports it (STORY-012). */
  queue_position: number | null
  /** The phase while running, the user-facing reason once failed. */
  message: string | null
  /** Non-null only when `state === 'failed'`. */
  error_code: JobErrorCode | null
  expires_at: string
  filename: string
  pages: number
}

export interface CreatedJob {
  id: string
  state: 'queued'
}

export interface FieldError {
  loc: (string | number)[]
  msg: string
  type: string
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly errors?: FieldError[]
  readonly requestId?: string

  constructor(status: number, code: string, message?: string, errors?: FieldError[], requestId?: string) {
    super(message ?? messageFor(code))
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.errors = errors
    this.requestId = requestId
  }

  /** What to show the visitor: the table's message for the code, never the raw server text. */
  get userMessage(): string {
    return messageFor(this.code)
  }
}

export const TERMINAL_STATES: ReadonlySet<JobState> = new Set(['review', 'done', 'failed'])

/** 404 and 410 mean the id will never answer again, so polling and retries must stop. */
export function isGone(err: unknown): boolean {
  return err instanceof ApiError && (err.code === 'expired' || err.code === 'not_found')
}

function jobUrl(id: string, suffix = ''): string {
  return `/api/jobs/${encodeURIComponent(id)}${suffix}`
}

/** An error body is `{code, message, errors?, request_id?}`; anything else (a proxy's HTML page) gets a code from the status. */
export function errorFromBody(status: number, text: string): ApiError {
  let body: unknown
  try {
    body = JSON.parse(text)
  } catch {
    body = null
  }
  if (body && typeof body === 'object' && typeof (body as { code?: unknown }).code === 'string') {
    const b = body as { code: string; message?: unknown; errors?: FieldError[]; request_id?: unknown }
    return new ApiError(
      status,
      b.code,
      typeof b.message === 'string' ? b.message : undefined,
      Array.isArray(b.errors) ? b.errors : undefined,
      typeof b.request_id === 'string' ? b.request_id : undefined,
    )
  }
  // A body-size cap in front of the API (Caddy) answers 413 without our JSON.
  return new ApiError(status, status === 413 ? 'too_large' : 'internal')
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(url, { ...init, headers: { Accept: 'application/json', ...init?.headers } })
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') throw err
    throw new ApiError(0, 'network')
  }
  const text = await res.text()
  if (!res.ok) throw errorFromBody(res.status, text)
  return JSON.parse(text) as T
}

export function getJob(id: string, signal?: AbortSignal): Promise<JobStatus> {
  return request<JobStatus>(jobUrl(id), { signal })
}

/** Uploads with XHR because `fetch` reports no upload progress. `onProgress` gets 0..1. */
export function createJob(
  file: File,
  onProgress?: (fraction: number) => void,
  makeXhr: () => XMLHttpRequest = () => new XMLHttpRequest(),
): Promise<CreatedJob> {
  return new Promise((resolve, reject) => {
    const xhr = makeXhr()
    xhr.open('POST', '/api/jobs')
    xhr.setRequestHeader('Accept', 'application/json')
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && e.total > 0) onProgress?.(e.loaded / e.total)
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText) as CreatedJob)
        } catch {
          reject(new ApiError(xhr.status, 'internal'))
        }
      } else {
        reject(errorFromBody(xhr.status, xhr.responseText))
      }
    }
    xhr.onerror = () => reject(new ApiError(0, 'network'))
    xhr.onabort = () => reject(new ApiError(0, 'network'))
    const form = new FormData()
    form.append('file', file, file.name)
    xhr.send(form)
  })
}
