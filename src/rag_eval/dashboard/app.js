(function () {
  'use strict';
  const C = globalThis.ExplorerCore;
  const dataNode = document.getElementById('dashboard-data');
  let data;
  try { data = JSON.parse(dataNode.textContent); } catch (error) { data = {entries: [], observations: {}, catalog: {}, warnings: [`数据无法解析：${error.message}`]}; }
  const observations = data.observations && typeof data.observations === 'object' ? data.observations : {};
  const entries = C.indexSearchText(Array.isArray(data.entries) ? data.entries : [], observations);
  const catalog = data.catalog && typeof data.catalog === 'object' ? data.catalog : {};
  const state = {filters: {}, query: '', page: 1, pageSize: 20, saved: []};
  const facetViews = new Map();
  const labels = {suite:'套件',task:'任务',track:'轨道',mode:'模式',scorer:'评分指标',status:'评分状态',output_status:'输出状态',behavior:'行为',partition:'分区',run_id:'运行',config_family:'配置族'};
  const errors = {empty_cohort:'组内没有条目',multiple_modes:'每个比较组必须只有一种模式',incompatible_dimensions:'套件、轨道、指标或配置族不一致',missing_dimension:'比较条目缺少 config_family',missing_identity:'条目缺少 pairing_id、case_id 或 scorer',duplicate_pair:'组内存在重复配对键，可能包含重试歧义',overlapping_runs:'两组包含同一 run',same_mode:'两组模式相同，无法作模式比较',no_common_pairs:'两组没有共同计划题'};
  const $ = (id) => document.getElementById(id);

  function el(tag, className, value) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (value != null) node.textContent = String(value);
    return node;
  }

  function svgEl(tag, attrs) {
    const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
    Object.entries(attrs || {}).forEach(([key, value]) => node.setAttribute(key, value));
    return node;
  }

  function formatNumber(value, digits = 3) {
    return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : '—';
  }
  function scorerLabel(value){return (catalog[value]&&catalog[value].name)||value||'未记录';}
  function trackLabel(value){return value==='native'?`${value} · 模型参照`:value==='product'?`${value} · SN 系统`:value||'未记录';}
  function shortConfig(value){const raw=value||'未记录';return raw.length>22?`${raw.slice(0,10)}…${raw.slice(-7)}`:raw;}

  function currentRows() { return C.filterEntries(entries, state.filters, state.query); }

  function describeFilter(filters, query) {
    const parts = [];
    for (const field of C.FACETS) if ((filters[field] || []).length) parts.push(`${labels[field]}：${filters[field].join('、')}`);
    if (query) parts.push(`搜索：“${query}”`);
    return parts.length ? parts.join(' · ') : '全部数据';
  }

  function renderFacets() {
    const host = $('facet-list'); host.replaceChildren();
    facetViews.clear();
    for (const field of C.FACETS) {
      const options = C.facetOptions(entries, field);
      if (!options.length) continue;
      const details = el('details', 'facet'); details.open = ['suite','mode','scorer','status'].includes(field);
      details.dataset.facet = field;
      const summaryCount = el('span', 'count');
      const summary = el('summary'); summary.append(el('span', '', labels[field]), summaryCount);
      const list = el('div', 'facet-options');
      const optionViews = new Map();
      for (const option of options) {
        const label = el('label', 'check');
        const input = document.createElement('input'); input.type = 'checkbox'; input.value = option.value; input.checked = (state.filters[field] || []).includes(option.value);
        input.dataset.facet = field;
        input.addEventListener('change', () => {
          const values = new Set(state.filters[field] || []);
          input.checked ? values.add(option.value) : values.delete(option.value);
          state.filters[field] = [...values]; state.page = 1; render();
        });
        const optionLabel=field==='scorer'?scorerLabel(option.value):field==='track'?trackLabel(option.value):option.value;const textNode=el('span','',optionLabel);if(field==='scorer'&&optionLabel!==option.value)textNode.title=option.value;if(field==='config_family')textNode.title=option.value;
        const count = el('span', 'count', option.count);
        label.append(input, textNode, count); list.append(label);
        optionViews.set(option.value, {label, input, count});
      }
      details.append(summary, list); host.append(details);
      facetViews.set(field, {details, summaryCount, options: optionViews});
    }
  }

  function updateFacets() {
    // Search the full observations once, not once per facet. Keep existing DOM
    // nodes so focus, expanded sections and each list's scroll position survive.
    const candidates = state.query ? C.filterEntries(entries, {}, state.query) : entries;
    let visibleGroups = 0;
    for (const [field, view] of facetViews) {
      const counts = new Map(C.facetOptions(candidates, field, state.filters).map((option) => [option.value, option.count]));
      const selected = state.filters[field] || [];
      for (const [value, option] of view.options) {
        const count = counts.get(value) || 0;
        option.input.checked = selected.includes(value);
        option.label.hidden = !counts.has(value);
        option.label.classList.toggle('zero-count', option.input.checked && count === 0);
        option.label.title = option.input.checked && count === 0 ? '当前其他条件下为 0 条；可取消此条件' : '';
        option.count.textContent = count;
      }
      view.details.hidden = counts.size === 0;
      if (counts.size) visibleGroups += 1;
      view.summaryCount.textContent = selected.length ? `${selected.length} 已选 / ${counts.size} 项` : `${counts.size} 项`;
    }
    $('facet-empty').hidden = visibleGroups > 0;
  }

  function renderChips() {
    const host = $('active-chips'); host.replaceChildren();
    for (const field of C.FACETS) for (const value of state.filters[field] || []) {
      const chip = el('span', 'chip', `${labels[field]} · ${value}`); const remove = el('button', '', '×'); remove.type = 'button'; remove.setAttribute('aria-label', `移除 ${value}`);
      remove.addEventListener('click', () => { state.filters[field] = state.filters[field].filter((item) => item !== value); state.page = 1; render(); });
      chip.append(remove); host.append(chip);
    }
  }

  function renderSummary(rows) {
    const summary = C.summarize(rows); const host = $('summary-cards'); host.replaceChildren();
    const cards = [
      ['计划问答次数', summary.predictions, '按 run × case 去重'], ['评分条目', summary.scoringEntries, 'run × case × scorer'],
      ['有效分数', summary.scoredEntries, '仅 scored 数值'], ['缺失 / 无效', summary.missingEntries, '仍计入计划分母'],
    ];
    cards.forEach(([name, value, note]) => { const card = el('article', 'summary-card'); card.append(el('span','',name), el('strong','',value), el('small','',note)); host.append(card); });
    $('visible-label').textContent = `${summary.scoringEntries} 条评分`;
  }

  function renderStatus(rows) {
    const host = $('status-chart'); host.replaceChildren();
    if (!rows.length) { host.append(el('div','empty-state','当前筛选没有评分条目')); return; }
    const counts = new Map(); rows.forEach((row) => { const key = C.facetValue(row,'status'); counts.set(key,(counts.get(key)||0)+1); });
    const values = [...counts]; const total = rows.length; const svg = svgEl('svg',{viewBox:'0 0 220 220','aria-hidden':'true'});const legend=el('div','status-legend');
    const semantic={scored:'#176b4c',missing:'#d99632',error:'#b84b43',failed:'#b84b43',skipped:'#397a92',not_applicable:'#8b948f'};let offset=0;const circumference=2*Math.PI*58;
    values.forEach(([name,count], index) => {
      const color=semantic[name]||['#6c877a','#7d6991','#9a774c'][index%3],length=count/total*circumference;const circle=svgEl('circle',{cx:105,cy:108,r:58,fill:'none',stroke:color,'stroke-width':24,'stroke-dasharray':`${length} ${circumference-length}`,'stroke-dashoffset':-offset,transform:'rotate(-90 105 108)'});circle.appendChild(document.createElementNS('http://www.w3.org/2000/svg','title')).textContent=`${name}: ${count}`;svg.append(circle);offset+=length;
      const item=el('div','status-legend-item');const dot=el('i');dot.style.background=color;item.append(dot,el('span','',name),el('strong','',`${count} · ${(count/total*100).toFixed(1)}%`));legend.append(item);
    });const totalText=svgEl('text',{x:105,y:105,'text-anchor':'middle',style:'font-size:24px;font-weight:800;fill:#17211d'});totalText.textContent=total;const label=svgEl('text',{x:105,y:124,'text-anchor':'middle'});label.textContent='计划评分';svg.append(totalText,label);const layout=el('div','status-layout');layout.append(svg,legend);host.append(layout);
  }

  function renderHistogram(rows) {
    const host = $('histogram-chart'); host.replaceChildren(); const result = C.scoreHistogram(rows, 10);
    $('histogram-hint').textContent = result.available ? `${scorerLabel(result.family)} · ${result.valid} 个有效分数` : '';
    if (!result.available) { const message = result.reason === 'mixed_metric_families' ? `请选择单一评分指标后查看（当前：${result.families.join('、')}）` : result.reason === 'incompatible_dimensions' ? '请继续筛选到单一套件、轨道、指标和配置族后查看' : '当前没有可绘制的有效分数'; host.append(el('div','empty-state',message)); return; }
    const svg = svgEl('svg',{viewBox:'0 0 500 220','aria-hidden':'true'}); const max = Math.max(1,...result.bins.map((bin)=>bin.count));
    result.bins.forEach((bin,index)=>{ const width=37, gap=5, x=38+index*(width+gap), height=bin.count/max*135, y=174-height; const rect=svgEl('rect',{x,y,width,height,fill:'#42a778'}); rect.appendChild(document.createElementNS('http://www.w3.org/2000/svg','title')).textContent=`${bin.from.toFixed(2)}–${bin.to.toFixed(2)}: ${bin.count}`; svg.append(rect); const tick=svgEl('text',{x:x+width/2,y:193,'text-anchor':'middle'}); tick.textContent=bin.from.toFixed(1); svg.append(tick); if(bin.count){const count=svgEl('text',{x:x+width/2,y:y-7,'text-anchor':'middle'});count.textContent=bin.count;svg.append(count);} });const endpoint=svgEl('text',{x:465,y:193,'text-anchor':'end'});endpoint.textContent=result.bins[result.bins.length-1].to.toFixed(1);svg.append(endpoint);
    svg.append(svgEl('line',{x1:35,y1:174,x2:465,y2:174,stroke:'#cbd1cb'})); host.append(svg);
  }

  function renderGroups(rows) {
    const host=$('group-chart');host.replaceChildren();const groups=C.groupScores(rows);
    if(!groups.length){host.append(el('div','empty-state','当前筛选没有分组'));return;}
    groups.forEach((group)=>{const row=el('div','group-row');const label=el('div','group-label');const primary=el('strong','',`${group.suite} · ${scorerLabel(group.scorer)}`);primary.title=group.scorer;const secondary=el('span','',`${trackLabel(group.track)} · ${shortConfig(group.config_family)} · ${group.mode} · ${group.task}`);secondary.title=group.config_family;label.append(primary,secondary);const visual=el('div','metric-bars');const scoreTrack=el('div','bar-track score-track');const fill=el('div','bar-fill');fill.style.width=`${Math.max(0,Math.min(100,(group.mean||0)*100))}%`;scoreTrack.append(fill);const coverage=el('div','coverage-track');const coverageFill=el('div','coverage-fill');coverageFill.style.width=`${group.planned?group.valid/group.planned*100:0}%`;coverage.append(coverageFill);visual.append(scoreTrack,coverage);const value=el('div','group-value');value.append(el('strong','',formatNumber(group.mean)),el('span','',`${group.valid}/${group.planned}`));row.append(label,visual,value);host.append(row);});
  }

  function renderEntries(rows) {
    const host=$('entry-body');host.replaceChildren();const pages=Math.max(1,Math.ceil(rows.length/state.pageSize));state.page=Math.min(state.page,pages);const start=(state.page-1)*state.pageSize;
    rows.slice(start,start+state.pageSize).forEach((entry)=>{const tr=el('tr');tr.tabIndex=0;tr.setAttribute('role','button');tr.setAttribute('aria-label',`打开 ${entry.case_id || entry.id} 详情`);const a=el('td','primary-cell');a.append(el('strong','',entry.suite||'未记录'),el('span','',entry.task||entry.case_id||'未记录'));const b=el('td','primary-cell');b.append(el('strong','',trackLabel(entry.track)),el('span','',entry.mode||'未记录'));const metricCell=el('td','primary-cell');metricCell.title=entry.scorer||'';metricCell.append(el('strong','',scorerLabel(entry.scorer)),el('span','',entry.scorer||'未记录'));const out=el('span',`status ${entry.output_status==='success'?'good':'bad'}`,entry.output_status||'未记录');const scoreStatus=el('span',`status ${entry.status==='scored'?'good':'bad'}`,entry.status||'missing');tr.append(a,b,metricCell,el('td',''),el('td',''),el('td','score',formatNumber(entry.score)));tr.children[3].append(out);tr.children[4].append(scoreStatus);tr.addEventListener('click',()=>openDetail(entry));tr.addEventListener('keydown',(event)=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();openDetail(entry);}});host.append(tr);});
    if(!rows.length){const tr=el('tr');const td=el('td','empty-state','没有符合条件的条目');td.colSpan=6;tr.append(td);host.append(tr);}
    $('page-label').textContent=`第 ${state.page} / ${pages} 页 · 共 ${rows.length} 条`;$('prev-page').disabled=state.page<=1;$('next-page').disabled=state.page>=pages;
  }

  function detailBlock(title, value, plain) {
    const block=el('section','detail-block');block.append(el('h3','',title));const content=el(plain?'div':'pre',plain?'detail-text':'');content.textContent=value==null||value===''?'未记录':(typeof value==='string'?value:JSON.stringify(value,null,2));block.append(content);return block;
  }

  function openDetail(entry) {
    const observation=observations[entry.observation_id]||{};const output=observation.output||{};const product=output.product_record||{};const response=product.response||{};const request=output.request||{};const metric=catalog[entry.scorer]||{};const runs=data.runs||[];const run=(entry.run_key?runs.find((item)=>item.key===entry.run_key||item.run_key===entry.run_key):runs.find((item)=>item.run_id===entry.run_id))||{};
    const tabs=[
      ['source','来源题目',()=>[detailBlock('Case',observation.case||entry.plan),detailBlock('计划身份',{pairing_id:entry.pairing_id,case_id:entry.case_id,partition:entry.partition,run_id:entry.run_id}),detailBlock('运行元数据',{key:run.key,path:run.path,phase:run.phase,suite:run.suite,track:run.track,mode:run.mode,config_family:run.config_family,warnings:run.warnings}),detailBlock('脱敏 Manifest',run.manifest)]],
      ['answer','实际问答',()=>{const blocks=[];if(product.question!=null)blocks.push(detailBlock('产品输入',product.question,true));if(request.prompt!=null||request.native_input!=null||request.expected_output!=null)blocks.push(detailBlock('Native 请求',{prompt:request.prompt,native_input:request.native_input,expected_output:request.expected_output}));blocks.push(detailBlock('实际输出',output.prediction,true));return blocks;}],
      ['retrieval','检索与引用',()=>[detailBlock('Retrieval context',product.retrieval_context||response.retrieval_context),detailBlock('Citations',response.citations),detailBlock('来源 / 文档身份',{source_ids:product.source_ids||response.source_ids,gold_document_ids:product.gold_document_ids||response.gold_document_ids,retrieved_document_ids:product.retrieved_document_ids||response.retrieved_document_ids}),detailBlock('Captures',product.captures||response.captures),detailBlock('确定性检查',product.deterministic||output.deterministic_checks||product.deterministic_checks||response.deterministic_checks)]],
      ['score','评分依据',()=>[detailBlock('评分结果',{status:entry.status,score:entry.score,reason:entry.reason}),metricNote(metric),detailBlock('原始 scorer details',entry.result?.details||entry.result?.score_details||entry.result)]],
      ['judge','Judge 事件',()=>{const resultId=entry.plan&&entry.plan.result_id;const events=(observation.events||[]).filter((event)=>event.role==='judge'&&resultId&&event.request_id===resultId);return events.length?[detailBlock('匹配的模型事件',events)]:[notice('该条目未保存匹配的 judge 模型事件；页面不会推测或补造评价过程。')];}],
      ['raw','原始 JSON',()=>[detailBlock('Entry',entry),detailBlock('Observation（含该题全部事件；未绑定到当前 scorer）',observation)]],
    ];
    $('detail-title').textContent=entry.case_id||entry.id||'评分条目';$('detail-kicker').textContent=`${entry.suite||'未记录'} · ${scorerLabel(entry.scorer)}`;const tabHost=$('detail-tabs');tabHost.replaceChildren();
    const show=(selected)=>{[...tabHost.children].forEach((button)=>button.setAttribute('aria-selected',String(button.dataset.tab===selected)));const content=$('detail-content');content.replaceChildren();const tab=tabs.find(([id])=>id===selected);tab[2]().forEach((node)=>content.append(node));};
    tabs.forEach(([id,name],index)=>{const button=el('button','',name);button.type='button';button.dataset.tab=id;button.setAttribute('role','tab');button.setAttribute('aria-selected',String(index===0));button.addEventListener('click',()=>show(id));button.addEventListener('keydown',(event)=>{if(!['ArrowLeft','ArrowRight'].includes(event.key))return;event.preventDefault();const buttons=[...tabHost.children];const step=event.key==='ArrowRight'?1:-1;const next=buttons[(buttons.indexOf(button)+step+buttons.length)%buttons.length];next.focus();show(next.dataset.tab);});tabHost.append(button);});show('source');$('detail-dialog').showModal();
  }

  function notice(message){return el('div','notice',message);}
  function metricNote(metric){const wrap=el('dl','metric-note');[['名称',metric.name],['方法',metric.method],['实现',metric.implementation],['输入',metric.inputs],['公式 / 步骤',metric.formula],['限制',metric.limitations],['代码位置',metric.code]].forEach(([name,value])=>{wrap.append(el('dt','',name),el('dd','',value==null?'未记录':typeof value==='string'?value:JSON.stringify(value)));});return wrap;}

  function comparisonGraphic(result) {
    const wrap=el('div','comparison-graphic');if(!result.commonValid)return wrap;
    const svg=svgEl('svg',{viewBox:'0 0 320 86','aria-label':`共同有效样本均值：参照 ${formatNumber(result.referenceMean)}，比较 ${formatNumber(result.comparisonMean)}`});const scale=(value)=>25+Math.max(0,Math.min(1,value))*270;svg.append(svgEl('line',{x1:25,y1:28,x2:295,y2:28,stroke:'#d5dad5','stroke-width':5}),svgEl('line',{x1:scale(result.referenceMean),y1:28,x2:scale(result.comparisonMean),y2:28,stroke:'#17211d','stroke-width':3}),svgEl('circle',{cx:scale(result.referenceMean),cy:28,r:7,fill:'#397a92'}),svgEl('circle',{cx:scale(result.comparisonMean),cy:28,r:7,fill:'#176b4c'}));const left=svgEl('text',{x:25,y:55});left.textContent=`参照 ${formatNumber(result.referenceMean)}`;const right=svgEl('text',{x:295,y:55,'text-anchor':'end'});right.textContent=`比较 ${formatNumber(result.comparisonMean)}`;svg.append(left,right);
    const total=result.wins+result.ties+result.losses;let x=25;[['胜',result.wins,'#176b4c'],['平',result.ties,'#8b948f'],['负',result.losses,'#b84b43']].forEach(([name,count,color])=>{const width=total?count/total*270:0;const rect=svgEl('rect',{x,y:68,width,height:9,fill:color});rect.appendChild(document.createElementNS('http://www.w3.org/2000/svg','title')).textContent=`${name} ${count}`;svg.append(rect);x+=width;});wrap.append(svg);return wrap;
  }

  function runConfigRows(rows) {
    const seen = new Set(); const configs = [];
    rows.forEach((row) => {
      const key = row.run_key || row.run_id;
      if (!key || seen.has(key)) return;
      seen.add(key);
      const run = (data.runs || []).find((item) => item.key === key || item.run_key === key || item.run_id === row.run_id) || {};
      const manifest = run.manifest || {};
      const identity = manifest.identity || {};
      configs.push({run: manifest.run_id || run.run_id || row.run_id || '未记录',
        models: identity.models || manifest.models || '未记录',
        product_protocol: manifest.product_protocol || '未记录',
        runtime_settings: identity.runtime_settings || '未记录',
        product_services: identity.product_services || '未记录',
        source: identity.source ? {dataset: identity.source.dataset, split: identity.source.split, revision: identity.source.revision} : '未记录'});
    });
    return configs;
  }

  function comparisonConfig(result, reference, comparison) {
    const wrap = el('div', 'comparison-config');
    [['参照组', result.configs.reference, reference.rows], ['比较组', result.configs.comparison, comparison.rows]].forEach(([label, config, rows]) => {
      const card = el('div', 'config-card');
      card.append(el('strong', '', label));
      card.append(el('span', '', `运行：${config.runs.join('、') || '未记录'}`));
      card.append(el('span', '', `模式：${config.modes.join('、') || '未记录'}`));
      card.append(el('span', '', `配置：${config.configs.join('、') || '未记录'}`));
      runConfigRows(rows).forEach((details) => {
        card.append(el('small', '', `模型：${JSON.stringify(details.models)}`));
        card.append(el('small', '', `协议：${details.product_protocol} · 设置哈希：${details.runtime_settings} · 服务哈希：${details.product_services}`));
        card.append(el('small', '', `来源：${JSON.stringify(details.source)}`));
      });
      wrap.append(card);
    });
    return wrap;
  }

  function comparisonStatus(result) {
    const wrap = el('div', 'comparison-status');
    [['评分状态', result.statusCounts], ['输出状态', result.outputStatusCounts]].forEach(([title, values]) => {
      const section = el('div', 'status-breakdown'); section.append(el('strong', '', title));
      const grid = el('div', 'status-breakdown-grid');
      [['参照', values.reference], ['比较', values.comparison]].forEach(([label, counts]) => {
        const item = el('div', 'status-breakdown-side'); item.append(el('span', '', label));
        Object.entries(counts).sort(([a], [b]) => a.localeCompare(b)).forEach(([name, count]) => item.append(el('small', '', `${name} ${count}`)));
        grid.append(item);
      });
      section.append(grid); wrap.append(section);
    });
    return wrap;
  }

  function comparisonPartitionTable(result) {
    const section = el('section', 'comparison-section');
    section.append(el('h5', '', '按分区查看差异'));
    const table = document.createElement('table'); table.className = 'comparison-table';
    const head = document.createElement('thead'); const headRow = document.createElement('tr');
    ['分区', '共同题', '共同有效', '参照均分', '比较均分', '差值'].forEach((name) => headRow.append(el('th', '', name)));
    head.append(headRow); table.append(head);
    const body = document.createElement('tbody');
    result.partitions.forEach((part) => {
      const row = document.createElement('tr');
      [part.partition, part.commonPlanned, part.commonValid, formatNumber(part.referenceMean), formatNumber(part.comparisonMean), formatNumber(part.meanDelta)].forEach((value) => row.append(el('td', '', value)));
      body.append(row);
    });
    table.append(body); section.append(table); return section;
  }

  function comparisonPairTable(result, reference, comparison) {
    const section = el('details', 'comparison-section pair-details');
    const summary = el('summary'); summary.append(el('strong', '', `逐题配对（${result.pairs.length} 题）`), el('span', '', '展开查看')); section.append(summary);
    const table = document.createElement('table'); table.className = 'comparison-table';
    const head = document.createElement('thead'); const headRow = document.createElement('tr');
    ['题目', '分区', '参照分数', '比较分数', '差值', '状态', '详情'].forEach((name) => headRow.append(el('th', '', name)));
    head.append(headRow); table.append(head);
    const body = document.createElement('tbody');
    result.pairs.forEach((pair) => {
      const row = document.createElement('tr');
      [pair.case_id, pair.partition, formatNumber(pair.reference.score), formatNumber(pair.comparison.score), formatNumber(pair.delta), pair.state].forEach((value) => row.append(el('td', '', value)));
      const actions = el('td');
      [['参照', pair.reference.id, reference.rows], ['比较', pair.comparison.id, comparison.rows]].forEach(([label, id, rows]) => {
        const button = el('button', 'pair-link', `查看${label}`); button.type = 'button';
        const target = rows.find((entry) => entry.id === id);
        button.disabled = !target; if (target) button.addEventListener('click', () => openDetail(target));
        actions.append(button);
      });
      row.append(actions); body.append(row);
    });
    table.append(body); section.append(table); return section;
  }

  function renderSaved() {
    const host=$('saved-groups');host.replaceChildren();state.saved.forEach((group,index)=>{const card=el('article',`saved-card ${index===0?'reference':''}`);const header=el('header');header.append(el('strong','',`${index===0?'参照 · ':''}${group.name}`));const remove=el('button','', '×');remove.type='button';remove.setAttribute('aria-label',`删除 ${group.name}`);remove.addEventListener('click',()=>{state.saved.splice(index,1);renderSaved();});header.append(remove);card.append(header,el('p','',group.description));C.groupScores(group.rows).forEach((summary)=>{const mini=el('div','saved-metric');const dimensions=el('span','',`${summary.suite} · ${trackLabel(summary.track)} · ${scorerLabel(summary.scorer)} · ${shortConfig(summary.config_family)} · ${summary.mode} · ${summary.task}`);dimensions.title=`${summary.scorer} · ${summary.config_family}`;mini.append(dimensions,el('i',''));mini.children[1].style.width=`${Math.max(0,Math.min(100,(summary.mean||0)*100))}%`;mini.append(el('small','',`${formatNumber(summary.mean)} · ${summary.valid}/${summary.planned}`));card.append(mini);});const restore=el('button','restore-button','恢复此筛选');restore.type='button';restore.addEventListener('click',()=>{state.filters=JSON.parse(JSON.stringify(group.filters));state.query=group.query;state.page=1;$('search-input').value=state.query;render();});card.append(restore);host.append(card);});
    const results=$('comparison-results');results.replaceChildren();if(state.saved.length<2){results.append(notice('保存至少两组筛选结果后开始比较。第一组自动作为参照组。'));return;}
    const reference=state.saved[0];state.saved.slice(1).forEach((group)=>{const result=C.compareCohorts(reference.rows,group.rows);const card=el('article',`comparison ${result.ok?'':'error'}`);card.append(el('h4','',`${reference.name} → ${group.name}`));if(!result.ok){card.append(el('div','delta',errors[result.code]||result.code));}else{const title=result.analysisType==='mode'?`模式对比：${result.referenceMode} → ${result.comparisonMode}`:'严格配对比较';card.append(el('div','comparison-question',title),el('div','delta',result.meanDelta==null?'无共同有效分数':`${result.meanDelta>=0?'+':''}${formatNumber(result.meanDelta)}`),el('p','',`${result.comparisonMode} 相对 ${result.referenceMode} 的共同有效均值差`),comparisonGraphic(result),comparisonConfig(result,reference,group),comparisonStatus(result),el('p','',`配对总体：共同计划 ${result.commonPlanned}；有效总体：共同有效 ${result.commonValid}，部分缺失 ${result.partialPairs}，双方缺失 ${result.missingBoth}`),el('p','',`共同有效中的胜 / 平 / 负 ${result.wins} / ${result.ties} / ${result.losses} · 非共同计划：仅参照 ${result.onlyA}，仅比较 ${result.onlyB}`),comparisonPartitionTable(result),comparisonPairTable(result,reference,group),el('p','comparison-footnote','图形与差值只使用共同有效配对；状态和配置用于解释差异。结果为描述性统计，不表示统计显著性或因果关系。'));}results.append(card);});
  }

  function renderDiagnostics() {
    const host=$('diagnostics');host.replaceChildren();const limitations=[...(data.limitations||[])];const warnings=[...(data.warnings||[])];const entryRuns=new Set(entries.map((entry)=>entry.run_key));(data.runs||[]).forEach((run)=>{const runWarnings=run.warnings||[];runWarnings.forEach((warning)=>warnings.push(`${run.run_id||run.key||run.path}：${typeof warning==='string'?warning:JSON.stringify(warning)}`));if(!entryRuns.has(run.key)&&!entryRuns.has(run.run_key))warnings.push(`${run.run_id||run.key||run.path}：该运行尚无评分条目（可能仍处于初始化阶段）`);});
    $('diagnostic-count').textContent=`${limitations.length} 条限制 · ${warnings.length} 条提示`;
    [['报告限制',limitations],['运行提示',warnings]].forEach(([title,items])=>{const section=el('section','diagnostic-list');section.append(el('h4','',title));if(!items.length)section.append(el('p','', '无'));else items.forEach((item)=>section.append(el('p','',typeof item==='string'?item:JSON.stringify(item))));host.append(section);});
  }

  function render() { const rows=currentRows();updateFacets();renderChips();renderSummary(rows);renderStatus(rows);renderHistogram(rows);renderGroups(rows);renderEntries(rows);renderSaved();renderDiagnostics(); }

  $('search-input').addEventListener('input',(event)=>{state.query=event.target.value;state.page=1;render();});
  $('clear-button').addEventListener('click',()=>{state.filters={};state.query='';state.page=1;$('search-input').value='';render();});
  $('prev-page').addEventListener('click',()=>{state.page-=1;renderEntries(currentRows());});$('next-page').addEventListener('click',()=>{state.page+=1;renderEntries(currentRows());});
  $('detail-close').addEventListener('click',()=>$('detail-dialog').close());$('detail-dialog').addEventListener('click',(event)=>{if(event.target===$('detail-dialog'))$('detail-dialog').close();});
  $('save-group-button').addEventListener('click',()=>{const rows=currentRows();const index=state.saved.length+1;const proposed=globalThis.prompt('比较组名称',`比较组 ${index}`);if(proposed==null)return;const name=proposed.trim()||`比较组 ${index}`;state.saved.push({name,description:describeFilter(state.filters,state.query),filters:JSON.parse(JSON.stringify(state.filters)),query:state.query,rows:[...rows]});renderSaved();});
  $('export-button').addEventListener('click',()=>{const selected=currentRows();const exportedEntries=selected.map(({search_text,...entry})=>entry);const observationIds=new Set(selected.map((entry)=>entry.observation_id));const runKeys=new Set(selected.map((entry)=>entry.run_key));const scorers=new Set(selected.map((entry)=>entry.scorer));const payload={exported_at:new Date().toISOString(),filters:state.filters,query:state.query,entries:exportedEntries,observations:Object.fromEntries(Object.entries(observations).filter(([key])=>observationIds.has(key))),runs:(data.runs||[]).filter((run)=>runKeys.has(run.key)||runKeys.has(run.run_key)),catalog:Object.fromEntries(Object.entries(catalog).filter(([key])=>scorers.has(key))),limitations:data.limitations||[],warnings:data.warnings||[]};const blob=new Blob([JSON.stringify(payload,null,2)],{type:'application/json'});const link=document.createElement('a');link.href=URL.createObjectURL(blob);link.download='dashboard-selection.json';link.click();setTimeout(()=>URL.revokeObjectURL(link.href),0);});
  renderFacets();render();
})();
