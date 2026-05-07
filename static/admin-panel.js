// ─── State ────────────────────────────────────────────────────────────────────
let currentPage = 'dashboard';
let scansPage   = 1;
let editUserId  = null;

// ─── Init ─────────────────────────────────────────────────────────────────────
(async () => {
    const r = await api('GET', '/api/auth/me');
    if (!r.logged_in || !r.user.is_admin) { location.href = '/login'; return; }
    document.getElementById('adminName').textContent = r.user.username;
    loadPage('dashboard');
})();

// ─── Navigation ───────────────────────────────────────────────────────────────
function showPage(page, el) {
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    if (el) el.classList.add('active');
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.getElementById('page-' + page).classList.add('active');
    const titles = { dashboard:'Dashboard', scans:'Lịch sử quét', users:'Người dùng', settings:'Cài đặt', files:'Quản lý Files' };
    document.getElementById('pageTitle').textContent = titles[page] || page;
    currentPage = page;
    loadPage(page);
    if (window.innerWidth <= 900) document.getElementById('sidebar').classList.remove('open');
}

function loadPage(page) {
    if (page === 'dashboard') loadDashboard();
    if (page === 'scans')     { scansPage = 1; loadScans(); }
    if (page === 'users')     loadUsers();
    if (page === 'settings')  loadSettings();
    if (page === 'files')     loadFiles();
}

function toggleSidebar() {
    document.getElementById('sidebar').classList.toggle('open');
}

// ─── Dashboard ────────────────────────────────────────────────────────────────
async function loadDashboard() {
    const d = await api('GET', '/api/admin/stats');
    const statsConfig = [
        { key: 'total_scans',   label: 'Tổng lượt quét',    icon: '📦', trend: null },
        { key: 'today_scans',   label: 'Hôm nay',            icon: '📅', trend: null },
        { key: 'week_scans',    label: '7 ngày qua',         icon: '📈', trend: 'up' },
        { key: 'total_users',   label: 'Người dùng',         icon: '👥', trend: null },
        { key: 'expiring_soon', label: 'Sắp hết hạn (≤30d)', icon: '⚠️', trend: 'warn' },
        { key: 'expired',       label: 'Đã hết hạn',         icon: '❌', trend: 'red' },
    ];
    document.getElementById('statsGrid').innerHTML = statsConfig.map(cfg => {
        const trendHtml = cfg.trend ? `<span class="stat-trend trend-${cfg.trend}">${cfg.trend === 'up' ? '↑' : cfg.trend === 'warn' ? '⚠' : '!'}</span>` : '';
        return `<div class="stat-card">
            <div class="stat-card-top"><span class="stat-icon">${cfg.icon}</span>${trendHtml}</div>
            <div class="stat-num">${d[cfg.key] ?? 0}</div>
            <div class="stat-label">${cfg.label}</div>
        </div>`;
    }).join('');
    document.getElementById('recentBody').innerHTML = (d.recent_scans || []).map(s => `
        <tr>
            <td><div style="display:flex;align-items:center;gap:8px">
                ${iconCell(s.icon_base64)}
                <span style="font-weight:600">${esc(s.app_name)}</span>
            </div></td>
            <td><span style="font-family:var(--mono);font-size:11px;color:var(--text2)">${esc(s.bundle_id)}</span></td>
            <td>${profileBadge(s.profile_type)}</td>
            <td>${daysCell(s.days_left, s.expiry_date)}</td>
            <td style="color:var(--text2);font-size:12px">${esc(s.created_at)}</td>
            <td style="color:var(--text3);font-size:11px">${esc(s.ip_address)}</td>
        </tr>`).join('') || '<tr><td colspan="6" style="text-align:center;color:var(--text3);padding:32px">Chưa có lượt quét nào</td></tr>';
}

