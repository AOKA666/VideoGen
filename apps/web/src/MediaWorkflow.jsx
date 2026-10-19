import React, { useState } from 'react';
import { ProductLibrary } from './ProductWorkflow';

const API = import.meta.env.VITE_API_URL ?? (import.meta.env.DEV ? 'http://127.0.0.1:8000' : '');
async function api(path, options = {}) {
  const response = await fetch(`${API}${path}`, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || '请求失败');
  return data;
}
const seconds = value => `${Number(value || 0).toFixed(3)} 秒`;

export function MediaLibrary({
  media,
  reload,
  onError,
  libraryTab = 'normal',
  onLibraryTabChange,
  project,
  projectId,
  busy,
  onProjectRefresh,
}) {
  return <section className="band media-workspace">
    <div className="media-subtabs" role="tablist" aria-label="素材分类">
      <button
        type="button"
        role="tab"
        aria-selected={libraryTab === 'normal'}
        className={libraryTab === 'normal' ? 'selected' : ''}
        onClick={() => onLibraryTabChange?.('normal')}
      >普通素材</button>
      <button
        type="button"
        role="tab"
        aria-selected={libraryTab === 'product'}
        className={libraryTab === 'product' ? 'selected' : ''}
        onClick={() => onLibraryTabChange?.('product')}
      >带货素材</button>
    </div>
    {libraryTab === 'product'
      ? <ProductLibrary
          media={media}
          reload={reload}
          onError={onError}
          project={project}
          projectId={projectId}
          busy={busy}
          onProjectRefresh={onProjectRefresh}
        />
      : <NormalMediaLibrary media={media} reload={reload} onError={onError} />}
  </section>;
}

function NormalMediaLibrary({ media, reload, onError }) {
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState('');
  async function upload(files) {
    setUploading(true);
    const failures = [];
    for (const [index, file] of [...files].entries()) {
      setProgress(`正在上传 ${index + 1}/${files.length}：${file.name}`);
      const body = new FormData(); body.append('file', file);
      try { await api('/api/media/videos', { method: 'POST', body }); }
      catch (error) { failures.push(`${file.name}：${error.message}`); }
    }
    try { await reload(); } catch (error) { failures.push(error.message); }
    setUploading(false); setProgress('');
    if (failures.length) onError(failures.join('；'));
  }
  return <>
    <div className="media-heading"><div><h2>普通素材</h2><p>上传后自动读取时长，所有项目都可以随机取用这些视频作为主画面。</p></div>
      <label className="media-upload">{uploading ? '上传中…' : '上传视频'}<input aria-label="上传视频" type="file" accept=".mp4,.mov,.mkv,.webm,.avi,.m4v" multiple disabled={uploading} onChange={event => { upload(event.target.files); event.target.value = ''; }} /></label>
    </div>
    <p>{media.videos.length} 个素材 · 总时长 {seconds(media.videos.reduce((sum, item) => sum + item.duration_sec, 0))} · 单个文件最大 1 GB</p>
    {progress && <p role="status">{progress}</p>}
    {!media.videos.length && <div className="media-empty">还没有普通素材。上传视频后，即可按配音时长自动拼接主画面。</div>}
    <div className="media-grid">{media.videos.map(item => <article className="media-card" key={item.id}>
      <video controls preload="metadata" src={`${API}${item.file_url}`} />
      <strong title={item.file_name}>{item.file_name}</strong><p>{seconds(item.duration_sec)} · {item.width} × {item.height}</p>
      <button disabled={uploading} onClick={async () => {
        if (!window.confirm(`删除素材「${item.file_name}」？`)) return;
        try { await api(`/api/media/videos/${item.id}`, { method: 'DELETE' }); await reload(); } catch (error) { onError(error.message); }
      }}>删除素材</button>
    </article>)}</div>
  </>;
}

export function TemplatePicker({ templates, value, onChange, reload, onError }) {
  const [uploading, setUploading] = useState(false);
  return <div className="template-picker"><div className="media-heading"><h3>背景模板 · 9:16</h3>
    <label className="media-upload">{uploading ? '上传中…' : '上传模板'}<input aria-label="上传背景模板" type="file" accept="image/png,image/jpeg,image/webp" disabled={uploading} onChange={async event => {
      const file = event.target.files[0]; event.target.value = ''; if (!file) return;
      setUploading(true); const body = new FormData(); body.append('file', file);
      try { const template = await api('/api/media/templates', { method: 'POST', body }); await reload(); onChange(template.id); }
      catch (error) { onError(error.message); } finally { setUploading(false); }
    }} /></label></div>
    <div className="template-options">{templates.map(template => <button type="button" aria-pressed={value === template.id} className={`template-option ${value === template.id ? 'selected' : ''}`} key={template.id} onClick={() => onChange(template.id)}>
      <img src={`${API}${template.file_url}`} alt={template.name} /><span>{template.name}</span>
    </button>)}</div>
    <small>素材保持原比例居中，放入模板中部区域；素材原声静音，使用所选配音。</small>
  </div>;
}
