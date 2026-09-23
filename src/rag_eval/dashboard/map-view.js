(function (root, factory) {
  const api = factory();
  root.SNExperimentMap = api;
  if (typeof module === 'object' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  // This is a display projection. The complete provenance graph stays in the
  // report and inspector, including edges omitted here when a path conveys it.
  function layoutGraph(graph) {
    const nodes = graph.nodes || [], edges = graph.edges || [];
    const byId = new Map(nodes.map(n => [n.id, n]));
    const rescoring = n => n && n.kind === 'run' && n.operation === 'rescoring';
    const hasRescore = nodes.some(rescoring);
    const columns = ['资料来源', '生成运行', '答卷', ...(hasRescore ? ['重新评分'] : []), '评分结果'];
    const cardWidth = 208, cardHeight = 82, stride = 268, rowHeight = 112;
    const positions = new Map(), occupied = new Map();
    function place(n, column, row) {
      if (positions.has(n.id)) return;
      while (occupied.has(`${column}:${row}`)) row += 1;
      occupied.set(`${column}:${row}`, n.id);
      positions.set(n.id, {...n, x: 28 + column * stride, y: 84 + row * rowHeight,
        width: cardWidth, height: cardHeight, row});
    }
    let row = 0;
    const sources = [...new Set(nodes.map(n => n.source_id).filter(Boolean))];
    if (nodes.some(n => n.kind === 'run' && !n.source_id)) sources.push(null);
    sources.forEach(source => {
      const group = nodes.filter(n => n.kind === 'run' && (n.source_id || null) === source);
      if (byId.has(source)) place(byId.get(source), 0, row);
      group.forEach(n => {
        place(n, rescoring(n) ? 3 : 1, row);
        edges.filter(e => e.source === n.id && e.kind === 'generate').forEach(e => {
          if (byId.has(e.target)) place(byId.get(e.target), 2, row);
        });
        edges.filter(e => e.source === n.id && e.kind === 'scoring_batch').forEach(e => {
          if (byId.has(e.target)) place(byId.get(e.target), columns.length - 1, row);
        });
        row += 1;
      });
      if (!group.length) row += 1;
    });
    nodes.filter(n => n.kind === 'answers' && !positions.has(n.id)).forEach(n => {
      const next = edges.find(e => e.source === n.id && e.kind === 'rescore');
      place(n, 2, next && positions.has(next.target) ? positions.get(next.target).row : row++);
    });
    nodes.filter(n => !positions.has(n.id)).forEach(n => {
      const next = edges.find(e => e.source === n.id && positions.has(e.target));
      const column = ['dataset', 'external'].includes(n.kind) ? 0 : n.kind === 'scores' ? columns.length - 1 : 1;
      place(n, column, next ? positions.get(next.target).row : row++);
    });
    const visibleEdges = edges.filter(e => {
      if (!positions.has(e.source) || !positions.has(e.target)) return false;
      if (e.kind === 'source' && rescoring(byId.get(e.target))) return false;
      if (e.kind === 'scoring_batch' && !rescoring(byId.get(e.source))) {
        return !edges.some(g => g.source === e.source && g.kind === 'generate' &&
          edges.some(v => v.source === g.target && v.target === e.target && v.kind === 'evaluate'));
      }
      if (e.kind === 'evaluate') {
        return !edges.some(r => r.source === e.source && r.kind === 'rescore' &&
          edges.some(s => s.source === r.target && s.target === e.target && s.kind === 'scoring_batch'));
      }
      return true;
    });
    return {nodes: [...positions.values()], edges: visibleEdges,
      columns: columns.map((label, i) => ({label, x: 28 + i * stride})),
      bounds: {width: 56 + (columns.length - 1) * stride + cardWidth,
        height: Math.max(300, ...[...positions.values()].map(n => n.y + n.height + 32))}};
  }

  function fitView(bounds, width, height) {
    width = Math.max(1, width); height = Math.max(1, height);
    const scale = Math.min(width / Math.max(1, bounds.width), height / Math.max(1, bounds.height), 1);
    return {x: (bounds.width - width / scale) / 2, y: (bounds.height - height / scale) / 2,
      width: width / scale, height: height / scale};
  }

  function zoomView(view, factor, fx, fy, pixelWidth, minimum, maximum) {
    const scale = Math.min(maximum, Math.max(minimum, pixelWidth / view.width * factor));
    const width = pixelWidth / scale, height = width * view.height / view.width;
    return {x: view.x + (view.width - width) * fx, y: view.y + (view.height - height) * fy, width, height};
  }

  function mount({svg, graph, label, onSelect, controls}) {
    const layout = layoutGraph(graph), positions = new Map(layout.nodes.map(n => [n.id, n]));
    const ns = 'http://www.w3.org/2000/svg';
    let view, fitted = true, selected = null, drag = null, ignoreClick = false, lastWidth = 0;
    function el(tag, attrs, text) {
      const element = document.createElementNS(ns, tag);
      Object.entries(attrs || {}).forEach(([key, value]) => element.setAttribute(key, String(value)));
      if (text != null) element.textContent = text;
      return element;
    }
    function apply() {
      if (!view) return;
      svg.setAttribute('viewBox', `${view.x} ${view.y} ${view.width} ${view.height}`);
      controls.percent.textContent = `${Math.round(svg.clientWidth / view.width * 100)}%`;
    }
    function fit() { if (!svg.clientWidth || !svg.clientHeight) return; fitted = true; view = fitView(layout.bounds, svg.clientWidth, svg.clientHeight); apply(); }
    function zoom(factor, fx = .5, fy = .5) {
      if (!view) return;
      fitted = false;
      const fitScale = svg.clientWidth / fitView(layout.bounds, svg.clientWidth, svg.clientHeight).width;
      view = zoomView(view, factor, fx, fy, svg.clientWidth, Math.min(.08, fitScale), 2.5); apply();
    }
    function reveal(id, center = false) {
      const n = positions.get(id);
      if (!n || !view) return;
      const fits = n.x >= view.x && n.y >= view.y && n.x+n.width <= view.x+view.width && n.y+n.height <= view.y+view.height;
      if (fits && !center) return;
      const scale = Math.min(1, svg.clientWidth / (n.width + 64));
      view = {x: n.x+n.width/2-svg.clientWidth/scale/2, y: n.y+n.height/2-svg.clientHeight/scale/2,
        width: svg.clientWidth/scale, height: svg.clientHeight/scale};
      fitted = false; apply();
    }
    function select(id) {
      selected = id;
      svg.querySelectorAll('.map-node').forEach(g => {
        g.classList.toggle('selected', g.dataset.nodeId === id);
        g.setAttribute('aria-pressed', String(g.dataset.nodeId === id));
      });
      svg.querySelectorAll('.edge').forEach(e => e.classList.toggle('highlight', e.dataset.source === id || e.dataset.target === id));
      controls.locate.disabled = !positions.has(id);
    }
    svg.replaceChildren();
    const defs = el('defs');
    const marker = el('marker', {id: 'map-arrow', viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 5, markerHeight: 5, orient: 'auto-start-reverse'});
    marker.append(el('path', {d: 'M 1 1 L 9 5 L 1 9', fill: 'none', stroke: '#8b96a4', 'stroke-width': 1.5}));
    defs.append(marker); svg.append(defs);
    layout.columns.forEach((c, index) => {
      svg.append(el('text', {x: c.x, y: 35, class: 'map-column-number'}, String(index+1).padStart(2, '0')),
        el('text', {x: c.x+28, y: 35, class: 'map-column-title'}, c.label),
        el('line', {x1: c.x, y1: 53, x2: c.x+208, y2: 53, class: 'map-column-rule'}));
    });
    layout.edges.forEach(e => {
      const a = positions.get(e.source), b = positions.get(e.target);
      const x = a.x+a.width, y = a.y+a.height/2, tx = b.x, ty = b.y+b.height/2;
      const mid = (x+tx)/2;
      const path = el('path', {d: `M ${x} ${y} C ${mid} ${y}, ${mid} ${ty}, ${tx-3} ${ty}`,
        class: `edge ${e.kind === 'rescore' ? 'reuse' : ''}`, 'marker-end': 'url(#map-arrow)',
        'data-source': e.source, 'data-target': e.target});
      path.append(el('title', {}, e.provenance || e.kind)); svg.append(path);
    });
    layout.nodes.forEach(n => {
      const text = label(n);
      const group = el('g', {class: `map-node ${n.kind}${n.operation === 'rescoring' ? ' rescore' : ''}`,
        tabindex: 0, role: 'button', 'aria-label': `${text.title}，${text.meta}，${text.detail}`,
        'data-node-id': n.id, transform: `translate(${n.x} ${n.y})`});
      group.append(el('title', {}, `${n.label || n.id}\n${text.meta}\n${text.detail}`),
        el('rect', {width: n.width, height: n.height, rx: 3}),
        el('text', {x: 13, y: 25, class: 'node-title'}, text.title),
        el('text', {x: 13, y: 45, class: 'node-meta'}, text.meta),
        el('text', {x: 13, y: 66, class: 'node-detail'}, text.detail));
      group.addEventListener('click', event => { if (ignoreClick) {event.preventDefault(); return;} select(n.id); onSelect(n); });
      group.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') {event.preventDefault(); event.stopPropagation(); select(n.id); onSelect(n);}
      });
      group.addEventListener('focus', () => reveal(n.id));
      svg.append(group);
      // SVG text doesn't wrap. Fit only long labels; full text remains in title/inspector.
      [...group.querySelectorAll('text')].forEach(t => {
        const full = t.textContent;
        if (t.getComputedTextLength() <= n.width-26) return;
        let lo = 0, hi = full.length;
        while (lo < hi) {const mid = Math.ceil((lo+hi)/2); t.textContent = full.slice(0,mid)+'…'; if(t.getComputedTextLength() > n.width-26) hi=mid-1; else lo=mid;}
        t.textContent = full.slice(0,lo)+'…';
      });
    });
    if (!layout.nodes.length) svg.append(el('text', {x: 28, y: 105, class: 'map-column-title'}, '没有可展示的实验关系'));
    svg.addEventListener('pointerdown', event => {
      if (event.button !== 0 || !event.isPrimary || !view) return;
      ignoreClick = false;
      drag = {id: event.pointerId, x: event.clientX, y: event.clientY, view: {...view}, moved: false};
    });
    svg.addEventListener('pointermove', event => {
      if (!drag || drag.id !== event.pointerId) return;
      const dx = event.clientX-drag.x, dy = event.clientY-drag.y;
      if (!drag.moved && Math.hypot(dx,dy) < 5) return;
      if (!drag.moved) {drag.moved = true; svg.setPointerCapture(event.pointerId); svg.classList.add('dragging');}
      fitted = false; ignoreClick = true;
      view = {...drag.view, x: drag.view.x-dx*drag.view.width/svg.clientWidth, y: drag.view.y-dy*drag.view.height/svg.clientHeight}; apply();
    });
    function endDrag(event) {
      if (!drag || drag.id !== event.pointerId) return;
      drag = null; svg.classList.remove('dragging');
      if (svg.hasPointerCapture(event.pointerId)) svg.releasePointerCapture(event.pointerId);
    }
    svg.addEventListener('pointerup', endDrag);
    svg.addEventListener('pointercancel', endDrag);
    svg.addEventListener('lostpointercapture', endDrag);
    svg.addEventListener('wheel', event => {
      event.preventDefault(); const box = svg.getBoundingClientRect();
      zoom(Math.exp(-Math.max(-100, Math.min(100, event.deltaY))*.003), (event.clientX-box.left)/box.width, (event.clientY-box.top)/box.height);
    }, {passive: false});
    svg.addEventListener('keydown', event => {
      if (!view) return;
      const directions = {ArrowLeft: [-1,0], ArrowRight:[1,0], ArrowUp:[0,-1], ArrowDown:[0,1]};
      if (directions[event.key]) {
        event.preventDefault(); event.stopPropagation(); fitted=false;
        view = {...view, x:view.x+directions[event.key][0]*view.width*.12, y:view.y+directions[event.key][1]*view.height*.12}; apply();
      } else if (['+','=','-','0','Home'].includes(event.key)) {
        event.preventDefault(); event.stopPropagation();
        if (event.key==='0' || event.key==='Home') fit(); else zoom(event.key==='-' ? 1/1.2 : 1.2);
      }
    });
    controls.minus.addEventListener('click', () => zoom(1/1.2));
    controls.plus.addEventListener('click', () => zoom(1.2));
    controls.fit.addEventListener('click', fit);
    controls.actual.addEventListener('click', () => {if(view) zoom(view.width/svg.clientWidth);});
    controls.locate.addEventListener('click', () => reveal(selected, true));
    const resize = new ResizeObserver(() => {
      if (!svg.clientWidth || !svg.clientHeight) return;
      if (fitted || !view) fit();
      else {
        const scale = lastWidth / view.width || 1, width=svg.clientWidth/scale, height=svg.clientHeight/scale;
        view = {x:view.x+(view.width-width)/2, y:view.y+(view.height-height)/2, width, height}; apply();
      }
      lastWidth = svg.clientWidth;
    });
    resize.observe(svg); fit(); lastWidth=svg.clientWidth;
    return {select, fit, locate: () => reveal(selected, true)};
  }
  return {layoutGraph, fitView, zoomView, mount};
});
