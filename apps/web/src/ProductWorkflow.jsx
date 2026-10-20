import { productTimeline, fittedProductBox, subtitleAtTime } from './productTiming.js';
import React, { useEffect, useImperativeHandle, useMemo, useRef, useState } from 'react';

const API = import.meta.env.VITE_API_URL ?? (import.meta.env.DEV ? 'http://127.0.0.1:8000' : '');
const PRODUCT_MARKER = '【带货开始】';
const MIN_PIP = 0.12;
const DEFAULT_PIP_BOX = { x: 0.62, y: 0.31, w: 0.34, h: 0.22 };

async function api(path, options = {}) {
  const response = await fetch(`${API}${path}`, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || '请求失败');
  return data;
}

const clamp = (value, min, max) => Math.max(min, Math.min(max, Number(value) || 0));
const seconds = value => `${Number(value || 0).toFixed(1)} 秒`;

function normalizePipBox(value) {
  const raw = value && typeof value === 'object' ? value : {};
  const x = clamp(raw.x ?? DEFAULT_PIP_BOX.x, 0, 1 - MIN_PIP);
  const y = clamp(raw.y ?? DEFAULT_PIP_BOX.y, 0, 1 - MIN_PIP);
  const w = clamp(raw.w ?? DEFAULT_PIP_BOX.w, MIN_PIP, 1 - x);
  const h = clamp(raw.h ?? DEFAULT_PIP_BOX.h, MIN_PIP, 1 - y);
  return { x, y, w, h };
}

