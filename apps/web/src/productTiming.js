export function productTimeline(items, assetMap, available) {
  let cursor = 0;
  let filled = false;
  const rows = items.map(item => {
    const asset = assetMap.get(item.asset_id);
    const video = asset?.media_type === 'video';
    const source = Number(asset?.duration_sec) || 0;
    const mode = item.duration_mode || 'custom';
    const remaining = available == null ? null : Math.max(0, available - cursor);
    let wanted = Math.max(0.5, Math.min(30, Number(item.duration_sec) || 0.5));
    if (video && source > 0) wanted = mode === 'original' ? source : Math.min(wanted, source);
    if (!video && mode === 'remaining') wanted = remaining;
    const skipped = filled;
    const effective = skipped ? 0 : wanted == null ? null : remaining == null ? wanted : Math.min(wanted, remaining);
    const row = { start: cursor, end: effective == null ? null : cursor + effective,
      wanted, effective, remaining, skipped, truncated: !skipped && remaining != null && wanted > remaining + 0.000001 };
    if (!skipped) cursor += effective || 0;
    if (!video && mode === 'remaining') filled = true;
    return row;
  });
  return { rows, used: cursor, remaining: available == null ? null : Math.max(0, available - cursor),
    truncated: rows.some(row => row.truncated), skipped: rows.filter(row => row.skipped).length };
}

export function subtitleAtTime(timings, time) {
  const currentIndex = timings.findIndex(cue => cue.start_time <= time && time < cue.end_time);
  if (currentIndex >= 0) {
    return { previous: timings[currentIndex - 1] || null, current: timings[currentIndex],
      next: timings[currentIndex + 1] || null };
  }
  const nextIndex = timings.findIndex(cue => cue.start_time > time);
  return { previous: (nextIndex < 0 ? timings.at(-1) : timings[nextIndex - 1]) || null,
    current: null, next: nextIndex < 0 ? null : timings[nextIndex] };
}

// Match the contain-fit geometry used by the exported video.
export function fittedProductBox(box, asset) {
  if (!(asset?.width > 0 && asset?.height > 0)) return box;
  const scale = Math.min(box.w * 1080 / asset.width, box.h * 1920 / asset.height);
  const w = asset.width * scale / 1080;
  const h = asset.height * scale / 1920;
  return { x: box.x + (box.w - w) / 2, y: box.y + (box.h - h) / 2, w, h };
}
