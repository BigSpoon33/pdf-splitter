/**
 * Page points ↔ preview pixels (AC-6). The engine's cuts and `rects` are PDF points with the origin at the
 * top-left of the page as PyMuPDF sees it (`page.rect`: the CropBox already translated to 0,0 and rotated), and
 * `/sheets/{n}.png` renders that same rectangle at `dpi`, so one pixel is `dpi / 72` points. A `SheetFrame`
 * carries an origin anyway: a page whose points do not start at 0,0 (a CropBox left as-is) maps through the same
 * two functions, and the overlay never has to know which case it is in.
 */
import type { Col, PlanSettings } from './api'

export type Dpi = 48 | 72 | 110

export interface Point {
  x: number
  y: number
}

/** A sheet as the preview shows it: its page size in points, the PNG's dpi, and where the page's points start. */
export interface SheetFrame {
  W: number
  H: number
  dpi: Dpi
  originX: number
  originY: number
}

export function ptToPx(pt: number, dpi: number): number {
  return (pt * dpi) / 72
}

export function pxToPt(px: number, dpi: number): number {
  return (px * 72) / dpi
}

export function frameOf(size: { W: number; H: number }, dpi: Dpi, origin: Point = { x: 0, y: 0 }): SheetFrame {
  return { W: size.W, H: size.H, dpi, originX: origin.x, originY: origin.y }
}

/** The PNG's pixel size, rounded the way PyMuPDF sizes a pixmap (to the nearest pixel). */
export function pixelSize(frame: SheetFrame): { width: number; height: number } {
  return { width: Math.round(ptToPx(frame.W, frame.dpi)), height: Math.round(ptToPx(frame.H, frame.dpi)) }
}

/** A page point → the pixel it lands on in the sheet's PNG. */
export function toPixel(frame: SheetFrame, pt: Point): Point {
  return { x: ptToPx(pt.x - frame.originX, frame.dpi), y: ptToPx(pt.y - frame.originY, frame.dpi) }
}

/** A pixel of the sheet's PNG → the page point under it. */
export function toPoint(frame: SheetFrame, px: Point): Point {
  return { x: pxToPt(px.x, frame.dpi) + frame.originX, y: pxToPt(px.y, frame.dpi) + frame.originY }
}

/**
 * A pointer position over the sheet's image, which the browser scales to `box`, → the page point under it,
 * clamped to the page. Independent of the dpi: the image is `W × H` points however many pixels it has.
 */
export function pointerToPoint(
  frame: SheetFrame,
  client: Point,
  box: { left: number; top: number; width: number; height: number },
): Point {
  const fx = box.width > 0 ? (client.x - box.left) / box.width : 0
  const fy = box.height > 0 ? (client.y - box.top) / box.height : 0
  return { x: clamp(fx * frame.W, 0, frame.W) + frame.originX, y: clamp(fy * frame.H, 0, frame.H) + frame.originY }
}

export function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v))
}

/** The gutter's x in points: `column_split` is a fraction of the page width. */
export function gutterX(settings: Pick<PlanSettings, 'column_split'>, W: number): number {
  return settings.column_split * W
}

/** The column a page point sits in — `full` for a one-column book, where every cut spans the page. */
export function colAt(x: number, W: number, settings: Pick<PlanSettings, 'column_split' | 'single_column'>): Col {
  if (settings.single_column) return 'full'
  return x < gutterX(settings, W) ? 'left' : 'right'
}

/** The horizontal extent of a cut in `col`, as the engine's `cut_rects` draws it. */
export function colSpan(col: Col, W: number, split: number): [number, number] {
  return col === 'left' ? [0, split] : col === 'right' ? [split, W] : [0, W]
}

/** Cuts are kept to half points: finer than the engine needs, coarse enough to read as a number. */
export function halfPoint(v: number): number {
  return Math.round(v * 2) / 2
}

/** AC-5: an arrow key moves a line 1 pt, 10 pt with Shift; other keys move nothing. */
export function nudgeFor(key: string, shift: boolean, orientation: 'vertical' | 'horizontal'): number {
  const step = shift ? 10 : 1
  const [less, more] = orientation === 'vertical' ? ['ArrowUp', 'ArrowDown'] : ['ArrowLeft', 'ArrowRight']
  return key === less ? -step : key === more ? step : 0
}
