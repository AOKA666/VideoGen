import React, { useEffect, useMemo, useRef, useState } from 'react';

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

  async function remove(asset) {
    if (!window.confirm(`删除带货素材「${asset.file_name}」？`)) return;
    try {
      await api(`/api/media/products/${asset.id}`, { method: 'DELETE' });
      await reload();
    } catch (error) {
      onError(error.message);
    }
  }

  const products = media.products || [];
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
    {!products.length && (
      <div className="media-empty">还没有带货素材。上传短视频或图片后，在下方配置画中画。</div>
    )}
    <div className="media-grid product-grid">
      {products.map(item => <article className="media-card product-card" key={item.id}>
        {item.media_type === 'image'
          ? <img src={`${API}${item.file_url}`} alt={item.file_name} />
          : <video controls preload="metadata" muted src={`${API}${item.file_url}`} />}
        <strong title={item.file_name}>{item.file_name}</strong>
        <p>{item.media_type === 'image'
          ? `图片 · ${item.width} × ${item.height}`
          : `${seconds(item.duration_sec)} · ${item.width} × ${item.height}`}</p>
        <button type="button" disabled={uploading} onClick={() => remove(item)}>删除素材</button>
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

export function ProductPlacementPanel({ project, projectId, media, busy, onProjectRefresh, onError }) {
  const placement = project.product_placement || {};
  const placementKey = JSON.stringify(placement);
  const [items, setItems] = useState([]);
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
      fill_duration: item.fill_duration !== false,
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
  const template = (media.templates || []).find(item => item.id === project.background_template_id);
  const markerPresent = String(project.raw_script || '').includes(PRODUCT_MARKER);
  const hasPreviewAudio = Boolean(project.audio_url);
  const previewStartSec = Number(placement.start_sec ?? 0) || 0;

  useEffect(() => {
    if (!playing || hasPreviewAudio || !items.length) {
      setFade(1);
      return undefined;
    }
    let fadeTimer;
    const interval = window.setInterval(() => {
      setFade(0);
      fadeTimer = window.setTimeout(() => {
        setSelectedIndex(current => (current + 1) % items.length);
        setFade(1);
      }, 300);
    }, 2200);
    return () => {
      window.clearInterval(interval);
      window.clearTimeout(fadeTimer);
      setFade(1);
    };
  }, [playing, hasPreviewAudio, items.length]);

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
      { asset_id: asset.id, duration_sec: duration, fill_duration: true, pip_box: box },
    ]);
    setSelectedIndex(nextIndex);
  }

  function updateDuration(index, value) {
    setItems(current => current.map((item, itemIndex) => {
      if (itemIndex !== index) return item;
      const asset = assetMap.get(item.asset_id);
      const maxDuration = asset?.media_type === 'video' && item.fill_duration === false
        ? Math.min(20, Number(asset.duration_sec) || 20)
        : 20;
      return { ...item, duration_sec: clamp(value, 0.5, maxDuration) };
    }));
  }

  function toggleFillDuration(index) {
    setItems(current => current.map((item, itemIndex) => {
      if (itemIndex !== index) return item;
      const asset = assetMap.get(item.asset_id);
      const fill = item.fill_duration === false;
      const maxDuration = asset?.media_type === 'video' && !fill
        ? Math.min(20, Number(asset.duration_sec) || 20)
        : 20;
      return {
        ...item,
        fill_duration: fill,
        duration_sec: Math.min(Number(item.duration_sec), maxDuration),
      };
    }));
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
        updateBox({
          ...start,
          w: clamp(start.w + dx, MIN_PIP, 1 - start.x),
          h: clamp(start.h + dy, MIN_PIP, 1 - start.y),
        });
      }
    };
    const end = () => {
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
    if (!items.length) return;
    setSelectedIndex(0);
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
    const elapsed = event.currentTarget.currentTime - previewStartSec;
    const total = items.reduce((sum, item) => sum + Number(item.duration_sec || 0), 0);
    if (elapsed < 0) {
      setFade(0.3);
      return;
    }
    if (elapsed >= total) {
      event.currentTarget.pause();
      setPlaying(false);
      setFade(1);
      return;
    }
    let cursor = 0;
    let active = 0;
    for (let index = 0; index < items.length; index += 1) {
      const duration = Number(items[index].duration_sec || 0);
      if (elapsed <= cursor + duration) {
        active = index;
        break;
      }
      cursor += duration;
    }
    const activeDuration = Number(items[active]?.duration_sec || 0);
    const fadeWindow = Math.min(0.3, activeDuration / 2);
    const within = elapsed - cursor;
    setSelectedIndex(active);
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
        fill_duration: item.fill_duration !== false,
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
          fill_duration: item.fill_duration !== false,
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

  async function save() {
    if (!projectId) return;
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
            fill_duration: item.fill_duration !== false,
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
    } catch (error) {
      onError?.(error.message);
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
    <div className="product-placement-content">
      <div className="product-placement-left">
        <h4>已选带货素材</h4>
        {!items.length && <p className="placement-empty">还没有选择素材。从下方点击素材添加到画中画。</p>}
        <div className="product-item-list">
          {items.map((item, index) => {
            const asset = assetMap.get(item.asset_id);
            const fill = item.fill_duration !== false;
            const sourceDuration = asset?.media_type === 'video' ? Number(asset.duration_sec || 0) : 0;
            const sliderMax = asset?.media_type === 'video' && !fill
              ? Math.min(20, sourceDuration || 20)
              : 20;
            const stretched = asset?.media_type === 'video'
              && fill && Number(item.duration_sec) > sourceDuration;
            return <div className={index === selectedIndex ? 'product-item-row selected' : 'product-item-row'} key={`${item.asset_id}-${index}`}>
              <button type="button" className="product-item-name" onClick={() => setSelectedIndex(index)}>
                {asset?.file_name || '素材已删除'}
              </button>
              <input
                type="range"
                min="0.5"
                max={sliderMax}
                step="0.5"
                value={Math.min(Number(item.duration_sec), sliderMax)}
                onChange={event => updateDuration(index, event.target.value)}
              />
              <span className="product-item-duration">
                {Number(item.duration_sec).toFixed(1)} 秒{stretched ? ' · 撑满' : ''}
              </span>
              {asset?.media_type === 'video' ? (
                <label className="product-fill-toggle" title="素材不够长时放慢视频，铺满所选时长">
                  <input type="checkbox" checked={fill} onChange={() => toggleFillDuration(index)} />
                  撑满时长
                </label>
              ) : <span />}
              <div className="product-item-actions">
                <button type="button" disabled={index === 0} onClick={() => moveItem(index, -1)}>↑</button>
                <button type="button" disabled={index === items.length - 1} onClick={() => moveItem(index, 1)}>↓</button>
                <button type="button" className="danger" onClick={() => removeItem(index)}>删除</button>
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
          <small>首个素材淡入、最后一个淡出各 0.3 秒；中间使用剪映「叠化」。视频超过原长时不再循环：勾选「撑满时长」会放慢铺满所选时长，未勾选则最多使用素材原长。</small>
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
                left: `${selectedPipBox.x * 100}%`,
                top: `${selectedPipBox.y * 100}%`,
                width: `${selectedPipBox.w * 100}%`,
                height: `${selectedPipBox.h * 100}%`,
              }}
              onPointerDown={event => beginPipInteraction(event, 'move')}
            >
              {selectedAsset.media_type === 'image'
                ? <img
                    key={selectedAsset.id}
                    src={`${API}${selectedAsset.file_url}`}
                    alt={selectedAsset.file_name}
                    style={{ opacity: fade, transition: 'opacity .3s' }}
                  />
                : <video
                    key={selectedAsset.id}
                    src={`${API}${selectedAsset.file_url}`}
                    muted
                    loop
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
