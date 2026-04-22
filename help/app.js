function initApp(cmdsData, scenData) {
  renderCommands(cmdsData);
  renderHooks(scenData.hooks);
  renderScenarios(scenData.stages);
  renderScrum(scenData.scrum, scenData.roles);
  renderConstructor(cmdsData);
  initTabs();
  initSearch();
}

// === HELPERS ===
// All data comes from local JSON files we control (commands.json, scenarios.json)
// No user input is rendered as HTML. This is a static documentation page.
function setHTML(el, html) {
  // Safe: all content from our own JSON files, no user input
  el.innerHTML = html; // eslint-disable-line no-unsanitized/property
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

// === SEARCH ===
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
      const planned = cmd.planned ? ' planned' : '';
      const tag = cmd.tag ? '<span class="tag plugin">' + esc(cmd.tag) + '</span>' : '';
      const plannedTag = cmd.planned ? '<span class="tag planned">planned</span>' : '';
      html += '<div class="cmd' + planned + '" data-search="' + esc(cmd.name + ' ' + cmd.desc) + '">';
      html += '<div class="cmd-header"><span class="cmd-name">' + esc(cmd.name) + '</span>' + tag + plannedTag + '</div>';
      html += '<div class="cmd-desc">' + esc(cmd.desc) + '</div>';
      html += '<div class="cmd-when">' + esc(cmd.when) + '</div>';
      html += '<div class="cmd-detail">';
      if (cmd.args && cmd.args.length) {
        html += '<h4>Аргументы</h4><ul>';
        cmd.args.forEach(a => {
          if (a.type === 'select') {
            html += '<li><code>' + esc(a.name) + '</code>';
            if (!a.required) html += ' (необязательный)';
            html += '<ul>';
            a.options.forEach(o => {
              html += '<li><code>' + esc(o.value || '(без аргумента)') + '</code> — ' + esc(o.label) + '</li>';
            });
            html += '</ul></li>';
          } else {
            html += '<li><code>' + esc(a.name) + '</code>';
            if (a.required) html += ' (обязательный)';
            if (a.hint) html += ' — ' + esc(a.hint);
            html += '</li>';
          }
        });
        html += '</ul>';
      }
      if (cmd.usage) {
        html += '<h4>Как использовать</h4><p style="color:var(--text2)">' + esc(cmd.usage) + '</p>';
      }
      if (cmd.tips) {
        html += '<h4>Совет</h4><p style="color:var(--warn);font-size:.78rem">' + esc(cmd.tips) + '</p>';
      }
      if (cmd.examples) {
        html += '<h4>Примеры</h4><ul>';
        cmd.examples.forEach(ex => {
          html += '<li><code>' + esc(ex) + '</code></li>';
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

// === HOOKS TAB ===
function renderHooks(hooks) {
  const panel = document.getElementById('hooks');
  let html = '<div class="group"><div class="group-title">Pipeline хуков</div>';
  html += '<table class="hooks-table"><tr><th>Хук</th><th>Событие</th><th>Matcher</th><th>Что делает</th></tr>';
  hooks.pipeline.forEach(h => {
    html += '<tr><td>' + esc(h.file) + '</td><td><span class="event">' + esc(h.event) + '</span></td><td>' + esc(h.matcher) + '</td><td>' + esc(h.desc) + '</td></tr>';
  });
  html += '</table></div>';

  html += '<div class="group"><div class="group-title">Разрешения (settings.example.json)</div>';
  html += '<div class="scenario"><div class="scenario-title">Deny-лист (всегда заблокировано)</div>';
  hooks.deny.forEach(d => {
    html += '<div class="step"><div class="step-num">&times;</div><div class="step-text"><code>' + esc(d) + '</code></div></div>';
  });
  html += '</div><div class="scenario"><div class="scenario-title">Allow-лист (авто-одобрено)</div>';
  hooks.allow.forEach(a => {
    html += '<div class="step"><div class="step-num">&check;</div><div class="step-text"><code>' + esc(a) + '</code></div></div>';
  });
  html += '</div></div>';

  html += '<div class="group"><div class="group-title">Философия: надёжность правил</div>';
  html += '<table class="hooks-table"><tr><th>Уровень</th><th>Инструмент</th><th>Надёжность</th><th>Для чего</th></tr>';
  hooks.philosophy.forEach(p => {
    html += '<tr><td>' + esc(p.level) + '</td><td>' + esc(p.tool) + '</td><td>' + esc(p.reliability) + '</td><td>' + esc(p.use) + '</td></tr>';
  });
  html += '</table>';
  html += '<div class="note" style="padding-left:0;margin-top:.5rem">Чем критичнее правило — тем ниже в таблице оно должно жить.</div>';
  html += '</div>';

  setHTML(panel, html);
}

// === SCENARIOS TAB ===
function renderScenarios(stages) {
  const panel = document.getElementById('scenarios');
  let html = '';
  stages.forEach(stage => {
    html += '<div class="group"><div class="group-title">' + esc(stage.title) + '</div>';
    if (stage.pipeline) {
      html += '<div class="pipeline">' + esc(stage.pipeline) + '</div>';
    }
    stage.scenarios.forEach(sc => {
      html += '<div class="scenario"><div class="scenario-title">' + esc(sc.title) + '</div>';
      if (sc.pipeline) {
        html += '<div class="pipeline">' + esc(sc.pipeline) + '</div>';
      }
      var stepNum = 0;
      sc.steps.forEach(s => {
        if (s.type === 'note') {
          html += '<div class="note">' + esc(s.text) + '</div>';
        } else {
          stepNum++;
          var cmdCode = s.cmd ? '<code>' + esc(s.cmd) + '</code> — ' : '';
          var tag = s.tag === 'plugin' ? '<span class="tag plugin">plugin</span> ' : '';
          var req = s.required ? '' : ' <span class="step-opt">по ситуации</span>';
          html += '<div class="step"><div class="step-num">' + stepNum + '</div><div class="step-text">' + tag + cmdCode + esc(s.text) + req + '</div></div>';
        }
      });
      html += '</div>';
    });
    html += '</div>';
  });
  setHTML(panel, html);
}

// === SCRUM TAB ===
function renderScrum(ceremonies, roles) {
  const panel = document.getElementById('scrum');
  let html = '<div class="group"><div class="group-title">Scrum / Agile церемонии</div>';
  ceremonies.forEach(c => {
    html += '<div class="scenario"><div class="scenario-title">' + esc(c.title) + '</div>';
    c.steps.forEach((s, i) => {
      if (s.type === 'note') {
        html += '<div class="note">' + esc(s.text) + '</div>';
      } else {
        const cmdCode = s.cmd ? '<code>' + esc(s.cmd) + '</code> — ' : '';
        const tag = s.tag === 'plugin' ? '<span class="tag plugin">plugin</span> ' : '';
        html += '<div class="step"><div class="step-num">' + (i + 1) + '</div><div class="step-text">' + tag + cmdCode + esc(s.text) + '</div></div>';
      }
    });
    html += '</div>';
  });
  html += '</div>';

  html += '<div class="group"><div class="group-title">Роли в разработке продукта</div>';
  roles.forEach(r => {
    html += '<div class="role-card"><div class="role-title">' + esc(r.title) + '</div><div class="role-cmds">';
    r.commands.forEach(c => {
      html += '<code style="background:var(--surface2);padding:.1rem .4rem;border-radius:3px;font-size:.75rem;color:var(--accent)">' + esc(c) + '</code>';
    });
    html += '</div><div class="role-doc">Ключевые docs: ' + esc(r.docs) + '</div></div>';
  });
  html += '</div>';

  setHTML(panel, html);
}

// === CONSTRUCTOR TAB ===
let allCommands = [];

function renderConstructor(data) {
  const panel = document.getElementById('constructor');
  allCommands = [];
  data.groups.forEach(g => g.commands.forEach(c => allCommands.push(c)));

  let html = '<div class="constructor">';
  html += '<div class="field"><label>Команда</label><select id="con-cmd"><option value="">Выбрать...</option>';
  data.groups.forEach(g => {
    html += '<optgroup label="' + esc(g.title) + '">';
    g.commands.forEach(c => {
      const short = c.desc.length > 50 ? c.desc.substring(0, 50) + '...' : c.desc;
      html += '<option value="' + esc(c.name) + '">' + esc(c.name) + ' — ' + esc(short) + '</option>';
    });
    html += '</optgroup>';
  });
  html += '</select></div>';
  html += '<div id="con-args"></div>';
  html += '<div class="output-box"><span class="output-text" id="con-output">Выберите команду...</span>';
  html += '<button class="copy-btn" id="con-copy">Копировать</button></div>';
  html += '<div class="output-explain" id="con-explain" style="display:none"></div>';
  html += '</div>';

  html += '<div class="group" style="margin-top:1.5rem"><div class="group-title">Быстрое копирование</div><div class="quick-cmds" id="quick-cmds"></div></div>';

  setHTML(panel, html);

  document.getElementById('con-cmd').addEventListener('change', onCmdChange);
  document.getElementById('con-copy').addEventListener('click', copyResult);

  var quickCmds = ['/rb-standup', '/rb-status', '/rb-todo', '/rb-test', '/rb-docs',
    '/rb-audit', '/rb-reflect', '/rb-close-day', '/rb-query', '/rb-lint', '/rb-all', '/revise-claude-md', '/commit'];
  var qDiv = document.getElementById('quick-cmds');
  quickCmds.forEach(function(cmd) {
    var btn = document.createElement('button');
    btn.className = 'copy-btn';
    btn.textContent = cmd;
    btn.style.fontSize = '.75rem';
    btn.addEventListener('click', function() { copyText(btn, cmd); });
    qDiv.appendChild(btn);
  });
}

function onCmdChange() {
  var name = document.getElementById('con-cmd').value;
  var cmd = allCommands.find(function(c) { return c.name === name; });
  var argsDiv = document.getElementById('con-args');
  argsDiv.textContent = '';

  if (!cmd) {
    document.getElementById('con-output').textContent = 'Выберите команду...';
    document.getElementById('con-explain').style.display = 'none';
    return;
  }

  if (cmd.args) {
    cmd.args.forEach(function(a, i) {
      var div = document.createElement('div');
      div.className = 'field';
      var label = document.createElement('label');
      label.textContent = (a.hint || a.name) + (a.required ? ' *' : '');
      div.appendChild(label);

      if (a.type === 'select') {
        var sel = document.createElement('select');
        sel.dataset.argIndex = i;
        sel.className = 'con-arg';
        a.options.forEach(function(o) {
          var opt = document.createElement('option');
          opt.value = o.value;
          opt.textContent = o.label;
          sel.appendChild(opt);
        });
        sel.addEventListener('change', updateResult);
        div.appendChild(sel);
      } else {
        var inp = document.createElement('input');
        inp.type = 'text';
        inp.placeholder = a.placeholder || '';
        inp.dataset.argIndex = i;
        inp.className = 'con-arg';
        inp.addEventListener('input', updateResult);
        div.appendChild(inp);
      }
      argsDiv.appendChild(div);
    });
  }

  updateResult();
}

function updateResult() {
  var name = document.getElementById('con-cmd').value;
  var cmd = allCommands.find(function(c) { return c.name === name; });
  if (!cmd) return;

  var result = cmd.name;
  var explainParts = ['<strong>' + esc(cmd.name) + '</strong> — ' + esc(cmd.desc)];
  var argEls = document.querySelectorAll('.con-arg');

  argEls.forEach(function(el, i) {
    var val = el.value.trim();
    var arg = cmd.args[i];
    if (val && !val.startsWith('custom_')) {
      result += ' ' + val;
    }
    if (val && arg && arg.type === 'select') {
      var opt = arg.options.find(function(o) { return o.value === val; });
      if (opt) explainParts.push('<code>' + esc(val || 'default') + '</code> — ' + esc(opt.label));
    }
  });

  document.getElementById('con-output').textContent = result;
  var explainDiv = document.getElementById('con-explain');
  setHTML(explainDiv, explainParts.join('<br>'));
  explainDiv.style.display = '';
}

function copyResult() {
  var text = document.getElementById('con-output').textContent;
  if (!text || text === 'Выберите команду...') return;
  copyText(document.getElementById('con-copy'), text);
}

function copyText(btn, text) {
  navigator.clipboard.writeText(text).then(function() {
    var orig = btn.textContent;
    btn.textContent = 'Скопировано!';
    btn.classList.add('copied');
    setTimeout(function() { btn.textContent = orig; btn.classList.remove('copied'); }, 1200);
  });
}

function esc(s) {
  if (!s) return '';
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
