import { describe, expect, it } from 'vitest'
import { colAt, colSpan, frameOf, halfPoint, nudgeFor, pixelSize, pointerToPoint, ptToPx, pxToPt, toPixel, toPoint } from './geometry'
import { analysisOf } from './fixtures'

// headed_book's page (tests/fixtures/books.py), as `analysisOf()` carries it.
const SIZE = analysisOf().size[0]!

describe('points ↔ pixels (AC-6)', () => {
  it.each([
    [72, 522.7, 522.7],
    [110, 522.7, 798.5694444444445],
    [48, 789.6, 526.4],
  ])('at %i dpi, %f pt is %f px and back', (dpi, pt, px) => {
    expect(ptToPx(pt, dpi)).toBeCloseTo(px, 6)
    expect(pxToPt(px, dpi)).toBeCloseTo(pt, 6)
    expect(pxToPt(ptToPx(pt, dpi), dpi)).toBeCloseTo(pt, 9)
  })

  it('sizes the PNG like PyMuPDF (nearest pixel) at 72 and 110 dpi', () => {
    expect(pixelSize(frameOf(SIZE, 72))).toEqual({ width: 523, height: 790 })
    expect(pixelSize(frameOf(SIZE, 110))).toEqual({ width: 799, height: 1206 })
  })

  it('maps a page point to its pixel and back with the origin at 0,0', () => {
    const frame = frameOf(SIZE, 110)
    const px = toPixel(frame, { x: 254.5, y: 400 })
    expect(px.x).toBeCloseTo(388.82, 2)
    expect(px.y).toBeCloseTo(611.11, 2)
    const back = toPoint(frame, px)
    expect(back.x).toBeCloseTo(254.5, 9)
    expect(back.y).toBeCloseTo(400, 9)
  })

  it('honours a non-zero CropBox origin: the origin lands on pixel 0,0 and the far corner on the PNG size', () => {
    const frame = frameOf(SIZE, 72, { x: 20, y: 30 })
    expect(toPixel(frame, { x: 20, y: 30 })).toEqual({ x: 0, y: 0 })
    const corner = toPixel(frame, { x: 20 + SIZE.W, y: 30 + SIZE.H })
    expect(corner.x).toBeCloseTo(SIZE.W, 9)
    expect(corner.y).toBeCloseTo(SIZE.H, 9)
    // At 110 dpi a cut 100 pt below the page's top is 100 × 110/72 px down the image, not (100 + 30) × 110/72.
    const hi = frameOf(SIZE, 110, { x: 20, y: 30 })
    expect(toPixel(hi, { x: 20, y: 130 }).y).toBeCloseTo(152.78, 2)
    expect(toPoint(hi, { x: 0, y: 152.7777 }).y).toBeCloseTo(130, 3)
  })

  it('turns a pointer over the scaled image into page points, clamped to the page', () => {
    const frame = frameOf(SIZE, 110)
    const box = { left: 100, top: 50, width: 261.35, height: 394.8 } // the image shown at half size
    expect(pointerToPoint(frame, { x: 100, y: 50 }, box)).toEqual({ x: 0, y: 0 })
    const mid = pointerToPoint(frame, { x: 100 + 261.35 / 2, y: 50 + 394.8 / 2 }, box)
    expect(mid.x).toBeCloseTo(SIZE.W / 2, 9)
    expect(mid.y).toBeCloseTo(SIZE.H / 2, 9)
    expect(pointerToPoint(frame, { x: 0, y: 9999 }, box)).toEqual({ x: 0, y: SIZE.H })
    // With an origin, the same pointer names the same place on the page, shifted by that origin.
    const shifted = pointerToPoint(frameOf(SIZE, 110, { x: 20, y: 30 }), { x: 100, y: 50 }, box)
    expect(shifted).toEqual({ x: 20, y: 30 })
    expect(pointerToPoint(frame, { x: 150, y: 80 }, { ...box, width: 0, height: 0 })).toEqual({ x: 0, y: 0 })
  })
})

describe('columns and nudges', () => {
  const settings = { column_split: 0.487, single_column: false }

  it('names the column a point sits in, full for a one-column book', () => {
    expect(colAt(100, SIZE.W, settings)).toBe('left')
    expect(colAt(300, SIZE.W, settings)).toBe('right')
    expect(colAt(300, SIZE.W, { ...settings, single_column: true })).toBe('full')
  })

  it("spans a cut like the engine's cut_rects", () => {
    expect(colSpan('left', 522.7, 254.5)).toEqual([0, 254.5])
    expect(colSpan('right', 522.7, 254.5)).toEqual([254.5, 522.7])
    expect(colSpan('full', 522.7, 254.5)).toEqual([0, 522.7])
  })

  it('rounds to half points', () => {
    expect(halfPoint(400.26)).toBe(400.5)
    expect(halfPoint(400.24)).toBe(400)
  })

  it('nudges 1 pt per arrow, 10 with Shift, along the line’s own axis only (AC-5)', () => {
    expect(nudgeFor('ArrowUp', false, 'vertical')).toBe(-1)
    expect(nudgeFor('ArrowDown', true, 'vertical')).toBe(10)
    expect(nudgeFor('ArrowLeft', false, 'vertical')).toBe(0)
    expect(nudgeFor('ArrowLeft', true, 'horizontal')).toBe(-10)
    expect(nudgeFor('ArrowRight', false, 'horizontal')).toBe(1)
    expect(nudgeFor('Enter', false, 'horizontal')).toBe(0)
  })
})
