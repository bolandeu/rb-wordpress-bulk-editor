function initApp(cmdsData, docsData, scenData) {
  renderDocs(docsData);
  renderCommands(cmdsData);
  renderScenarios(scenData.stages);
  renderConstructor(cmdsData);
  initTabs();
  initSearch();
}

// === HELPERS ===
// All data comes from local JSON files we control (commands.json, scenarios.json, docs.json)
// No user input is rendered as HTML. This is a static documentation page.
function setHTML(el, html) {
  // Safe: all content from our own JSON files, no user input
  el.innerHTML = html; // eslint-disable-line no-unsanitized/property
}

function esc(s) {
  if (s === null || s === undefined) return '';
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// === TABS ===
function initTabs() {
  document.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
      document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
      tab.classList.add('active');
      document.getElementById(tab.dataset.tab).classList.add('active');
    });
  });
}

// === SEARCH (фильтрует карточки команд) ===
function initSearch() {
  document.getElementById('search').addEventListener('input', e => {
    const q = e.target.value.toLowerCase();
    document.querySelectorAll('.cmd').forEach(cmd => {
      const text = cmd.textContent + ' ' + (cmd.dataset.search || '');
      cmd.style.display = text.toLowerCase().includes(q) ? '' : 'none';
    });
    document.querySelectorAll('.group').forEach(g => {
      const vis = g.querySelectorAll('.cmd:not([style*="display: none"])');
      g.style.display = vis.length || !q ? '' : 'none';
    });
  });
}

// === COMMANDS TAB ===
function renderCommands(data) {
  const panel = document.getElementById('commands');
  let html = '';
  data.groups.forEach(group => {
    html += '<div class="group"><div class="group-title">' + esc(group.title) + '</div>';
    group.commands.forEach(cmd => {
      html += '<div class="cmd" data-search="' + esc(cmd.name + ' ' + (cmd.desc || '') + ' ' + (cmd.when || '')) + '">';
      html += '<div class="cmd-header"><span class="cmd-name">' + esc(cmd.name) + '</span></div>';
      if (cmd.desc) html += '<div class="cmd-desc">' + esc(cmd.desc) + '</div>';
      if (cmd.when) html += '<div class="cmd-when">' + esc(cmd.when) + '</div>';
      html += '<div class="cmd-detail">';

      if (cmd.args && cmd.args.length) {
        html += '<h4>Аргументы</h4><ul class="args-list">';
        cmd.args.forEach(a => {
          html += '<li><code>' + esc(a.name) + '</code>';
          const badges = [];
          if (a.required) badges.push('<span class="badge-req">required</span>');
          if (a.type) badges.push('<span class="badge-type">' + esc(a.type) + '</span>');
          if (a.default !== undefined && a.default !== '' && a.default !== false) {
            badges.push('<span class="badge-default">default: ' + esc(a.default) + '</span>');
          }
          if (a.env) badges.push('<span class="badge-env">env: ' + esc(a.env) + '</span>');
          if (badges.length) html += ' ' + badges.join(' ');
          if (a.hint) html += ' — ' + esc(a.hint);
          if (a.type === 'select' && a.options && a.options.length) {
            html += '<ul class="args-options">';
            a.options.forEach(o => {
              html += '<li><code>' + esc(o.value || '(пусто)') + '</code> — ' + esc(o.label || o.value) + '</li>';
            });
            html += '</ul>';
          }
          html += '</li>';
        });
        html += '</ul>';
      }
      if (cmd.usage) {
        html += '<h4>Как использовать</h4><p style="color:var(--text2)">' + esc(cmd.usage) + '</p>';
      }
      if (cmd.tips) {
        html += '<h4>Совет</h4><p style="color:var(--warn);font-size:.78rem">' + esc(cmd.tips) + '</p>';
      }
      if (cmd.examples && cmd.examples.length) {
        html += '<h4>Примеры</h4><ul class="examples-list">';
        cmd.examples.forEach(ex => {
          if (typeof ex === 'string') {
            html += '<li><code>' + esc(ex) + '</code></li>';
          } else {
            html += '<li><code>' + esc(ex.cmd) + '</code>';
            if (ex.note) html += '<div class="example-note">' + esc(ex.note) + '</div>';
            html += '</li>';
          }
        });
        html += '</ul>';
      }
      html += '</div></div>';
    });
    html += '</div>';
  });
  setHTML(panel, html);
  panel.querySelectorAll('.cmd').forEach(c => {
    c.addEventListener('click', () => c.classList.toggle('open'));
  });
}

// === DOCS TAB (Основы) ===
function renderDocs(data) {
  const panel = document.getElementById('docs');
  if (!data || !data.sections) { setHTML(panel, '<p class="note">docs.json пуст.</p>'); return; }
  let html = '';
  data.sections.forEach(sec => {
    html += '<div class="doc-section">';
    html += '<h2 class="doc-title">' + esc(sec.title) + '</h2>';
    if (sec.subtitle) html += '<p class="doc-subtitle">' + esc(sec.subtitle) + '</p>';
    (sec.blocks || []).forEach(b => {
      html += renderDocBlock(b);
    });
    html += '</div>';
  });
  setHTML(panel, html);
}

