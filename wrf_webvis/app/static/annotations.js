/* 看板图片批注：缩略图上只读显示；放大窗口里可以画箭头、直线、矩形、椭圆、画笔、文字和便签。
 * 坐标一律用图片归一化坐标（左上 0,0，右下 1,1），与后端和 AI 批注一致。
 * 只读访客能看批注，不能画；编辑者的修改通过 /api/dashboards/<看板>/images/<图>/annotations 保存。 */
(function (root) {
  'use strict';

  const SVG_NS = 'http://www.w3.org/2000/svg';
  const TOOLS = [
    ['select', '选择'], ['arrow', '箭头'], ['line', '直线'], ['rect', '矩形'], ['ellipse', '椭圆'],
    ['freehand', '画笔'], ['text', '文字'], ['pin', '便签'],
  ];

  // ------------------------------------------------------------------ 几何（可在 node 里测试）
  function clamp01(value) { return Math.min(1, Math.max(0, value)); }

  function normalizedPoint(rect, clientX, clientY) {
    return [clamp01((clientX - rect.left) / rect.width), clamp01((clientY - rect.top) / rect.height)];
  }

  function scaleFor(width) { return Math.max(0.6, width / 1000); }

  function arrowHead(p0, p1, size) {
    const angle = Math.atan2(p1[1] - p0[1], p1[0] - p0[0]);
    const spread = Math.PI / 7;
    return [p1,
      [p1[0] - size * Math.cos(angle - spread), p1[1] - size * Math.sin(angle - spread)],
      [p1[0] - size * Math.cos(angle + spread), p1[1] - size * Math.sin(angle + spread)]];
  }

  function distanceToSegment(p, a, b) {
    const dx = b[0] - a[0], dy = b[1] - a[1];
    const length = dx * dx + dy * dy;
    const t = length ? Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length)) : 0;
    return Math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy));
  }

  /* 判断点 p（像素）是否落在形状上；W、H 是图片像素尺寸。 */
  function hitTest(shape, p, W, H, tolerance) {
    const pts = shape.points.map(([x, y]) => [x * W, y * H]);
    const tol = tolerance || 8;
    if (shape.kind === 'text' || shape.kind === 'pin') {
      const size = (shape.font_size || 15) * scaleFor(W);
      const width = Math.max(size, String(shape.text || '').length * size * 0.9);
      return p[0] >= pts[0][0] - tol && p[0] <= pts[0][0] + width + tol && p[1] >= pts[0][1] - size - tol && p[1] <= pts[0][1] + tol;
    }
    if (shape.kind === 'rect' || shape.kind === 'ellipse') {
      const [x0, x1] = [Math.min(pts[0][0], pts[1][0]), Math.max(pts[0][0], pts[1][0])];
      const [y0, y1] = [Math.min(pts[0][1], pts[1][1]), Math.max(pts[0][1], pts[1][1])];
      const inside = p[0] >= x0 - tol && p[0] <= x1 + tol && p[1] >= y0 - tol && p[1] <= y1 + tol;
      const nearEdge = Math.min(Math.abs(p[0] - x0), Math.abs(p[0] - x1), Math.abs(p[1] - y0), Math.abs(p[1] - y1)) <= tol * 2;
      return inside && (nearEdge || shape.kind === 'ellipse');
    }
    for (let i = 1; i < pts.length; i += 1) if (distanceToSegment(p, pts[i - 1], pts[i]) <= tol) return true;
    return false;
  }

  function shapeIsValid(shape) {
    const need = { arrow: 2, line: 2, rect: 2, ellipse: 2, text: 1, pin: 1 };
    if (shape.kind === 'freehand') return shape.points.length >= 2;
    if (need[shape.kind] !== shape.points.length) return false;
    if (need[shape.kind] === 2) {
      const [a, b] = shape.points;
      return Math.hypot(a[0] - b[0], a[1] - b[1]) > 0.004;
    }
    return true;
  }

  function simplify(points, minStep) {
    const out = [];
    for (const point of points) {
      const last = out[out.length - 1];
      if (!last || Math.hypot(point[0] - last[0], point[1] - last[1]) >= minStep) out.push(point);
    }
    if (points.length && out[out.length - 1] !== points[points.length - 1]) out.push(points[points.length - 1]);
    return out.slice(0, 2000);
  }

  // ------------------------------------------------------------------ 绘制
  // 把服务器返回的形状转回请求格式：有 data_points 和 panel 的保留物理坐标，其余用图片坐标
  function payloadShape(shape) {
    const out = { kind: shape.kind, text: shape.text || '' };
    if (Array.isArray(shape.data_points) && shape.data_points.length && shape.panel !== null && shape.panel !== undefined) {
      out.coords = 'data'; out.panel = shape.panel; out.points = shape.data_points;
    } else { out.coords = 'image'; out.points = shape.points; }
    for (const key of ['color', 'width', 'font_size']) if (shape[key] !== null && shape[key] !== undefined) out[key] = shape[key];
    return out;
  }

  // 撤销一步：draw → 去掉最后一个未保存形状；delete → 这条批注还在就 PATCH 回删除前的全部形状，已整条删掉就 POST 重建（同作者、同说明）
  function undoPlan(entry, rows) {
    if (!entry || entry.type === 'draw') return { kind: 'draft' };
    const prev = entry.annotation;
    const shapes = prev.shapes.map(payloadShape);
    if ((rows || []).some((row) => row.id === prev.id)) return { kind: 'request', method: 'PATCH', path: `/${encodeURIComponent(prev.id)}`, body: { shapes } };
    return { kind: 'request', method: 'POST', path: '', body: { shapes, note: prev.note || '', author: prev.author === 'ai' ? 'ai' : 'user' } };
  }

  function svgElement(tag, attrs, parent) {
    const node = root.document.createElementNS(SVG_NS, tag);
    for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, String(value));
    if (parent) parent.append(node);
    return node;
  }

  function drawShape(group, shape, W, H, options) {
    const s = scaleFor(W);
    const color = shape.color || '#e4572e';
    const stroke = (shape.width || 2.5) * s;
    const dash = options?.ai ? `${6 * s} ${4 * s}` : '';
    const common = { stroke: color, 'stroke-width': stroke, fill: 'none', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' };
    if (dash) common['stroke-dasharray'] = dash;
    const pts = shape.points.map(([x, y]) => [x * W, y * H]);
    const g = svgElement('g', { class: `annot-shape${options?.selected ? ' selected' : ''}` }, group);
    const label = (text, x, y, size) => {
      if (!text) return;
      const t = svgElement('text', { x, y, 'font-size': size, fill: color, 'font-weight': 600, 'paint-order': 'stroke',
        stroke: '#fff', 'stroke-width': size * 0.22, 'font-family': 'system-ui, sans-serif' }, g);
      t.textContent = text;
    };
    const fontSize = (shape.font_size || 15) * s;
    if (shape.kind === 'arrow' || shape.kind === 'line') {
      svgElement('line', { ...common, x1: pts[0][0], y1: pts[0][1], x2: pts[1][0], y2: pts[1][1] }, g);
      if (shape.kind === 'arrow') {
        const head = arrowHead(pts[0], pts[1], Math.max(10, stroke * 4));
        svgElement('polygon', { points: head.map((p) => p.join(',')).join(' '), fill: color, stroke: color, 'stroke-width': 1 }, g);
      }
      label(shape.text, pts[0][0] + 4 * s, pts[0][1] - 6 * s, fontSize);
    } else if (shape.kind === 'rect') {
      const x = Math.min(pts[0][0], pts[1][0]), y = Math.min(pts[0][1], pts[1][1]);
      svgElement('rect', { ...common, x, y, width: Math.abs(pts[1][0] - pts[0][0]), height: Math.abs(pts[1][1] - pts[0][1]) }, g);
      label(shape.text, x + 2 * s, y - 6 * s, fontSize);
    } else if (shape.kind === 'ellipse') {
      const cx = (pts[0][0] + pts[1][0]) / 2, cy = (pts[0][1] + pts[1][1]) / 2;
      const rx = Math.abs(pts[1][0] - pts[0][0]) / 2, ry = Math.abs(pts[1][1] - pts[0][1]) / 2;
      svgElement('ellipse', { ...common, cx, cy, rx, ry }, g);
      label(shape.text, cx - rx, cy - ry - 6 * s, fontSize);
    } else if (shape.kind === 'freehand') {
      svgElement('polyline', { ...common, points: pts.map((p) => p.join(',')).join(' ') }, g);
      label(shape.text, pts[0][0], pts[0][1] - 6 * s, fontSize);
    } else if (shape.kind === 'text') {
      label(shape.text || '文字', pts[0][0], pts[0][1], fontSize * 1.1);
    } else if (shape.kind === 'pin') {
      const r = 9 * s;
      svgElement('circle', { cx: pts[0][0], cy: pts[0][1], r, fill: color, stroke: '#fff', 'stroke-width': 2 * s }, g);
      const n = svgElement('text', { x: pts[0][0], y: pts[0][1] + r * 0.4, 'font-size': r * 1.1, fill: '#fff', 'text-anchor': 'middle',
        'font-weight': 700, 'font-family': 'system-ui, sans-serif' }, g);
      n.textContent = options?.number ?? '!';
      label(shape.text, pts[0][0] + r * 1.4, pts[0][1] + r * 0.4, fontSize);
    }
    if (options?.ai && (shape.kind !== 'text')) {
      const anchor = pts[pts.length - 1];
      const badge = svgElement('text', { x: anchor[0] + 4 * s, y: anchor[1] + 14 * s, 'font-size': 10 * s, fill: color,
        'font-weight': 700, 'font-family': 'system-ui, sans-serif', 'paint-order': 'stroke', stroke: '#fff', 'stroke-width': 2.5 * s }, g);
      badge.textContent = 'AI';
    }
    return g;
  }

  function drawCanvasShape(ctx, shape, W, H, ai) {
    const s = scaleFor(W);
    const color = shape.color || '#e4572e';
    const stroke = (shape.width || 2.5) * s;
    const pts = shape.points.map(([x, y]) => [x * W, y * H]);
    const fontSize = (shape.font_size || 15) * s;
    ctx.save();
    ctx.strokeStyle = color; ctx.fillStyle = color; ctx.lineWidth = stroke; ctx.lineCap = 'round'; ctx.lineJoin = 'round';
    ctx.setLineDash(ai ? [6 * s, 4 * s] : []);
    const label = (text, x, y, size) => {
      if (!text) return;
      ctx.setLineDash([]);
      ctx.font = `600 ${size}px system-ui, sans-serif`;
      ctx.lineWidth = size * 0.22; ctx.strokeStyle = '#fff'; ctx.strokeText(text, x, y);
      ctx.fillStyle = color; ctx.fillText(text, x, y);
    };
    if (shape.kind === 'arrow' || shape.kind === 'line' || shape.kind === 'freehand') {
      ctx.beginPath(); pts.forEach((p, i) => (i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1]))); ctx.stroke();
      if (shape.kind === 'arrow') {
        const head = arrowHead(pts[0], pts[1], Math.max(10, stroke * 4));
        ctx.setLineDash([]); ctx.beginPath(); head.forEach((p, i) => (i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1]))); ctx.closePath(); ctx.fill();
      }
      label(shape.text, pts[0][0] + 4 * s, pts[0][1] - 6 * s, fontSize);
    } else if (shape.kind === 'rect') {
      const x = Math.min(pts[0][0], pts[1][0]), y = Math.min(pts[0][1], pts[1][1]);
      ctx.strokeRect(x, y, Math.abs(pts[1][0] - pts[0][0]), Math.abs(pts[1][1] - pts[0][1]));
      label(shape.text, x + 2 * s, y - 6 * s, fontSize);
    } else if (shape.kind === 'ellipse') {
      const cx = (pts[0][0] + pts[1][0]) / 2, cy = (pts[0][1] + pts[1][1]) / 2;
      const rx = Math.abs(pts[1][0] - pts[0][0]) / 2, ry = Math.abs(pts[1][1] - pts[0][1]) / 2;
      ctx.beginPath(); ctx.ellipse(cx, cy, Math.max(rx, 0.5), Math.max(ry, 0.5), 0, 0, Math.PI * 2); ctx.stroke();
      label(shape.text, cx - rx, cy - ry - 6 * s, fontSize);
    } else if (shape.kind === 'text') {
      label(shape.text || '文字', pts[0][0], pts[0][1], fontSize * 1.1);
    } else if (shape.kind === 'pin') {
      const r = 9 * s;
      ctx.setLineDash([]); ctx.beginPath(); ctx.arc(pts[0][0], pts[0][1], r, 0, Math.PI * 2); ctx.fill();
      label(shape.text, pts[0][0] + r * 1.4, pts[0][1] + r * 0.4, fontSize);
    }
    ctx.restore();
  }

  // ------------------------------------------------------------------ 状态与网络
  const state = {
    dashboardId: null,
    byImage: new Map(),
    visible: true,
    editor: null,
  };
  try { state.visible = localStorage.getItem('wrf_webvis.annotations.visible') !== 'false'; } catch (_) { /* 无存储 */ }

  async function request(url, options = {}) {
    const method = options.method || 'GET';
    if (root.WRFWebVisAccess) await root.WRFWebVisAccess.assertWrite(method, url);
    const response = await fetch(url, {
      method, headers: options.body ? { 'Content-Type': 'application/json' } : undefined,
      body: options.body ? JSON.stringify(options.body) : undefined,
    });
    if (!response.ok) {
      let detail = `请求失败（${response.status}）`;
      try {
        const data = await response.json();
        detail = typeof data.detail === 'string' ? data.detail : (data.detail?.message || (Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join('；') : detail));
      } catch (_) { /* 非 JSON */ }
      throw new Error(detail);
    }
    return response.json();
  }

  async function loadDashboard(dashboardId) {
    state.dashboardId = dashboardId;
    state.byImage = new Map();
    if (!dashboardId) return;
    try {
      const rows = await request(`/api/dashboards/${encodeURIComponent(dashboardId)}/annotations`);
      if (state.dashboardId !== dashboardId) return;
      for (const row of rows) {
        if (!state.byImage.has(row.image_id)) state.byImage.set(row.image_id, []);
        state.byImage.get(row.image_id).push(row);
      }
    } catch (_) { /* 批注读取失败不影响看图 */ }
    refreshOverlays();
  }

  function annotationsFor(imageId) { return state.byImage.get(imageId) || []; }

  // ------------------------------------------------------------------ 缩略图上的只读批注
  const overlays = new Set();

  function renderOverlay(svg, img, imageId, extra) {
    const W = img.naturalWidth, H = img.naturalHeight;
    svg.replaceChildren();
    if (!W || !H) return;
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    svg.style.display = state.visible ? '' : 'none';
    let pin = 0;
    for (const annotation of annotationsFor(imageId)) {
      for (const [index, shape] of annotation.shapes.entries()) {
        const selected = extra?.selected && extra.selected.annotationId === annotation.id && extra.selected.index === index;
        drawShape(svg, shape, W, H, { ai: annotation.author === 'ai', selected, number: shape.kind === 'pin' ? String(++pin) : undefined });
      }
    }
    for (const shape of extra?.draft || []) drawShape(svg, shape, W, H, { selected: true });
  }

  function attach(img, image) {
    const frame = root.document.createElement('div');
    frame.className = 'annot-frame annot-thumb';
    img.replaceWith(frame);
    frame.append(img);
    const svg = svgElement('svg', { class: 'annot-overlay', 'aria-hidden': 'true', preserveAspectRatio: 'none' }, frame);
    const entry = { svg, img, imageId: image.id };
    overlays.add(entry);
    const draw = () => renderOverlay(svg, img, image.id);
    if (img.complete && img.naturalWidth) draw(); else img.addEventListener('load', draw, { once: true });
    const count = annotationsFor(image.id).length;
    if (count) frame.dataset.count = String(count);
    return frame;
  }

  function refreshOverlays() {
    for (const entry of [...overlays]) {
      if (!entry.svg.isConnected) { overlays.delete(entry); continue; }
      renderOverlay(entry.svg, entry.img, entry.imageId);
      const frame = entry.svg.parentElement;
      const count = annotationsFor(entry.imageId).length;
      if (count) frame.dataset.count = String(count); else delete frame.dataset.count;
    }
    if (state.editor) renderEditor();
  }

  function setVisible(visible) {
    state.visible = Boolean(visible);
    try { localStorage.setItem('wrf_webvis.annotations.visible', String(state.visible)); } catch (_) { /* 无存储 */ }
    refreshOverlays();
  }

  // ------------------------------------------------------------------ 放大窗口里的编辑器
  function byId(id) { return root.document.getElementById(id); }

  // 2026-11-06：root 和勾了“可批注”的账号都能画；普通账号只能删改自己写的那组（后端也会检查）。
  function canWrite() { return root.WRFWebVisAccess?.canAnnotate?.() === true || root.WRFWebVisAccess?.canWrite?.() === true; }
  function isRoot() { return root.WRFWebVisAccess?.canWrite?.() === true; }
  function ownsAnnotation(annotation) {
    if (isRoot()) return true;
    const me = root.WRFWebVisAccess?.user?.().username;
    return Boolean(me) && annotation?.created_by === me;
  }

  function openEditor(image, options = {}) {
    const img = byId('zoomImage');
    const frame = byId('zoomFrame');
    if (!img || !frame || !state.dashboardId) return;
    let svg = frame.querySelector('svg.annot-overlay');
    if (!svg) svg = svgElement('svg', { class: 'annot-overlay annot-editable', preserveAspectRatio: 'none' }, frame);
    state.editor = { image, img, svg, frame, tool: 'select', draft: [], drawing: null, selected: null, history: [], saveNote: options.saveNote, noteDirty: false };
    buildToolbar();
    buildSidePanel();
    const ready = () => { fitImage(); renderEditor(); };
    if (img.complete && img.naturalWidth) ready(); else img.addEventListener('load', ready, { once: true });
    svg.onpointerdown = onPointerDown;
    svg.onpointermove = onPointerMove;
    svg.onpointerup = onPointerUp;
    svg.onpointercancel = onPointerUp;
    state.editor.onKey = (event) => {
      // 编辑器打开时 ⌘Z / Ctrl+Z 撤销批注操作（在输入框里打字时不拦截）
      if (!(event.metaKey || event.ctrlKey) || event.shiftKey || String(event.key).toLowerCase() !== 'z') return;
      const tag = String(event.target?.tagName || '').toLowerCase();
      if (tag === 'input' || tag === 'textarea' || event.target?.isContentEditable) return;
      if (!canWrite()) return;
      event.preventDefault(); event.stopImmediatePropagation();
      undoLast();
    };
    root.addEventListener?.('keydown', state.editor.onKey, true);
  }

  function closeEditor() {
    const editor = state.editor;
    if (editor && (editor.draft.length || editor.noteDirty) && canWrite()) {
      if (!root.confirm('有还没保存的批注，确定放弃吗？')) return false;
    }
    if (editor?.onKey) root.removeEventListener?.('keydown', editor.onKey, true);
    state.editor = null;
    return true;
  }

  function fitImage() {
    const editor = state.editor;
    if (!editor) return;
    const stage = editor.frame.parentElement;
    const W = editor.img.naturalWidth, H = editor.img.naturalHeight;
    if (!W || !H || !stage) return;
    const scale = Math.min((stage.clientWidth - 8) / W, (stage.clientHeight - 8) / H, 2);
    editor.img.style.width = `${Math.max(50, Math.floor(W * scale))}px`;
    editor.img.style.height = `${Math.max(50, Math.floor(H * scale))}px`;
  }

  function buildToolbar() {
    const bar = byId('annotationToolbar');
    if (!bar) return;
    bar.replaceChildren();
    const editor = state.editor;
    const group = root.document.createElement('div'); group.className = 'annot-tools'; group.setAttribute('role', 'toolbar');
    for (const [tool, label] of TOOLS) {
      const button = root.document.createElement('button');
      button.type = 'button'; button.textContent = label; button.dataset.tool = tool;
      button.classList.toggle('active', editor.tool === tool);
      button.addEventListener('click', () => { editor.tool = tool; editor.selected = null; buildToolbar(); renderEditor(); });
      group.append(button);
    }
    const color = root.document.createElement('input');
    color.type = 'color'; color.value = editor.color || '#e4572e'; color.title = '颜色'; color.id = 'annotationColor';
    color.addEventListener('input', () => { editor.color = color.value; });
    const width = root.document.createElement('select'); width.id = 'annotationWidth'; width.title = '线宽';
    for (const [value, label] of [['1.5', '细'], ['2.5', '中'], ['4', '粗']]) {
      const option = root.document.createElement('option'); option.value = value; option.textContent = label; width.append(option);
    }
    width.value = String(editor.width || 2.5);
    width.addEventListener('change', () => { editor.width = Number(width.value); });
    const note = root.document.createElement('input');
    note.type = 'text'; note.id = 'annotationNote'; note.placeholder = '这组批注的说明（可选）'; note.value = editor.note || '';
    note.addEventListener('input', () => { editor.note = note.value; });
    const undo = button('撤销', undoLast);
    undo.title = '撤销上一步：新画的形状、删除所选、删除这组（也可按 ⌘Z / Ctrl+Z）';
    const remove = button('删除所选', deleteSelected);
    const save = button('保存批注', saveDraft); save.classList.add('primary');
    const exporter = button('导出带批注 PNG', exportPng);
    const toggle = button(state.visible ? '隐藏批注' : '显示批注', () => { setVisible(!state.visible); buildToolbar(); });
    const status = root.document.createElement('span'); status.id = 'annotationStatus'; status.className = 'annot-status';
    const writeOnly = [group, color, width, note, undo, remove, save];
    writeOnly.forEach((node) => { node.dataset.writeOnly = ''; });
    bar.append(group, color, width, note, undo, remove, save, toggle, exporter, status);
  }

  function button(label, handler) {
    const node = root.document.createElement('button');
    node.type = 'button'; node.textContent = label; node.addEventListener('click', handler);
    return node;
  }

  function setStatus(text, error) {
    const node = byId('annotationStatus');
    if (node) { node.textContent = text || ''; node.classList.toggle('error', Boolean(error)); }
  }

  function renderEditor() {
    const editor = state.editor;
    if (!editor) return;
    renderOverlay(editor.svg, editor.img, editor.image.id, { draft: editor.drawing ? [...editor.draft, editor.drawing] : editor.draft, selected: editor.selected });
    editor.svg.classList.toggle('annot-drawing', canWrite() && editor.tool !== 'select');
    editor.svg.style.pointerEvents = canWrite() ? 'auto' : 'none';
    renderList();
  }

  // 右栏分两块：上面是图片的文字批注（和卡片下方“批注”栏是同一份），下面是画在图上的批注组。
  // 文字批注只在打开时建一次，画图时 renderList 只刷新下面那块，免得打字时被冲掉。
  function buildSidePanel() {
    const list = byId('annotationList');
    const editor = state.editor;
    if (!list || !editor) return;
    const noteSection = root.document.createElement('section'); noteSection.className = 'annot-note-section';
    const noteHead = root.document.createElement('h4'); noteHead.textContent = '文字批注';
    noteSection.append(noteHead);
    if (canWrite()) {
      const textarea = root.document.createElement('textarea');
      textarea.rows = 10; textarea.className = 'annot-note-input';
      textarea.placeholder = '写下对这张图的看法、结论或要核对的问题（和卡片下方“批注”栏是同一份）';
      textarea.value = editor.image.note || '';
      const status = root.document.createElement('span'); status.className = 'annot-status';
      textarea.addEventListener('input', () => { editor.noteDirty = textarea.value !== (editor.image.note || ''); status.textContent = editor.noteDirty ? '未保存' : ''; });
      const save = button('保存文字批注', () => saveNote(textarea, status));
      const actions = root.document.createElement('div'); actions.className = 'annot-note-actions';
      actions.append(save, status);
      noteSection.append(textarea, actions);
    } else {
      const text = root.document.createElement('p');
      text.className = editor.image.note ? 'annot-note-text' : 'annot-muted';
      text.textContent = editor.image.note || '（空）';
      noteSection.append(text);
    }
    const body = root.document.createElement('div'); body.className = 'annot-list-body';
    list.replaceChildren(noteSection, body);
    editor.listBody = body;
  }

  async function saveNote(textarea, status) {
    const editor = state.editor;
    if (!editor) return;
    const image = editor.image;
    status.classList.remove('error');
    status.textContent = '正在保存…';
    try {
      if (editor.saveNote) await editor.saveNote(textarea.value);
      else {
        const updated = await request(`/api/dashboards/${encodeURIComponent(state.dashboardId)}/images/${encodeURIComponent(image.id)}/note`, { method: 'PUT', body: { note: textarea.value } });
        image.note = (updated.images || []).find((item) => item.id === image.id)?.note || '';
      }
      if (state.editor !== editor) return;
      textarea.value = image.note || '';
      editor.noteDirty = false;
      status.textContent = '已保存';
    } catch (error) {
      status.textContent = `保存失败：${error.message}`;
      status.classList.add('error');
    }
  }

  function renderList() {
    const editor = state.editor;
    if (!editor) return;
    if (!editor.listBody || !editor.listBody.isConnected) buildSidePanel();
    const list = editor.listBody;
    if (!list) return;
    list.replaceChildren();
    const head = root.document.createElement('h4'); head.textContent = '图上批注'; list.append(head);
    const rows = annotationsFor(editor.image.id);
    if (!rows.length && !editor.draft.length) {
      const empty = root.document.createElement('p'); empty.className = 'annot-muted';
      empty.textContent = canWrite() ? '还没有画在图上的批注。选一个工具在图上画；画完点“保存批注”。' : '这张图还没有画在图上的批注。';
      list.append(empty);
    }
    if (editor.draft.length) {
      const draft = root.document.createElement('p'); draft.className = 'annot-draft';
      draft.textContent = `未保存：${editor.draft.length} 个形状`;
      list.append(draft);
    }
    for (const annotation of rows) {
      const card = root.document.createElement('article'); card.className = `annot-card${annotation.author === 'ai' ? ' ai' : ''}`;
      const meta = root.document.createElement('div'); meta.className = 'annot-meta';
      meta.textContent = `${annotation.author === 'ai' ? 'AI' : (annotation.created_by && annotation.created_by !== root.WRFWebVisAccess?.user?.().username ? annotation.created_by : '我')} · ${String(annotation.updated_at || '').replace('T', ' ').slice(0, 16)} · ${annotation.shapes.length} 个形状`;
      card.append(meta);
      if (annotation.note) { const p = root.document.createElement('p'); p.textContent = annotation.note; card.append(p); }
      const labels = annotation.shapes.map((shape) => shape.text).filter(Boolean);
      if (labels.length) { const p = root.document.createElement('p'); p.className = 'annot-muted'; p.textContent = labels.join(' / '); card.append(p); }
      if (canWrite() && ownsAnnotation(annotation)) {
        const remove = button('删除这组', async () => {
          if (!root.confirm('删除这组批注？（图片不受影响）')) return;
          try {
            const before = JSON.parse(JSON.stringify(annotation));
            await request(`/api/dashboards/${state.dashboardId}/images/${editor.image.id}/annotations/${annotation.id}`, { method: 'DELETE' });
            editor.history.push({ type: 'delete', annotation: before });
            await loadDashboard(state.dashboardId);
            setStatus('已删除这组。点“撤销”或按 ⌘Z 可恢复。');
          } catch (error) { setStatus(error.message, true); }
        });
        card.append(remove);
      }
      list.append(card);
    }
  }

  function pixelPoint(event) {
    const editor = state.editor;
    const rect = editor.svg.getBoundingClientRect();
    const point = normalizedPoint(rect, event.clientX, event.clientY);
    return { point, pixel: [point[0] * editor.img.naturalWidth, point[1] * editor.img.naturalHeight], rect };
  }

  function onPointerDown(event) {
    const editor = state.editor;
    if (!editor || !canWrite()) return;
    const { point, pixel, rect } = pixelPoint(event);
    if (editor.tool === 'select') {
      const tolerance = 8 * editor.img.naturalWidth / rect.width;
      editor.selected = null;
      for (const annotation of annotationsFor(editor.image.id)) {
        if (!ownsAnnotation(annotation)) continue;  // 别人写的批注不能选中修改
        annotation.shapes.forEach((shape, index) => {
          if (hitTest(shape, pixel, editor.img.naturalWidth, editor.img.naturalHeight, tolerance)) editor.selected = { annotationId: annotation.id, index };
        });
      }
      renderEditor();
      setStatus(editor.selected ? '已选中一个形状，可点“删除所选”。' : '');
      return;
    }
    const base = { color: editor.color || '#e4572e', width: editor.width || 2.5 };
    if (editor.tool === 'text' || editor.tool === 'pin') {
      const text = root.prompt(editor.tool === 'text' ? '文字内容' : '便签内容', '');
      if (text === null || (editor.tool === 'text' && !text.trim())) return;
      editor.draft.push({ ...base, kind: editor.tool, points: [point], text: text.trim() });
      editor.history.push({ type: 'draw' });
      renderEditor();
      return;
    }
    editor.drawing = { ...base, kind: editor.tool, points: editor.tool === 'freehand' ? [point] : [point, point], text: '' };
    editor.svg.setPointerCapture(event.pointerId);
    event.preventDefault();
  }

  function onPointerMove(event) {
    const editor = state.editor;
    if (!editor?.drawing) return;
    const { point } = pixelPoint(event);
    if (editor.drawing.kind === 'freehand') editor.drawing.points.push(point);
    else editor.drawing.points[1] = point;
    renderEditor();
  }

  function onPointerUp(event) {
    const editor = state.editor;
    if (!editor?.drawing) return;
    const shape = editor.drawing;
    editor.drawing = null;
    if (shape.kind === 'freehand') shape.points = simplify(shape.points, 0.002);
    if (shapeIsValid(shape)) {
      if (shape.kind !== 'freehand') {
        const text = root.prompt('给这个形状加一个短标签（可留空）', '');
        if (text) shape.text = text.trim();
      }
      editor.draft.push(shape);
      editor.history.push({ type: 'draw' });
    }
    try { editor.svg.releasePointerCapture(event.pointerId); } catch (_) { /* 已释放 */ }
    renderEditor();
  }

  async function saveDraft() {
    const editor = state.editor;
    if (!editor || !editor.draft.length) { setStatus('没有新画的批注。'); return; }
    try {
      setStatus('正在保存…');
      await request(`/api/dashboards/${state.dashboardId}/images/${editor.image.id}/annotations`, {
        method: 'POST', body: { shapes: editor.draft, note: editor.note || '', author: 'user' },
      });
      editor.draft = []; editor.note = '';
      editor.history = editor.history.filter((entry) => entry.type !== 'draw');
      buildToolbar();
      await loadDashboard(state.dashboardId);
      setStatus('批注已保存，其他人刷新后可见。');
    } catch (error) { setStatus(error.message, true); }
  }

  async function undoLast() {
    const editor = state.editor;
    if (!editor || !canWrite()) return;
    let entry = editor.history.pop();
    while (entry && entry.type === 'draw' && !editor.draft.length) entry = editor.history.pop();   // 已保存的新形状不在这里撤
    if (!entry) {
      if (editor.draft.length) { editor.draft.pop(); renderEditor(); return; }
      setStatus('没有可撤销的操作。'); return;
    }
    const plan = undoPlan(entry, annotationsFor(editor.image.id));
    if (plan.kind === 'draft') { editor.draft.pop(); renderEditor(); return; }
    try {
      setStatus('正在撤销…');
      await request(`/api/dashboards/${state.dashboardId}/images/${editor.image.id}/annotations${plan.path}`, { method: plan.method, body: plan.body });
      editor.selected = null;
      await loadDashboard(state.dashboardId);
      setStatus('已恢复删除的批注。');
    } catch (error) {
      editor.history.push(entry);
      setStatus(error.message, true);
    }
  }

  async function deleteSelected() {
    const editor = state.editor;
    if (!editor?.selected) { setStatus('先用“选择”工具点中一个形状。'); return; }
    const annotation = annotationsFor(editor.image.id).find((row) => row.id === editor.selected.annotationId);
    if (!annotation || !root.confirm('删除选中的形状？')) return;
    const before = JSON.parse(JSON.stringify(annotation));
    const remaining = annotation.shapes.filter((_, index) => index !== editor.selected.index).map(payloadShape);
    const base = `/api/dashboards/${state.dashboardId}/images/${editor.image.id}/annotations/${annotation.id}`;
    try {
      if (remaining.length) await request(base, { method: 'PATCH', body: { shapes: remaining } });
      else await request(base, { method: 'DELETE' });
      editor.selected = null;
      editor.history.push({ type: 'delete', annotation: before });
      await loadDashboard(state.dashboardId);
      setStatus('已删除。点“撤销”或按 ⌘Z 可恢复。');
    } catch (error) { setStatus(error.message, true); }
  }

  async function exportPng() {
    const editor = state.editor;
    if (!editor) return;
    const img = editor.img;
    const canvas = root.document.createElement('canvas');
    canvas.width = img.naturalWidth; canvas.height = img.naturalHeight;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(img, 0, 0);
    for (const annotation of annotationsFor(editor.image.id)) {
      for (const shape of annotation.shapes) drawCanvasShape(ctx, shape, canvas.width, canvas.height, annotation.author === 'ai');
    }
    for (const shape of editor.draft) drawCanvasShape(ctx, shape, canvas.width, canvas.height, false);
    canvas.toBlob((blob) => {
      if (!blob) { setStatus('导出失败', true); return; }
      const link = root.document.createElement('a');
      link.href = URL.createObjectURL(blob);
      link.download = `${(editor.image.title || 'figure').replace(/[\\/:*?"<>|]+/g, '_')}_批注.png`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(link.href), 5000);
    }, 'image/png');
  }

  if (root.addEventListener) root.addEventListener('resize', () => { if (state.editor) fitImage(); });

  const api = {
    // 几何（测试用）
    normalizedPoint, arrowHead, hitTest, shapeIsValid, simplify, distanceToSegment, payloadShape, undoPlan,
    // 页面
    loadDashboard, attach, refreshOverlays, setVisible, openEditor, closeEditor, annotationsFor,
    get visible() { return state.visible; },
    get dashboardId() { return state.dashboardId; },
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.WRFWebVisAnnotations = api;
})(typeof window !== 'undefined' ? window : globalThis);
