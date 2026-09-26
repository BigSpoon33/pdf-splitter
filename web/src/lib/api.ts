/** Typed client for the pdf-splitter API. Every non-2xx answer becomes an `ApiError` carrying the API's `code`. */
import { messageFor } from './errors'
import { KEEPALIVE_MAX_BYTES } from './config'

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

/** The response when it is 2xx; every other outcome is the `ApiError` the caller shows (the API's error body is JSON whatever `accept` says). */
async function fetchOk(url: string, init: RequestInit | undefined, accept: string): Promise<Response> {
  let res: Response
  try {
    res = await fetch(url, { ...init, headers: { Accept: accept, ...init?.headers } })
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') throw err
    throw new ApiError(0, 'network')
  }
  if (!res.ok) throw errorFromBody(res.status, await res.text())
  return res
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetchOk(url, init, 'application/json')
  return JSON.parse(await res.text()) as T
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

// ── The review payloads (STORY-009). Shapes: tests/test_worker.py::test_analyze_outline_book,
// ::test_analyze_headings_book_without_outline, ::test_default_plan_from_outline_and_headings and
// src/pdf_splitter/models.py (the PUT side).

export type Source = 'outline' | 'headings' | 'manual'
export type Col = 'full' | 'left' | 'right'

export interface OutlineItem {
  name: string
  /** 1-based sheet (ADR-003). */
  page: number
  heading: string
  level: number
  y?: number | null
}

export interface HeadingLevel {
  size: number
  count: number
}

export interface HeadingCandidate {
  name: string
  page: number
  heading: string
  size: number
  /** 1-based; level 1 is the biggest type. */
  level: number
  y: number
  col: Col
}

export interface Analysis {
  pages: number
  /** The printed label per sheet, `""` when the PDF has none. */
  pageLabels: string[]
  size: { W: number; H: number }[]
  outline: { levels: number[]; items: OutlineItem[] }
  headings: { body_size: number; levels: HeadingLevel[]; candidates: HeadingCandidate[] }
  suggested: { source: Source; level: number | null }
}

export interface PlanSettings {
  column_split: number
  single_column: boolean
  header_band: number
  footer_band: number
  heading_min_size: number
  /** Optional: absent means the engine's own (16 pt). */
  heading_wrap_gap?: number
}

export interface Section {
  name: string
  page: number
  heading: string
}

/** A cut's y in points, or null for no cut (the section starts at the top / ends at the bottom of its sheet). */
export type Cut = number | null

/** Only the keys sent are applied (`models.py:Override`): `startCut: null` removes a cut, an absent key keeps the engine's. */
export interface Override {
  startCut?: Cut
  startCol?: Col
  endCut?: Cut
  endCol?: Col
}

export interface Plan {
  source: Source
  settings: PlanSettings
  sections: Section[]
  /** Keyed by section INDEX as a decimal string. */
  overrides: Record<string, Override>
}

/** One row of the last cut's manifest (`GET /api/jobs/{id}/manifest`, `tests/test_api_e2e.py::test_manifest_from_a_real_cut_matches_the_zip`). */
export interface ManifestRow {
  index: number
  name: string
  file: string
  flags: string[]
  notes: string[]
  leaks: string[]
  bytes: number
}

export function getAnalysis(id: string, signal?: AbortSignal): Promise<Analysis> {
  return request<Analysis>(jobUrl(id, '/analysis'), { signal })
}

export function getPlan(id: string, signal?: AbortSignal): Promise<Plan> {
  return request<Plan>(jobUrl(id, '/plan'), { signal })
}

export interface SaveOptions {
  signal?: AbortSignal
  /** The page is going away: the browser finishes the request after unload (only for bodies `fitsKeepalive` allows). */
  keepalive?: boolean
}

/** Whether the plan's JSON body is small enough for a keepalive request (`KEEPALIVE_MAX_BYTES`, in UTF-8 bytes). */
export function fitsKeepalive(plan: Plan): boolean {
  return new TextEncoder().encode(JSON.stringify(plan)).byteLength <= KEEPALIVE_MAX_BYTES
}

/** 200 returns the NORMALIZED plan (names trimmed, duplicates suffixed); 422 carries `errors[].loc` per field. */
export function putPlan(id: string, plan: Plan, { signal, keepalive }: SaveOptions = {}): Promise<Plan> {
  return request<Plan>(jobUrl(id, '/plan'), {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(plan),
    signal,
    keepalive,
  })
}

export function getManifest(id: string, signal?: AbortSignal): Promise<ManifestRow[]> {
  return request<ManifestRow[]>(jobUrl(id, '/manifest'), { signal })
}

// ── The preview payloads (STORY-010). Shapes: tests/test_api_e2e.py::test_section_plan_returns_the_engine_view_with_rects
// and ::test_sheet_png_renders_through_the_sandboxed_subprocess_and_caches.

/**
 * The engine's view of one section under given settings and override (`POST /sections/{i}/plan`). `pages` are
 * the first and last sheet (1-based, ADR-003); `rects` are the regions the cut removes, each on the ABSOLUTE
 * sheet it sits on, in page points with the origin top-left (`y0 == endCut` on the end sheet).
 */
export interface SectionPlan {
  pages: [number, number]
  startCut: Cut
  startCol: Col
  endCut: Cut
  endCol: Col
  flags: string[]
  notes: string[]
  rects: [number, [number, number, number, number]][]
}

/**
 * Absent `settings` = the saved plan's; absent `override` = the saved one for that section, an explicit `null`
 * the engine's own plan (`::test_section_plan_uses_the_saved_override_unless_told_otherwise`). Nothing is persisted.
 */
export interface SectionPlanRequest {
  settings?: PlanSettings
  override?: Override | null
}

export function getSectionPlan(id: string, i: number, body: SectionPlanRequest = {}, signal?: AbortSignal): Promise<SectionPlan> {
  return request<SectionPlan>(jobUrl(id, `/sections/${i}/plan`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
}

export type SheetDpi = 48 | 72 | 110

/** `GET /sheets/{n}.png?dpi=`: sheet `n` is 1-based (ADR-003); the PNG is `W × dpi/72` by `H × dpi/72` pixels of `analysis.size[n-1]`. */
export function sheetUrl(id: string, n: number, dpi: SheetDpi): string {
  return jobUrl(id, `/sheets/${n}.png?dpi=${dpi}`)
}

/**
 * The sheet's PNG as a Blob (the caller shows it through an object URL). Fetched rather than set as an `<img src>`
 * so a 410 (the job deleted while rendering) is `isGone`, a 500 `preview_failed` is retryable, and a network drop is
 * `network` — an image element cannot tell them apart.
 */
export async function getSheet(id: string, n: number, dpi: SheetDpi, signal?: AbortSignal): Promise<Blob> {
  const res = await fetchOk(sheetUrl(id, n, dpi), { signal }, 'image/png')
  return res.blob()
}