export function ProductLibrary({ media, reload, onError, project, projectId, busy, onProjectRefresh }) {
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState('');
  const [selectedIds, setSelectedIds] = useState(() => new Set());
  const [bulkDeleting, setBulkDeleting] = useState(false);
  const selectAllRef = useRef(null);
  const products = media.products || [];
  const allSelected = products.length > 0 && selectedIds.size === products.length;
  const partiallySelected = selectedIds.size > 0 && !allSelected;

  useEffect(() => {
    if (!media.products) return;
    setSelectedIds(current => new Set(
      [...current].filter(id => media.products.some(item => item.id === id)),
    ));
  }, [media.products]);

  useEffect(() => {
    if (selectAllRef.current) selectAllRef.current.indeterminate = partiallySelected;
  }, [partiallySelected]);

  async function upload(files) {
    setUploading(true);
    const failures = [];
    for (const [index, file] of [...files].entries()) {
      setProgress(`正在上传 ${index + 1}/${files.length}：${file.name}`);
      const body = new FormData();
      body.append('file', file);
      try { await api('/api/media/products', { method: 'POST', body }); }
      catch (error) { failures.push(`${file.name}：${error.message}`); }
    }
    try { await reload(); } catch (error) { failures.push(error.message); }
    setUploading(false);
    setProgress('');
    if (failures.length) onError(failures.join('；'));
  }

  function toggleSelected(id) {
    setSelectedIds(current => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  function toggleAll() {
    setSelectedIds(allSelected ? new Set() : new Set(products.map(item => item.id)));
  }

  async function remove(asset) {
    if (!window.confirm(`删除带货素材「${asset.file_name}」？`)) return;
    try {
      await api(`/api/media/products/${asset.id}`, { method: 'DELETE' });
      setSelectedIds(current => {
        const next = new Set(current);
        next.delete(asset.id);
        return next;
      });
      await reload();
    } catch (error) {
      onError(error.message);
    }
  }

  async function bulkDelete() {
    const ids = [...selectedIds];
    if (!ids.length || bulkDeleting) return;
    if (!window.confirm(`删除选中的 ${ids.length} 个带货素材？已被项目或预设使用的素材会跳过。`)) return;
    setBulkDeleting(true);
    try {
      const result = await api('/api/media/products/bulk-delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ asset_ids: ids }),
      });
      setSelectedIds(new Set());
      await reload();
      if (result.skipped?.length) {
        const detail = result.skipped
          .map(item => `${products.find(asset => asset.id === item.asset_id)?.file_name || item.asset_id}：${item.reason}`)
          .join('；');
        onError(`已删除 ${result.deleted_ids?.length || 0} 个素材；${result.skipped.length} 个未删除（${detail}）`);
      }
    } catch (error) {
      onError(error.message);
    } finally {
      setBulkDeleting(false);
    }
  }

  return <>
    <div className="media-heading">
      <div>
        <h2>带货素材</h2>
        <p>单独上传短视频或图片，用于从文案标记位置开始的画中画展示。</p>
      </div>
      <label className="media-upload">{uploading ? '上传中…' : '上传带货素材'}
        <input
          aria-label="上传带货素材"
          type="file"
          multiple
          accept="video/*,.mp4,.mov,.mkv,.webm,.avi,.m4v,image/png,image/jpeg,image/webp"
          disabled={uploading}
          onChange={event => { upload(event.target.files); event.target.value = ''; }}
        />
      </label>
    </div>
    <p>{products.length} 个带货素材 · 支持视频和图片 · 视频最大 1 GB，图片最大 20 MB</p>
    {progress && <p role="status">{progress}</p>}
    {!!products.length && <div className="media-batch-bar">
      <label>
        <input
          ref={selectAllRef}
          type="checkbox"
          checked={allSelected}
          disabled={uploading || bulkDeleting}
          onChange={toggleAll}
        />
        全选
      </label>
      <span>已选 {selectedIds.size} / {products.length}</span>
      <button
        type="button"
        className="danger"
        disabled={!selectedIds.size || uploading || bulkDeleting}
        onClick={bulkDelete}
      >
        {bulkDeleting ? '删除中…' : `删除选中（${selectedIds.size}）`}
      </button>
    </div>}
    {!products.length && (
      <div className="media-empty">还没有带货素材。上传短视频或图片后，在下方配置画中画。</div>
    )}
    <div className="media-grid product-grid">
      {products.map(item => <article className={selectedIds.has(item.id) ? 'media-card product-card selected' : 'media-card product-card'} key={item.id}>
        <label className="media-select">
          <input
            type="checkbox"
            checked={selectedIds.has(item.id)}
            disabled={uploading || bulkDeleting}
            onChange={() => toggleSelected(item.id)}
            aria-label={`选择带货素材 ${item.file_name}`}
          />
          选择
        </label>
        {item.media_type === 'image'
          ? <img src={`${API}${item.file_url}`} alt={item.file_name} />
          : <video controls preload="metadata" muted src={`${API}${item.file_url}`} />}
        <strong title={item.file_name}>{item.file_name}</strong>
        <p>{item.media_type === 'image'
          ? `图片 · ${item.width} × ${item.height}`
          : `${seconds(item.duration_sec)} · ${item.width} × ${item.height}`}</p>
        <button type="button" disabled={uploading || bulkDeleting} onClick={() => remove(item)}>删除素材</button>
      </article>)}
    </div>
    {project?.workflow_type === 'video_library' ? (
      <ProductPlacementPanel
        key={project.id}
        project={project}
        projectId={projectId}
        media={media}
        busy={busy}
        onProjectRefresh={onProjectRefresh}
        onError={onError}
      />
    ) : (
      <div className="product-placement-hint">
        选择一个素材视频项目后，可以在这里配置带货开始点、展示时长、画中画位置和转场。
      </div>
    )}
  </>;
}

export function ProductPlacementPanel({ project, projectId, media, busy, onProjectRefresh, onError, saveRef }) {
  const placement = project.product_placement || {};
  const placementKey = JSON.stringify(placement);
  const [items, setItems] = useState(() => (placement.items || []).map(item => ({
    ...item,
    duration_mode: item.duration_mode || 'custom',
    pip_box: normalizePipBox(item.pip_box || placement.pip_box),
  })));
  const [transitionSec, setTransitionSec] = useState(0.5);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [fade, setFade] = useState(1);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState('');
  const [placementPresets, setPlacementPresets] = useState([]);
  const [selectedPresetId, setSelectedPresetId] = useState('');
  const [presetName, setPresetName] = useState('');
  const [presetSaving, setPresetSaving] = useState(false);
  const [previewError, setPreviewError] = useState('');
  const stageRef = useRef(null);
  const previewAudioRef = useRef(null);

  useEffect(() => {
    const current = project.product_placement || {};
    const fallback = normalizePipBox(current.pip_box);
    setItems((current.items || []).map(item => ({
      ...item,
      duration_mode: item.duration_mode || 'custom',
      pip_box: normalizePipBox(item.pip_box || fallback),
    })));
    setTransitionSec(Number(current.transition_sec ?? 0.5));
    setSelectedIndex(0);
    setPlaying(false);
    setPreviewError('');
    setNotice('');
    // The serialized placement key intentionally resets local edits after saves/project changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project.id, placementKey]);

  useEffect(() => {
    api('/api/video-library/placement-presets')
      .then(data => setPlacementPresets(data.presets || []))
      .catch(error => onError?.(error.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const assetMap = useMemo(
    () => new Map((media.products || []).map(item => [item.id, item])),
    [media.products],
  );
  const fallbackPipBox = useMemo(
    () => normalizePipBox(project.product_placement?.pip_box),
    [project.product_placement?.pip_box],
  );
  const selectedItem = items[selectedIndex];
  const selectedAsset = selectedItem ? assetMap.get(selectedItem.asset_id) : null;
  const selectedPipBox = selectedItem?.pip_box
    ? normalizePipBox(selectedItem.pip_box)
    : fallbackPipBox;
  const visiblePipBox = fittedProductBox(selectedPipBox, selectedAsset);
  const template = (media.templates || []).find(item => item.id === project.background_template_id);
  const markerPresent = String(project.raw_script || '').includes(PRODUCT_MARKER);
  const hasPreviewAudio = Boolean(project.audio_url);
  const previewStartSec = Number(placement.start_sec ?? 0) || 0;
  const timingKnown = markerPresent && placement.start_sec != null && Number(project.audio_duration_sec) > 0 && !placement.start_sec_stale;
  const available = timingKnown ? Math.max(0, Number(project.audio_duration_sec) - previewStartSec) : null;
  const timeline = productTimeline(items, assetMap, available);
  const subtitleTimings = useMemo(() => (project.narration_subtitle_timings || [])
    .filter(cue => cue.text?.trim() && Number.isFinite(Number(cue.start_time))
      && Number(cue.end_time) > Number(cue.start_time))
    .map(cue => ({ ...cue, start_time: Number(cue.start_time), end_time: Number(cue.end_time) }))
    .sort((a, b) => a.start_time - b.start_time), [project.narration_subtitle_timings]);
  const playbackItems = items.map((item, index) => ({ ...item, index, duration_sec: timeline.rows[index].effective }))
    .filter(item => item.duration_sec > 0);


  useEffect(() => {
    if (!playing) {
      previewAudioRef.current?.pause();
      setFade(1);
      return undefined;
    }
    if (hasPreviewAudio) return undefined;
    const started = performance.now();
    const interval = window.setInterval(() => updatePreviewElapsed((performance.now() - started) / 1000), 50);
    return () => window.clearInterval(interval);
  }, [playing, hasPreviewAudio]);

  useEffect(() => {
    setPlaying(false);
    previewAudioRef.current?.pause();
  }, [items, project.audio_duration_sec, placement.start_sec]);

  let statusText = '';
  let statusWarning = false;
  if (!markerPresent) {
    statusWarning = true;
    statusText = `文案中没有${PRODUCT_MARKER}标记。请在创建项目时于文案中插入标记；已有项目需要重新创建后才能启用带货画中画。`;
  } else if (placement.start_sec === null || placement.start_sec === undefined) {
    statusText = '已插入标记；生成配音后会自动换算带货开始时间。';
  } else if (placement.start_sec_stale) {
    statusWarning = true;
    statusText = `开始时间约为 ${seconds(placement.start_sec)}，但文案已修改，请重新生成配音后再导出。`;
  } else {
    statusText = `带货开始：${seconds(placement.start_sec)}（标记前约 ${Number(placement.marker_offset || 0)} 个朗读字符）`;
  }

  function addItem(asset) {
    const sourceDuration = asset.media_type === 'video' ? Number(asset.duration_sec || 0) : 3;
    const duration = Math.max(0.5, Math.min(20, sourceDuration > 0 ? Math.min(3, sourceDuration) : 3));
    const nextIndex = items.length;
    const box = selectedItem?.pip_box ? { ...selectedItem.pip_box } : { ...fallbackPipBox };
    setItems(current => [
      ...current,
      { asset_id: asset.id, duration_sec: duration, duration_mode: 'custom', pip_box: box },
    ]);
    setSelectedIndex(nextIndex);
  }

  function updateDuration(index, value) {
    setItems(current => current.map((item, itemIndex) => {
      if (itemIndex !== index) return item;
      const asset = assetMap.get(item.asset_id);
      const maxDuration = asset?.media_type === 'video'
        ? Math.min(30, Number(asset.duration_sec) || 30)
        : 30;
      return { ...item, duration_sec: clamp(value, 0.5, maxDuration) };
    }));
  }

  function toggleDurationMode(index, mode) {
    setItems(current => current.map((item, itemIndex) => itemIndex === index
      ? { ...item, duration_mode: item.duration_mode === mode ? 'custom' : mode } : item));
  }

  function moveItem(index, offset) {
    const target = index + offset;
    if (target < 0 || target >= items.length) return;
    setItems(current => {
      const next = [...current];
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
    setSelectedIndex(target);
  }

  function removeItem(index) {
    setItems(current => current.filter((_, itemIndex) => itemIndex !== index));
    setSelectedIndex(current => Math.max(0, Math.min(current, items.length - 2)));
  }

  function beginPipInteraction(event, mode) {
    event.preventDefault();
    event.stopPropagation();
    const rect = stageRef.current?.getBoundingClientRect();
    if (!rect) return;
    const start = { ...selectedPipBox };
    const startX = event.clientX;
    const startY = event.clientY;
    const updateBox = (nextBox) => {
      setItems(current => current.map((item, index) => (
        index === selectedIndex ? { ...item, pip_box: nextBox } : item
      )));
    };
    const move = (moveEvent) => {
      const dx = (moveEvent.clientX - startX) / rect.width;
      const dy = (moveEvent.clientY - startY) / rect.height;
      if (mode === 'move') {
        updateBox({
          ...start,
          x: clamp(start.x + dx, 0, 1 - start.w),
          y: clamp(start.y + dy, 0, 1 - start.h),
        });
      } else {
        const fitted = fittedProductBox(start, selectedAsset);
        const desired = 1 + (Math.abs(dx / fitted.w) > Math.abs(dy / fitted.h) ? dx / fitted.w : dy / fitted.h);
        const minimum = Math.max(MIN_PIP / start.w, MIN_PIP / start.h);
        const offsetX = fitted.x - start.x;
        const offsetY = fitted.y - start.y;
        const maximum = Math.min(
          (1 - fitted.x) / (start.w - offsetX),
          (1 - fitted.y) / (start.h - offsetY),
          offsetX > 0 ? fitted.x / offsetX : Infinity,
          offsetY > 0 ? fitted.y / offsetY : Infinity,
        );
        const scale = clamp(desired, minimum, maximum);
        updateBox({
          x: fitted.x - offsetX * scale, y: fitted.y - offsetY * scale,
          w: start.w * scale, h: start.h * scale,
        });
      }
    };
    const end = (upEvent) => {
      if (upEvent.type === 'pointerup') move(upEvent);
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', end);
      window.removeEventListener('pointercancel', end);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', end);
    window.addEventListener('pointercancel', end);
  }

  function togglePreview() {
    if (playing) {
      previewAudioRef.current?.pause();
      setPlaying(false);
      setFade(1);
      return;
    }
    if (!playbackItems.length) return;
    setSelectedIndex(playbackItems[0].index);
    setFade(1);
    setPreviewError('');
    setPlaying(true);
    const audio = previewAudioRef.current;
    if (hasPreviewAudio && audio) {
      try {
        audio.currentTime = Math.max(0, previewStartSec);
        audio.volume = Math.min(1, Number(project.voice_volume ?? 1));
        const promise = audio.play();
        if (promise?.catch) {
          promise.catch(() => {
            setPlaying(false);
            setPreviewError('浏览器阻止了播放，请再点一次“播放预览”');
          });
        }
      } catch {
        setPlaying(false);
        setPreviewError('无法播放当前项目配音');
      }
    }
  }

  function handlePreviewTimeUpdate(event) {
    updatePreviewElapsed(event.currentTarget.currentTime - previewStartSec);
  }

  function updatePreviewElapsed(elapsed) {
    const total = timeline.used;
    if (elapsed < 0) {
      setFade(0.3);
      return;
    }
    if (elapsed >= total) {
      previewAudioRef.current?.pause();
      setPlaying(false);
      setFade(1);
      return;
    }
    let cursor = 0;
    let active = 0;
    for (let index = 0; index < playbackItems.length; index += 1) {
      const duration = Number(playbackItems[index].duration_sec || 0);
      if (elapsed < cursor + duration) {
        active = index;
        break;
      }
      cursor += duration;
    }
    const activeDuration = Number(playbackItems[active]?.duration_sec || 0);
    const fadeWindow = Math.min(0.3, activeDuration / 2);
    const within = elapsed - cursor;
    setSelectedIndex(playbackItems[active]?.index ?? 0);
    setFade(
      within < fadeWindow || elapsed > cursor + activeDuration - fadeWindow ? 0.3 : 1,
    );
  }

  function applyPlacementPreset(preset) {
    const fallback = normalizePipBox(preset.pip_box);
    const nextItems = (preset.items || [])
      .filter(item => assetMap.has(item.asset_id))
      .map(item => ({
        asset_id: item.asset_id,
        duration_sec: Number(item.duration_sec || 3),
        duration_mode: item.duration_mode || 'custom',
        pip_box: normalizePipBox(item.pip_box || fallback),
      }));
    setItems(nextItems);
    setTransitionSec(Number(preset.transition_sec ?? 0.5));
    setSelectedIndex(0);
    setPlaying(false);
    setPreviewError('');
    setSelectedPresetId(preset.id);
    setPresetName(preset.name || '');
    const skipped = (preset.items || []).length - nextItems.length;
    setNotice(skipped
      ? `已应用带货预设，${skipped} 个素材已不存在并跳过`
      : '已应用带货预设，点击“保存带货设置”后写入项目');
  }

  async function savePlacementPreset(overwrite) {
    const name = presetName.trim();
    if (!name) {
      setNotice('请填写带货预设名称');
      return;
    }
    setPresetSaving(true);
    setNotice('');
    try {
      const payload = {
        name,
        items: items.map(item => ({
          asset_id: item.asset_id,
          duration_sec: Number(item.duration_sec),
          duration_mode: item.duration_mode || 'custom',
          pip_box: normalizePipBox(item.pip_box || selectedPipBox),
        })),
        transition_sec: Number(transitionSec),
        pip_box: selectedPipBox,
      };
      if (overwrite && selectedPresetId) payload.preset_id = selectedPresetId;
      const result = await api('/api/video-library/placement-presets', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const saved = result.preset;
      setPlacementPresets(current => [
        saved,
        ...current.filter(item => item.id !== saved.id),
      ]);
      setSelectedPresetId(saved.id);
      setPresetName(saved.name);
      setNotice('带货预设已保存');
    } catch (error) {
      onError?.(error.message);
    } finally {
      setPresetSaving(false);
    }
  }

  async function deletePlacementPreset() {
    if (!selectedPresetId) return;
    if (!window.confirm(`删除带货预设「${presetName || ''}」？`)) return;
    try {
      await api(`/api/video-library/placement-presets/${selectedPresetId}`, { method: 'DELETE' });
      setPlacementPresets(current => current.filter(item => item.id !== selectedPresetId));
      setSelectedPresetId('');
      setPresetName('');
      setNotice('带货预设已删除');
    } catch (error) {
      onError?.(error.message);
    }
  }

  useImperativeHandle(saveRef, () => ({ projectId, save }));

  async function save() {
    if (!projectId) return false;
    setSaving(true);
    setNotice('');
    try {
      const result = await api(`/api/projects/${projectId}/product-placement`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          items: items.map(item => ({
            asset_id: item.asset_id,
            duration_sec: Number(item.duration_sec),
            duration_mode: item.duration_mode || 'custom',
            pip_box: normalizePipBox(item.pip_box || selectedPipBox),
          })),
          transition_sec: Number(transitionSec),
          pip_box: selectedPipBox,
        }),
      });
      const saved = result.product_placement || {};
      const savedFallback = normalizePipBox(saved.pip_box);
      setItems((saved.items || []).map(item => ({
        ...item,
        pip_box: normalizePipBox(item.pip_box || savedFallback),
      })));
      setTransitionSec(Number(saved.transition_sec ?? 0.5));
      setNotice('带货设置已保存');
      await onProjectRefresh?.();
      return true;
    } catch (error) {
      onError?.(error.message);
      return false;
    } finally {
      setSaving(false);
    }
  }

  return <section className="product-placement-panel">
    <div className="media-heading">
      <div>
        <h3>项目带货设置 · {project.name}</h3>
        <p>从文案标记位置开始，按顺序展示带货素材；素材之间渐隐渐显。</p>
      </div>
      <button type="button" className="primary" disabled={busy || saving} onClick={save}>
        {saving ? '保存中…' : '保存带货设置'}
      </button>
    </div>
    <div className="placement-preset-bar">
      <label>
        带货预设
        <select
          value={selectedPresetId}
          onChange={(event) => {
            const preset = placementPresets.find(item => item.id === event.target.value);
            if (preset) applyPlacementPreset(preset);
            else {
              setSelectedPresetId('');
              setPresetName('');
            }
          }}
        >
          <option value="">不使用预设</option>
          {placementPresets.map(preset => (
            <option key={preset.id} value={preset.id}>{preset.name}</option>
          ))}
        </select>
      </label>
      <input
        value={presetName}
        onChange={event => setPresetName(event.target.value)}
        placeholder="带货预设名称"
        maxLength={60}
      />
      <button type="button" onClick={() => savePlacementPreset(false)} disabled={presetSaving || !presetName.trim()}>保存为新预设</button>
      <button type="button" onClick={() => savePlacementPreset(true)} disabled={presetSaving || !selectedPresetId}>覆盖保存</button>
      <button type="button" className="danger" onClick={deletePlacementPreset} disabled={presetSaving || !selectedPresetId}>删除</button>
    </div>
    {hasPreviewAudio && (
      <audio
        ref={previewAudioRef}
        src={`${API}${project.audio_url}`}
        onTimeUpdate={handlePreviewTimeUpdate}
        onEnded={() => { setPlaying(false); setFade(1); }}
        hidden
      />
    )}
    <div className={statusWarning ? 'placement-status warning' : 'placement-status'}>{statusText}</div>
    <div className={timeline.truncated ? 'placement-status warning' : 'placement-status'} role="status" aria-live="polite">
      {timingKnown ? <>可用 {seconds(available)} · 已安排 {seconds(timeline.used)} · 剩余 {seconds(timeline.remaining)}
        {timeline.truncated && '；素材超出配音结尾，导出时将截断。'}
        {timeline.skipped > 0 && `；撑满图片之后的 ${timeline.skipped} 个素材不再播放。`}
      </> : '生成有效配音后显示剩余时长；当前无法准确判断是否超出。'}
    </div>
    <div className="product-placement-content">
      <div className="product-placement-left">
        <h4>已选带货素材</h4>
        {!items.length && <p className="placement-empty">还没有选择素材。从下方点击素材添加到画中画。</p>}
        <div className="product-item-list">
          {items.map((item, index) => {
            const asset = assetMap.get(item.asset_id);
            const video = asset?.media_type === 'video';
            const mode = item.duration_mode || 'custom';
            const rowTiming = timeline.rows[index];
            const endTime = timingKnown && rowTiming.end != null ? previewStartSec + rowTiming.end : null;
            const subtitle = endTime == null ? null : subtitleAtTime(subtitleTimings, endTime);
            const sliderMax = video ? Math.min(30, Number(asset.duration_sec) || 30) : 30;
            return <div className={index === selectedIndex ? 'product-item-row selected' : 'product-item-row'} key={`${item.asset_id}-${index}`}>
              <button type="button" className="product-item-name" onClick={() => setSelectedIndex(index)}>
                {asset?.file_name || '素材已删除'}
              </button>
              <input
                type="range"
                aria-label={`素材 ${index + 1} 展示时长`}
                aria-describedby={`product-subtitle-${projectId}-${index}`}
                min={Math.min(0.5, sliderMax)}
                max={sliderMax}
                step="0.1"
                disabled={mode !== 'custom' || rowTiming.skipped}
                value={Math.min(Number(item.duration_sec) || 3, sliderMax)}
                onChange={event => updateDuration(index, event.target.value)}
              />
              <span className="product-item-duration">
                {rowTiming.skipped ? '不再播放' : rowTiming.effective == null ? '至配音结尾' : seconds(rowTiming.effective)}
                {rowTiming.truncated && <strong className="product-truncated">超出 {seconds(rowTiming.wanted - rowTiming.effective)}，将截断</strong>}
              </span>
              <label className="product-fill-toggle" title={video ? '按素材原时长正常播放，超出配音结尾时截断' : '一直展示到配音结尾，后续素材不再播放'}>
                <input type="checkbox" checked={mode === (video ? 'original' : 'remaining')}
                  onChange={() => toggleDurationMode(index, video ? 'original' : 'remaining')} />
                {video ? '原时长' : '撑满时长'}
              </label>
              <div className="product-item-actions">
                <button type="button" disabled={index === 0} onClick={() => moveItem(index, -1)}>↑</button>
                <button type="button" disabled={index === items.length - 1} onClick={() => moveItem(index, 1)}>↓</button>
                <button type="button" className="danger" onClick={() => removeItem(index)}>删除</button>
              </div>
              <div className="product-item-subtitles" id={`product-subtitle-${projectId}-${index}`}>
                {rowTiming.skipped || rowTiming.effective === 0
                  ? <small>该素材不再播放，没有对应字幕。</small>
                  : !timingKnown
                    ? <small>{placement.start_sec_stale ? '文案已修改，请重新生成配音后对齐字幕。' : '生成配音并设置带货开始标记后，可查看切换位置的字幕。'}</small>
                    : !subtitleTimings.length
                      ? <small>当前配音暂无字幕时间信息。</small>
                      : <>
                        <small>
                          展示区间 {seconds(previewStartSec + rowTiming.start)} — {seconds(endTime)}
                          {endTime >= Number(project.audio_duration_sec) ? ' · 配音结尾' : ' · 拖动滑块查看切换处文案'}
                        </small>
                        {subtitle.previous && <p className="product-subtitle-context"><span>上一句</span>{subtitle.previous.text}</p>}
                        <p className="product-subtitle-current"><span>切换处</span>{
                          endTime >= Number(project.audio_duration_sec) ? '配音已结束'
                            : subtitle.current?.text || '此处为字幕间隙'
                        }</p>
                        {subtitle.next && <p className="product-subtitle-context"><span>下一句</span>{subtitle.next.text}</p>}
                      </>}
              </div>
            </div>;
          })}
        </div>
        <label className="product-transition-row">
          素材间转场：{Number(transitionSec).toFixed(1)} 秒
          <input
            type="range"
            min="0"
            max="2"
            step="0.1"
            value={transitionSec}
            onChange={event => setTransitionSec(Number(event.target.value))}
          />
          <small>首个素材淡入、最后一个淡出各 0.3 秒；中间使用剪映「叠化」。图片可撑满至配音结尾，后续素材不再播放；视频选择「原时长」后正常速度完整播放，超出配音结尾的部分将截断。</small>
        </label>
        <h4>添加带货素材</h4>
        <div className="product-picker">
          {(media.products || []).map(asset => <button type="button" key={asset.id} onClick={() => addItem(asset)}>
            {asset.media_type === 'image'
              ? <img src={`${API}${asset.file_url}`} alt={asset.file_name} />
              : <video muted preload="metadata" src={`${API}${asset.file_url}`} />}
            <span>{asset.file_name}</span>
            <small>+ 添加到画中画</small>
          </button>)}
          {!(media.products || []).length && <small>素材库还没有带货素材，请先上传。</small>}
        </div>
      </div>
      <div className="product-placement-right">
        <div
          ref={stageRef}
          className="pip-stage"
          style={template ? { backgroundImage: `url("${API}${template.file_url}")` } : undefined}
        >
          <div className="pip-guide pip-guide-title1">{project.title_line1 || '标题第一行'}</div>
          <div className="pip-guide pip-guide-title2">{project.title_line2 || '标题第二行'}</div>
          <div className="pip-guide pip-guide-subtitle">字幕区域</div>
          {selectedAsset ? (
            <div
              className="pip-box"
              style={{
                left: `${visiblePipBox.x * 100}%`,
                top: `${visiblePipBox.y * 100}%`,
                width: `${visiblePipBox.w * 100}%`,
                height: `${visiblePipBox.h * 100}%`,
              }}
              onPointerDown={event => beginPipInteraction(event, 'move')}
            >
              {selectedAsset.media_type === 'image'
                ? <img
                    key={`${selectedAsset.id}-${selectedIndex}-${playing}`}
                    src={`${API}${selectedAsset.file_url}`}
                    alt={selectedAsset.file_name}
                    style={{ opacity: fade, transition: 'opacity .3s' }}
                  />
                : <video
                    key={`${selectedAsset.id}-${selectedIndex}-${playing}`}
                    src={`${API}${selectedAsset.file_url}`}
                    muted
                    autoPlay
                    playsInline
                    style={{ opacity: fade, transition: 'opacity .3s' }}
                  />}
              <span
                className="pip-resize-handle"
                onPointerDown={event => beginPipInteraction(event, 'resize')}
              />
            </div>
          ) : (
            <div className="pip-empty">选择带货素材后，在这里拖动/缩放画中画</div>
          )}
        </div>
        <div className="pip-controls">
          <button type="button" disabled={!items.length} onClick={togglePreview}>
            {playing ? '停止预览' : (hasPreviewAudio ? '播放预览（同步配音）' : '播放预览')}
          </button>
          <small>
            {hasPreviewAudio
              ? `从${seconds(previewStartSec)}开始播放配音，画中画按实际时长切换；拖动可移动，右下角圆点可缩放当前素材。`
              : '生成配音后可同步试听；当前按顺序渐变预览。拖动/缩放只作用于当前选中的素材。'}
          </small>
          {previewError && <small className="placement-preview-error">{previewError}</small>}
        </div>
      </div>
    </div>
    {notice && <p className="placement-notice">{notice}</p>}
  </section>;
}