function renderDocBlock(b) {
  switch (b.type) {
    case 'h':
      return '<h3 class="doc-h">' + esc(b.text) + '</h3>';
    case 'p':
      return '<p class="doc-p">' + esc(b.text) + '</p>';
    case 'info':
    case 'success':
    case 'warn': {
      const cls = b.type === 'info' ? 'doc-info' : (b.type === 'success' ? 'doc-success' : 'doc-warn');
      let out = '<div class="' + cls + '">';
      if (b.title) out += '<div class="doc-box-title">' + esc(b.title) + '</div>';
      if (b.text) out += '<div>' + esc(b.text) + '</div>';
      if (b.items && b.items.length) {
        out += '<ul>';
        b.items.forEach(i => { out += '<li>' + esc(i) + '</li>'; });
        out += '</ul>';
      }
      if (b.ordered && b.ordered.length) {
        out += '<ol>';
        b.ordered.forEach(i => { out += '<li>' + esc(i) + '</li>'; });
        out += '</ol>';
      }
      out += '</div>';
      return out;
    }
    case 'code':
      return '<pre class="doc-code"><code>' + esc(b.code) + '</code></pre>';
    case 'example': {
      let out = '<div class="doc-example"><code>' + esc(b.cmd) + '</code>';
      if (b.note) out += '<div class="example-note">' + esc(b.note) + '</div>';
      out += '</div>';
      return out;
    }
    case 'table': {
      let out = '<table class="doc-table"><thead><tr>';
      (b.headers || []).forEach(h => { out += '<th>' + esc(h) + '</th>'; });
      out += '</tr></thead><tbody>';
      (b.rows || []).forEach(row => {
        out += '<tr>';
        row.forEach(cell => { out += '<td>' + esc(cell) + '</td>'; });
        out += '</tr>';
      });
      out += '</tbody></table>';
      return out;
    }
    case 'list': {
      const tag = b.ordered ? 'ol' : 'ul';
      let out = '<' + tag + ' class="doc-list">';
      (b.items || []).forEach(i => { out += '<li>' + esc(i) + '</li>'; });
      out += '</' + tag + '>';
      return out;
    }
    default:
      return '';
  }
}

// === SCENARIOS TAB (Процессы) ===
function renderScenarios(stages) {
  const panel = document.getElementById('scenarios');
  if (!stages || !stages.length) { setHTML(panel, '<p class="note">scenarios.json пуст.</p>'); return; }
  let html = '';
  stages.forEach(stage => {
    html += '<div class="group"><div class="group-title">' + esc(stage.title) + '</div>';
    if (stage.pipeline) {
      html += '<div class="pipeline">' + esc(stage.pipeline) + '</div>';
    }
    (stage.scenarios || []).forEach(sc => {
      html += '<div class="scenario"><div class="scenario-title">' + esc(sc.title) + '</div>';
      if (sc.pipeline) html += '<div class="pipeline">' + esc(sc.pipeline) + '</div>';
      let stepNum = 0;
      (sc.steps || []).forEach(s => {
        if (s.type === 'note') {
          html += '<div class="note">' + esc(s.text) + '</div>';
        } else {
          stepNum++;
          const cmdCode = s.cmd ? '<code>' + esc(s.cmd) + '</code> — ' : '';
          const req = s.required === false ? ' <span class="step-opt">по ситуации</span>' : '';
          html += '<div class="step"><div class="step-num">' + stepNum + '</div><div class="step-text">' + cmdCode + esc(s.text || '') + req + '</div></div>';
        }
      });
      html += '</div>';
    });
    html += '</div>';
  });
  setHTML(panel, html);
}

// === CONSTRUCTOR TAB ===
let allCommands = [];

function renderConstructor(data) {
  const panel = document.getElementById('constructor');
  allCommands = [];
  data.groups.forEach(g => g.commands.forEach(c => allCommands.push(c)));

  let html = '<div class="constructor">';
  html += '<div class="field"><label>Проект (опционально, --project / -P)</label>';
  html += '<input type="text" id="con-project" placeholder="например: ost, tzm"></div>';
  html += '<div class="field"><label>Команда</label><select id="con-cmd"><option value="">Выбрать...</option>';
  data.groups.forEach(g => {
    html += '<optgroup label="' + esc(g.title) + '">';
    g.commands.forEach(c => {
      const short = c.desc && c.desc.length > 60 ? c.desc.substring(0, 60) + '...' : (c.desc || '');
      html += '<option value="' + esc(c.name) + '">' + esc(c.name) + (short ? ' — ' + esc(short) : '') + '</option>';
    });
    html += '</optgroup>';
  });
  html += '</select></div>';
  html += '<div id="con-args"></div>';
  html += '<div class="output-box"><span class="output-text" id="con-output">Выберите команду...</span>';
  html += '<button class="copy-btn" id="con-copy">Копировать</button></div>';
  html += '<div class="output-explain" id="con-explain" style="display:none"></div>';
  html += '</div>';

  setHTML(panel, html);

  document.getElementById('con-cmd').addEventListener('change', onCmdChange);
  document.getElementById('con-project').addEventListener('input', updateResult);
  document.getElementById('con-copy').addEventListener('click', copyResult);
}