// ─── Scans ────────────────────────────────────────────────────────────────────
async function loadScans() {
    const q  = document.getElementById('scanSearch').value;
    const pt = document.getElementById('filterProfile').value;
    const d  = await api('GET', `/api/admin/scans?page=${scansPage}&q=${encodeURIComponent(q)}&profile_type=${encodeURIComponent(pt)}`);

    document.getElementById('scansBody').innerHTML = (d.scans || []).map(s => `
        <tr>
            <td><input type="checkbox" class="scan-check" value="${s.id}" onchange="updateBulkBtn()"></td>
            <td>${iconCell(s.icon_base64)}</td>
            <td><div style="font-weight:600;max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(s.app_name)}</div>
                <div style="font-size:11px;color:var(--text3)">${esc(s.filename)}</div></td>
            <td><span style="font-family:var(--mono);font-size:11px;color:var(--text2)">${esc(s.bundle_id)}</span></td>
            <td style="font-size:12px">${esc(s.version)}<span style="color:var(--text3)"> (${esc(s.build)})</span></td>
            <td>${profileBadge(s.profile_type)}</td>
            <td style="font-size:12px;color:var(--text2)">${esc(s.expiry_date)}</td>
            <td>${daysCell(s.days_left)}</td>
            <td style="font-size:12px;color:var(--text2);max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(s.team_name)}</td>
            <td style="font-size:12px;color:var(--text2)">${esc(s.created_at)}</td>
            <td style="font-size:12px;color:var(--text2)">${esc(s.user)}</td>
            <td style="display:flex;gap:4px">
                <button class="btn-icon" onclick="viewScan(${s.id})" title="Chi tiết">🔍</button>
                <button class="btn-icon" onclick="deleteScan(${s.id},this)" title="Xóa">🗑</button>
            </td>
        </tr>`).join('') || '<tr><td colspan="12" style="text-align:center;color:var(--text3);padding:32px">Không có kết quả</td></tr>';

    // Pagination
    const pg = document.getElementById('scansPagination');
    pg.innerHTML = '';
    if (d.pages > 1) {
        const info = document.createElement('span');
        info.className = 'page-info';
        info.textContent = `Tổng: ${d.total} kết quả`;
        for (let i = 1; i <= Math.min(d.pages, 10); i++) {
            const b = document.createElement('button');
            b.className = 'page-btn' + (i === d.page ? ' active' : '');
            b.textContent = i;
            b.onclick = () => { scansPage = i; loadScans(); };
            pg.appendChild(b);
        }
        pg.appendChild(info);
    }
}

function updateBulkBtn() {
    const checked = document.querySelectorAll('.scan-check:checked').length;
    document.getElementById('btnBulkDel').style.display = checked > 0 ? 'block' : 'none';
}

function toggleAllChecks(master) {
    document.querySelectorAll('.scan-check').forEach(c => c.checked = master.checked);
    updateBulkBtn();
}

async function deleteScan(id, btn) {
    if (!confirm('Xóa lượt quét này?')) return;
    btn.disabled = true;
    const r = await api('DELETE', `/api/admin/scans/${id}`);
    if (r.success) { showToast('Đã xóa', 'success'); loadScans(); }
    else showToast(r.error || 'Lỗi', 'error');
}

async function bulkDeleteScans() {
    const ids = [...document.querySelectorAll('.scan-check:checked')].map(c => +c.value);
    if (!ids.length || !confirm(`Xóa ${ids.length} mục đã chọn?`)) return;
    const r = await api('POST', '/api/admin/scans/bulk-delete', { ids });
    if (r.success) { showToast(`Đã xóa ${r.deleted} mục`, 'success'); loadScans(); }
    else showToast(r.error || 'Lỗi', 'error');
    document.getElementById('btnBulkDel').style.display = 'none';
}

