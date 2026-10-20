import test from 'node:test';
import assert from 'node:assert/strict';
import { productTimeline, fittedProductBox, subtitleAtTime } from './productTiming.js';

test('subtitle context uses absolute switch time, exact boundaries and pauses', () => {
  const cues = [
    { start_time: 530, end_time: 532, text: '第一句' },
    { start_time: 532, end_time: 535, text: '第二句' },
    { start_time: 536, end_time: 539, text: '第三句' },
  ];
  const timeline = productTimeline([
    { asset_id: 'image', duration_sec: 2 },
    { asset_id: 'image', duration_sec: 3.5 },
  ], assets, 9);
  assert.equal(timeline.rows[1].start, 2);
  assert.equal(subtitleAtTime(cues, 530 + timeline.rows[0].end).current.text, '第二句');
  const gap = subtitleAtTime(cues, 530 + timeline.rows[1].end);
  assert.equal(gap.current, null);
  assert.equal(gap.previous.text, '第二句');
  assert.equal(gap.next.text, '第三句');
  assert.equal(subtitleAtTime(cues, 539).current, null);
  assert.equal(subtitleAtTime(cues, 529).previous, null);
  assert.deepEqual(subtitleAtTime([], 0), { previous: null, current: null, next: null });
});

const assets = new Map([
  ['image', { media_type: 'image', width: 900, height: 1600 }],
  ['video', { media_type: 'video', duration_sec: 65, width: 1920, height: 1080 }],
]);

test('original video uses full source duration and warns when it overflows', () => {
  const timeline = productTimeline([{ asset_id: 'video', duration_mode: 'original' }], assets, 12);
  assert.equal(timeline.rows[0].wanted, 65);
  assert.equal(timeline.rows[0].effective, 12);
  assert.equal(timeline.truncated, true);
  assert.equal(timeline.remaining, 0);
});

test('fill image covers remaining fraction and skips later assets', () => {
  const timeline = productTimeline([
    { asset_id: 'image', duration_sec: 3 },
    { asset_id: 'image', duration_mode: 'remaining' },
    { asset_id: 'video', duration_mode: 'original' },
  ], assets, 3.2);
  assert.ok(Math.abs(timeline.rows[1].effective - 0.2) < 1e-8);
  assert.equal(timeline.rows[2].skipped, true);
  assert.equal(timeline.truncated, false);
  assert.equal(timeline.used, 3.2);
});

test('without narration fill duration stays unknown', () => {
  const timeline = productTimeline([{ asset_id: 'image', duration_mode: 'remaining' }], assets, null);
  assert.equal(timeline.rows[0].effective, null);
  assert.equal(timeline.remaining, null);
  assert.equal(timeline.truncated, false);
});

test('fitted selection hugs portrait and landscape material edges', () => {
  const box = { x: 0.1, y: 0.2, w: 0.5, h: 0.5 };
  for (const asset of assets.values()) {
    const fit = fittedProductBox(box, asset);
    assert.ok(Math.abs(fit.w * 1080 / (fit.h * 1920) - asset.width / asset.height) < 1e-8);
    assert.ok(fit.x >= box.x && fit.y >= box.y);
    assert.ok(Math.abs(fit.x + fit.w / 2 - (box.x + box.w / 2)) < 1e-8);
  }
});