function onCmdChange() {
  const name = document.getElementById('con-cmd').value;
  const cmd = allCommands.find(c => c.name === name);
  const argsDiv = document.getElementById('con-args');
  argsDiv.textContent = '';

  if (!cmd) {
    document.getElementById('con-output').textContent = 'Выберите команду...';
    document.getElementById('con-explain').style.display = 'none';
    return;
  }

  (cmd.args || []).forEach((a, i) => {
    const div = document.createElement('div');
    div.className = 'field';
    const label = document.createElement('label');
    const labelCode = document.createElement('code');
    labelCode.textContent = a.name;
    label.appendChild(labelCode);
    if (a.required) {
      const star = document.createElement('span');
      star.textContent = ' *';
      star.style.color = 'var(--danger)';
      label.appendChild(star);
    }
    if (a.hint) {
      const hint = document.createElement('span');
      hint.textContent = ' — ' + a.hint;
      hint.style.color = 'var(--text2)';
      label.appendChild(hint);
    }
    div.appendChild(label);

    if (a.type === 'select') {
      const sel = document.createElement('select');
      sel.dataset.argIndex = i;
      sel.dataset.argName = a.name;
      sel.dataset.argType = 'select';
      sel.className = 'con-arg';
      (a.options || []).forEach(o => {
        const opt = document.createElement('option');
        opt.value = o.value || '';
        opt.textContent = o.label || o.value || '';
        sel.appendChild(opt);
      });
      sel.addEventListener('change', updateResult);
      div.appendChild(sel);
    } else if (a.type === 'bool') {
      const wrap = document.createElement('label');
      wrap.style.display = 'flex';
      wrap.style.alignItems = 'center';
      wrap.style.gap = '.4rem';
      wrap.style.cursor = 'pointer';
      const chk = document.createElement('input');
      chk.type = 'checkbox';
      chk.dataset.argIndex = i;
      chk.dataset.argName = a.name;
      chk.dataset.argType = 'bool';
      chk.className = 'con-arg';
      chk.addEventListener('change', updateResult);
      wrap.appendChild(chk);
      const span = document.createElement('span');
      span.style.color = 'var(--text2)';
      span.style.fontSize = '.78rem';
      span.textContent = 'Включить флаг';
      wrap.appendChild(span);
      div.appendChild(wrap);
    } else {
      const inp = document.createElement('input');
      inp.type = a.type === 'number' ? 'number' : 'text';
      inp.placeholder = a.placeholder || '';
      inp.dataset.argIndex = i;
      inp.dataset.argName = a.name;
      inp.dataset.argType = a.type || 'text';
      inp.className = 'con-arg';
      inp.addEventListener('input', updateResult);
      div.appendChild(inp);
    }
    argsDiv.appendChild(div);
  });

  updateResult();
}

// Первый флаг из составного имени вида "--source / --src / -s" — для построения команды
function pickPrimaryFlag(name) {
  if (!name) return '';
  return String(name).split('/')[0].trim();
}

function updateResult() {
  const name = document.getElementById('con-cmd').value;
  const cmd = allCommands.find(c => c.name === name);
  if (!cmd) return;

  const project = (document.getElementById('con-project').value || '').trim();
  let result = 'wp-bulk';
  if (project) result += ' -P ' + project;
  result += ' ' + cmd.name;

  const explainParts = ['<strong>' + esc(cmd.name) + '</strong> — ' + esc(cmd.desc || '')];
  const argEls = document.querySelectorAll('#con-args .con-arg');

  argEls.forEach(el => {
    const argName = el.dataset.argName;
    const argType = el.dataset.argType;
    const flag = pickPrimaryFlag(argName);

    if (argType === 'bool') {
      if (el.checked) result += ' ' + flag;
    } else {
      const val = (el.value || '').trim();
      if (!val) return;
      if (argType === 'select') {
        result += ' ' + flag + ' ' + val;
      } else if (val.includes(' ')) {
        result += ' ' + flag + ' "' + val + '"';
      } else {
        result += ' ' + flag + ' ' + val;
      }
    }
  });

  document.getElementById('con-output').textContent = result;
  const explainDiv = document.getElementById('con-explain');
  setHTML(explainDiv, explainParts.join('<br>'));
  explainDiv.style.display = '';
}

function copyResult() {
  const btn = document.getElementById('con-copy');
  const text = document.getElementById('con-output').textContent;
  if (!text || text === 'Выберите команду...') return;
  copyText(btn, text);
}

function copyText(btn, text) {
  navigator.clipboard.writeText(text).then(() => {
    const orig = btn.textContent;
    btn.textContent = 'Скопировано!';
    btn.classList.add('copied');
    setTimeout(() => { btn.textContent = orig; btn.classList.remove('copied'); }, 1200);
  });
}