async function viewScan(id) {
    const d = await api('GET', `/api/admin/scans/${id}`);
    const r = d.result || {};
    const ai = r.app_info || {};
    const pv = r.provision || {};
    const certs = r.certificates || [];

    const iconHtml = d.icon_base64
        ? `<img src="data:image/png;base64,${d.icon_base64}" style="width:64px;height:64px;border-radius:14px;object-fit:cover;display:block;margin:0 auto 16px">`
        : `<div style="width:64px;height:64px;border-radius:14px;background:var(--bg4);display:flex;align-items:center;justify-content:center;font-size:28px;margin:0 auto 16px">📱</div>`;

    document.getElementById('scanDetailBody').innerHTML = `
        ${iconHtml}
        <div class="detail-grid">
            <div class="detail-section">
                <h4>📋 App Info</h4>
                ${detailRows([
                    ['Tên',       ai.display_name],
                    ['Bundle ID', ai.bundle_id],
                    ['Phiên bản', `${ai.version} (${ai.build})`],
                    ['iOS min',   ai.min_os ? 'iOS ' + ai.min_os : '—'],
                    ['Nền tảng',  (ai.platforms||[]).join(', ')],
                    ['Kích thước',d.file_size],
                ])}
            </div>
            <div class="detail-section">
                <h4>🔐 Provisioning Profile</h4>
                ${detailRows([
                    ['Tên profile', pv.name],
                    ['UUID',        pv.uuid],
                    ['Team Name',   pv.team_name],
                    ['Team ID',     pv.team_id],
                    ['Loại',        pv.profile_type],
                    ['Tạo lúc',     pv.creation],
                    ['Hết hạn',     pv.expiry],
                    ['Còn lại',     pv.days_left != null ? (pv.is_expired ? '❌ Hết hạn' : `✅ ${pv.days_left} ngày`) : '—'],
                    ['Thiết bị',    pv.device_count > 0 ? pv.device_count + ' thiết bị' : 'Không giới hạn'],
                ])}
            </div>
        </div>
        ${certs.length > 0 ? `<div class="detail-section" style="margin-top:16px">
            <h4>🏆 Certificates (${certs.length})</h4>
            ${certs.map(c => `<div style="background:var(--bg2);border-radius:10px;padding:12px;margin-bottom:8px">
                ${detailRows([
                    ['Tên',       c.common_name],
                    ['Tổ chức',   c.org],
                    ['Cấp lúc',   c.not_before],
                    ['Hết hạn',   c.not_after],
                    ['Còn lại',   c.days_left != null ? (c.is_expired ? '❌ Hết hạn' : `✅ ${c.days_left} ngày`) : '—'],
                ])}
            </div>`).join('')}
        </div>` : ''}`;
    document.getElementById('scanModal').style.display = 'flex';
}

function closeScanModal() { document.getElementById('scanModal').style.display = 'none'; }

// ─── Users ────────────────────────────────────────────────────────────────────
async function loadUsers() {
    const d = await api('GET', '/api/admin/users');
    document.getElementById('usersBody').innerHTML = (d.users || []).map((u, i) => {
        const planLabel = u.plan === 'premium'
            ? `<span class="badge b-premium">⭐ Premium${u.plan_expires_at ? '<br><small>' + u.plan_expires_at + '</small>' : ''}</span>`
            : `<span class="badge b-free">🆓 Free</span>`;
        return `<tr>
            <td style="color:var(--text3)">${i+1}</td>
            <td><strong>${esc(u.username)}</strong></td>
            <td style="color:var(--text2);font-size:12px">${esc(u.email) || '—'}</td>
            <td><span class="badge b-${u.role}">${u.role}</span></td>
            <td>${planLabel}</td>
            <td><span class="badge ${u.is_active ? 'b-active' : 'b-inactive'}">${u.is_active ? 'Hoạt động' : 'Đã khóa'}</span></td>
            <td style="font-size:12px;color:var(--text2)">${esc(u.last_login)}</td>
            <td style="font-size:12px">${u.scan_count}</td>
            <td style="font-size:12px;color:var(--text2)">${esc(u.created_at)}</td>
            <td style="display:flex;gap:4px">
                <button class="btn-icon" onclick="editUser(${u.id})" title="Sửa">✏️</button>
                <button class="btn-icon" onclick="managePlan(${u.id},'${u.plan}')" title="${u.plan === 'premium' ? 'Hạ về Free' : 'Nâng cấp Premium'}">${u.plan === 'premium' ? '⬇️' : '⭐'}</button>
                <button class="btn-icon" onclick="toggleUser(${u.id},${u.is_active})" title="${u.is_active ? 'Khóa' : 'Mở khóa'}">${u.is_active ? '🔒' : '🔓'}</button>
                <button class="btn-icon" onclick="deleteUser(${u.id},this)" title="Xóa">🗑</button>
            </td>
        </tr>`;
    }).join('') || '<tr><td colspan="10" style="text-align:center;color:var(--text3);padding:32px">Không có người dùng</td></tr>';
}

