const guildId = document.querySelector('.console').dataset.guildId;
const commands = window.VANTRIX_COMMANDS || [];
const select = document.getElementById('console-cmd-select');
const paramsBox = document.getElementById('console-params');
const resultBox = document.getElementById('console-result');
let mentionables = { users: [], roles: [], channels: [] };

select.innerHTML = commands.map(c => `<option value="${c.name}">/${c.name} — ${c.description}</option>`).join('');

async function loadMentionables() {
  try {
    const res = await fetch(`/api/guild/${guildId}/mentionables`, { credentials: 'same-origin' });
    if (res.ok) mentionables = await res.json();
  } catch (err) {
    console.error('Error loading mentionables', err);
  }
  renderParams();
}

function optionsFor(type) {
  if (type === 'user') return mentionables.users.map(u => `<option value="${u.id}">${u.name}</option>`);
  if (type === 'channel') return mentionables.channels.map(c => `<option value="${c.id}">#${c.name}</option>`);
  if (type === 'role') return mentionables.roles.map(r => `<option value="${r.id}">${r.name}</option>`);
  return [];
}

function renderParams() {
  const cmd = commands.find(c => c.name === select.value);
  if (!cmd) { paramsBox.innerHTML = ''; return; }
  paramsBox.innerHTML = cmd.params.map(p => {
    if (['user', 'channel', 'role'].includes(p.type)) {
      return `<div class="console-field"><label>${p.label}</label><select data-param="${p.name}">${optionsFor(p.type).join('')}</select></div>`;
    }
    const inputType = p.type === 'number' ? 'number' : 'text';
    return `<div class="console-field"><label>${p.label}</label><input data-param="${p.name}" type="${inputType}" /></div>`;
  }).join('');
}

select.addEventListener('change', renderParams);
loadMentionables();

async function runCommand() {
  const cmd = commands.find(c => c.name === select.value);
  const params = {};
  paramsBox.querySelectorAll('[data-param]').forEach(el => { params[el.dataset.param] = el.value; });
  resultBox.classList.remove('hidden', 'error');
  resultBox.textContent = 'Running...';
  try {
    const res = await fetch(`/api/guild/${guildId}/run-command`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: cmd.name, params }),
    });
    const data = await res.json();
    resultBox.textContent = data.ok ? data.result : 'Error: ' + data.error;
    resultBox.classList.toggle('error', !data.ok);
  } catch (err) {
    resultBox.textContent = 'Request failed: ' + err.message;
    resultBox.classList.add('error');
  }
}
