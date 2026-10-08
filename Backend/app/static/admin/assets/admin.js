const state = { accessToken: null, refreshToken: null };
const $ = (id) => document.getElementById(id);
const message = (text, error = false) => { $('message').textContent = text; $('message').classList.toggle('error', error); };

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}), 'Content-Type': 'application/json' };
  if (state.accessToken) headers.Authorization = `Bearer ${state.accessToken}`;
  const response = await fetch(path, { ...options, headers });
  const body = response.status === 204 ? null : await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof body.detail === 'string' ? body.detail : 'Check the supplied fields and try again.';
    if (response.status === 401 && state.accessToken) signOutLocally();
    throw new Error(detail);
  }
  return body;
}

async function loadUsers() {
  const users = await api('/api/v1/admin/users');
  $('accounts').innerHTML = users.map(user => `<div class="account"><span><strong>${escapeHtml(user.login_id)}</strong><br><span class="muted">${user.role} · ${user.is_active ? 'Active' : 'Disabled'}</span></span>${user.is_active && user.role !== 'ADMIN' ? `<button class="secondary" data-disable="${user.id}">Disable</button>` : ''}</div>`).join('') || '<p class="muted">No mobile accounts created yet.</p>';
  document.querySelectorAll('[data-disable]').forEach(button => button.addEventListener('click', async () => {
    if (!confirm('Disable this account?')) return;
    try { await api(`/api/v1/admin/users/${button.dataset.disable}/disable`, { method: 'POST' }); message('Account disabled.'); await loadUsers(); } catch (error) { message(error.message, true); }
  }));
}

function escapeHtml(value) { return value.replace(/[&<>'"]/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[character])); }

$('login-form').addEventListener('submit', async (event) => {
  event.preventDefault(); message('Signing in…');
  const data = Object.fromEntries(new FormData(event.target));
  try {
    const tokens = await api('/api/v1/auth/login', { method: 'POST', body: JSON.stringify(data) });
    state.accessToken = tokens.access_token; state.refreshToken = tokens.refresh_token;
    const user = await api('/api/v1/auth/me');
    if (user.role !== 'ADMIN') {
      await api('/api/v1/auth/logout', { method: 'POST', body: JSON.stringify({ refresh_token: state.refreshToken }) });
      signOutLocally();
      throw new Error('An administrator account is required.');
    }
    event.target.reset();
    $('login-card').classList.add('hidden'); $('console-card').classList.remove('hidden');
    message('Authenticated.'); await loadUsers();
  } catch (error) { signOutLocally(); message(error.message, true); }
});

$('create-form').addEventListener('submit', async (event) => {
  event.preventDefault(); message('Creating PostgreSQL account…');
  const data = Object.fromEntries(new FormData(event.target));
  try {
    await api('/api/v1/admin/users', { method: 'POST', body: JSON.stringify(data) });
    event.target.reset(); message('Account created and persisted.');
    try { await loadUsers(); } catch (_) { message('Account created; list refresh failed. Do not recreate it.'); }
  } catch (error) { message(error.message, true); }
});

function signOutLocally() {
  state.accessToken = null; state.refreshToken = null;
  $('login-form').reset(); $('create-form').reset(); $('accounts').replaceChildren();
  $('console-card').classList.add('hidden'); $('login-card').classList.remove('hidden');
}

$('logout').addEventListener('click', async () => {
  try { await api('/api/v1/auth/logout', { method: 'POST', body: JSON.stringify({ refresh_token: state.refreshToken }) }); } catch (_) {}
  signOutLocally(); message('Signed out.');
});