function openUserModal(id) {
    editUserId = id || null;
    document.getElementById('userModalTitle').textContent = id ? 'Sửa người dùng' : 'Thêm người dùng';
    document.getElementById('um-username').value  = '';
    document.getElementById('um-email').value     = '';
    document.getElementById('um-password').value  = '';
    document.getElementById('um-role').value      = 'user';
    document.getElementById('umError').style.display = 'none';
    document.getElementById('um-username').disabled = false;
    document.getElementById('userModal').style.display = 'flex';
}

async function editUser(id) {
    const d = await api('GET', '/api/admin/users');
    const u = (d.users || []).find(x => x.id === id);
    if (!u) return;
    editUserId = id;
    document.getElementById('userModalTitle').textContent = 'Sửa người dùng';
    document.getElementById('um-username').value  = u.username;
    document.getElementById('um-email').value     = u.email;
    document.getElementById('um-password').value  = '';
    document.getElementById('um-role').value      = u.role;
    document.getElementById('um-username').disabled = true;
    document.getElementById('umError').style.display = 'none';
    document.getElementById('userModal').style.display = 'flex';
}

function closeUserModal() { document.getElementById('userModal').style.display = 'none'; }

async function saveUser() {
    const err = document.getElementById('umError');
    err.style.display = 'none';
    const body = {
        username: document.getElementById('um-username').value.trim(),
        email:    document.getElementById('um-email').value.trim(),
        password: document.getElementById('um-password').value,
        role:     document.getElementById('um-role').value,
    };
    let r;
    if (editUserId) {
        r = await api('PUT', `/api/admin/users/${editUserId}`, body);
    } else {
        r = await api('POST', '/api/admin/users', body);
    }
    if (r.success) {
        showToast(editUserId ? 'Đã cập nhật' : 'Đã tạo người dùng', 'success');
        closeUserModal();
        loadUsers();
    } else {
        err.textContent = r.error || 'Lỗi';
        err.style.display = 'block';
    }
}

async function toggleUser(id, isActive) {
    const r = await api('PUT', `/api/admin/users/${id}`, { is_active: !isActive });
    if (r.success) { showToast(isActive ? 'Đã khóa tài khoản' : 'Đã mở khóa', 'success'); loadUsers(); }
    else showToast(r.error || 'Lỗi', 'error');
}

async function deleteUser(id, btn) {
    if (!confirm('Xóa người dùng này?')) return;
    btn.disabled = true;
    const r = await api('DELETE', `/api/admin/users/${id}`);
    if (r.success) { showToast('Đã xóa người dùng', 'success'); loadUsers(); }
    else { showToast(r.error || 'Lỗi', 'error'); btn.disabled = false; }
}

async function managePlan(id, currentPlan) {
    if (currentPlan === 'premium') {
        if (!confirm('Hạ tài khoản này về gói Free?')) return;
        const r = await api('POST', `/api/admin/users/${id}/downgrade`);
        if (r.success) { showToast('Đã hạ về Free', 'success'); loadUsers(); }
        else showToast(r.error || 'Lỗi', 'error');
    } else {
        const months = prompt('Nâng cấp Premium mấy tháng?\n(nhập 1, 3, hoặc 12)', '3');
        if (!months) return;
        if (!['1','3','12'].includes(months.trim())) { showToast('Chỉ nhập 1, 3 hoặc 12', 'error'); return; }
        const r = await api('POST', `/api/admin/users/${id}/upgrade`, { months: +months });
        if (r.success) { showToast(`Đã nâng cấp Premium ${months} tháng`, 'success'); loadUsers(); }
        else showToast(r.error || 'Lỗi', 'error');
    }
}

// ─── Files ────────────────────────────────────────────────────────────────────
async function loadFiles() {
    const d = await api('GET', '/api/admin/files');
    document.getElementById('filesTotalSize').textContent =
        `Tổng: ${d.count || 0} files · ${d.total_size || '0 B'}`;
    document.getElementById('filesBody').innerHTML = (d.files || []).map(s => `
        <tr>
            <td>${iconCell(s.icon_base64)}</td>
            <td><div style="font-weight:600;max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(s.app_name)}</div>
                <div style="font-size:11px;color:var(--text3)">${esc(s.filename)}</div></td>
            <td><span style="font-family:var(--mono);font-size:11px">${esc(s.bundle_id)}</span></td>
            <td style="font-size:12px">${esc(s.version)}</td>
            <td style="font-size:12px">${esc(s.disk_size)}</td>
            <td style="font-size:12px">⬇ ${s.download_count || 0}</td>
            <td style="font-size:12px;color:var(--text2)">${esc(s.created_at)}</td>
            <td style="font-size:12px">${esc(s.user)}</td>
            <td><button class="btn-icon" onclick="deleteFile('${esc(s.file_uuid)}',this)" title="Xóa file">🗑</button></td>
        </tr>`).join('') || '<tr><td colspan="9" style="text-align:center;color:var(--text3);padding:32px">Không có files đang lưu</td></tr>';
}

async function deleteFile(fileUuid, btn) {
    if (!confirm('Xóa file IPA này? Link tải sẽ không còn hoạt động.')) return;
    btn.disabled = true;
    const r = await api('DELETE', `/api/admin/files/${fileUuid}`);
    if (r.success) { showToast('Đã xóa file', 'success'); loadFiles(); }
    else { showToast(r.error || 'Lỗi', 'error'); btn.disabled = false; }
}

// ─── Settings ─────────────────────────────────────────────────────────────────
async function loadSettings() {
    const d = await api('GET', '/api/admin/settings');
    document.getElementById('s-site_name').value     = d.site_name     || '';
    document.getElementById('s-footer_text').value   = d.footer_text   || '';
    document.getElementById('s-max_upload_mb').value = d.max_upload_mb || '500';
    document.getElementById('s-require_login').checked  = d.require_login  === 'true';
    document.getElementById('s-allow_register').checked = d.allow_register !== 'false';
    document.getElementById('s-bank_name').value        = d.bank_name        || '';
    document.getElementById('s-bank_account').value     = d.bank_account     || '';
    document.getElementById('s-bank_owner').value       = d.bank_owner       || '';
    document.getElementById('s-bank_stc_secret').value  = d.bank_stc_secret  || '';
    document.getElementById('s-contact_zalo').value     = d.contact_zalo     || '';
    document.getElementById('s-contact_telegram').value = d.contact_telegram || '';
    document.getElementById('s-contact_email').value    = d.contact_email    || '';
    document.getElementById('s-contact_website').value  = d.contact_website  || '';
}

async function saveSettings() {
    const d = {
        site_name:        document.getElementById('s-site_name').value.trim(),
        footer_text:      document.getElementById('s-footer_text').value.trim(),
        max_upload_mb:    document.getElementById('s-max_upload_mb').value,
        require_login:    document.getElementById('s-require_login').checked  ? 'true' : 'false',
        allow_register:   document.getElementById('s-allow_register').checked ? 'true' : 'false',
        bank_name:        document.getElementById('s-bank_name').value.trim(),
        bank_account:     document.getElementById('s-bank_account').value.trim(),
        bank_owner:       document.getElementById('s-bank_owner').value.trim(),
        bank_stc_secret:  document.getElementById('s-bank_stc_secret').value,
        contact_zalo:     document.getElementById('s-contact_zalo').value.trim(),
        contact_telegram: document.getElementById('s-contact_telegram').value.trim(),
        contact_email:    document.getElementById('s-contact_email').value.trim(),
        contact_website:  document.getElementById('s-contact_website').value.trim(),
    };
    const r = await api('POST', '/api/admin/settings', d);
    if (r.success) showToast('Đã lưu cài đặt', 'success');
    else showToast(r.error || 'Lỗi', 'error');
}

async function changePassword() {
    const msg     = document.getElementById('pwMsg');
    const oldPw   = document.getElementById('oldPw').value;
    const newPw   = document.getElementById('newPw').value;
    const confirm = document.getElementById('confirmPw').value;
    msg.style.display = 'none';
    if (!oldPw || !newPw) { showMsg(msg, 'Vui lòng điền đầy đủ', 'error'); return; }
    if (newPw.length < 6)  { showMsg(msg, 'Mật khẩu mới phải có ít nhất 6 ký tự', 'error'); return; }
    if (newPw !== confirm)  { showMsg(msg, 'Xác nhận mật khẩu không khớp', 'error'); return; }
    // Verify old password via login
    const check = await api('POST', '/api/auth/login', {
        username: document.getElementById('adminName').textContent, password: oldPw
    });
    if (!check.success) { showMsg(msg, 'Mật khẩu hiện tại không đúng', 'error'); return; }
    // Update via users API (current user)
    const me = await api('GET', '/api/auth/me');
    const r  = await api('PUT', `/api/admin/users/${me.user.id}`, { password: newPw });
    if (r.success) {
        showMsg(msg, '✅ Đổi mật khẩu thành công', 'success');
        document.getElementById('oldPw').value = document.getElementById('newPw').value = document.getElementById('confirmPw').value = '';
    } else showMsg(msg, r.error || 'Lỗi', 'error');
}

// ─── Auth ─────────────────────────────────────────────────────────────────────
async function doLogout() {
    await api('POST', '/api/auth/logout');
    location.href = '/login';
}

// ─── Helpers ──────────────────────────────────────────────────────────────────
async function api(method, url, body) {
    try {
        const opts = { method, credentials: 'include', headers: {} };
        if (body) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
        const r = await fetch(url, opts);
        return await r.json();
    } catch { return { error: 'Lỗi kết nối server' }; }
}

function esc(s) {
    if (s == null) return '—';
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function profileBadge(pt) {
    const map = {
        'Enterprise (In-House)': 'b-enterprise',
        'Ad Hoc':   'b-adhoc',
        'Development': 'b-dev',
        'App Store': 'b-appstore',
    };
    return `<span class="badge ${map[pt] || 'b-unknown'}">${esc(pt) || '—'}</span>`;
}

function daysCell(days, dateStr) {
    if (days == null) return `<span style="color:var(--text3)">—</span>`;
    if (days < 0)     return `<span class="days-danger">❌ Hết hạn</span>`;
    if (days <= 30)   return `<span class="days-warn">⚠ ${days}d</span>`;
    return `<span class="days-ok">✅ ${days}d</span>`;
}

function iconCell(b64) {
    if (!b64 || b64 === '[omitted]')
        return `<div class="icon-sm">📱</div>`;
    return `<div class="icon-sm"><img src="data:image/png;base64,${b64}" onerror="this.parentNode.innerHTML='📱'"></div>`;
}

function detailRows(rows) {
    return rows.map(([k, v]) =>
        `<div class="detail-row"><span class="k">${k}</span><span class="v">${esc(v)}</span></div>`
    ).join('');
}

function showMsg(el, text, type) {
    el.textContent = (type === 'error' ? '⚠️ ' : '✅ ') + text;
    el.className = `msg-box msg-${type}`;
    el.style.display = 'block';
}

function showToast(msg, type = 'success') {
    const t = document.getElementById('toast');
    t.textContent = (type === 'success' ? '✅ ' : '⚠️ ') + msg;
    t.className = `toast toast-${type}`;
    t.style.display = 'block';
    clearTimeout(window._toastTimer);
    window._toastTimer = setTimeout(() => { t.style.display = 'none'; }, 2800);
}

function togglePwId(id, btn) {
    const inp = document.getElementById(id);
    inp.type = inp.type === 'password' ? 'text' : 'password';
    btn.textContent = inp.type === 'password' ? '👁' : '🙈';
}
