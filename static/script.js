const dropZone    = document.getElementById('dropZone');
const fileInput   = document.getElementById('fileInput');
const results     = document.getElementById('results');
const errorBox    = document.getElementById('errorBox');
const progressWrap = document.getElementById('progressWrap');

// ─── Upload events ─────────────────────────────────────────────────────────

dropZone.addEventListener('click', () => fileInput.click());

fileInput.addEventListener('change', () => {
    if (fileInput.files[0]) upload(fileInput.files[0]);
});

dropZone.addEventListener('dragover', e => {
    e.preventDefault();
    dropZone.classList.add('drag-over');
});

dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));

dropZone.addEventListener('drop', e => {
    e.preventDefault();
    dropZone.classList.remove('drag-over');
    const file = e.dataTransfer.files[0];
    if (file && file.name.toLowerCase().endsWith('.ipa')) {
        upload(file);
    } else {
        showError('Vui lòng chọn file có định dạng .ipa');
    }
});

// ─── Upload & scan ─────────────────────────────────────────────────────────

function upload(file) {
    results.style.display  = 'none';
    errorBox.style.display = 'none';
    progressWrap.style.display = 'block';
    setProgress(0, 'Đang tải lên...');

    document.getElementById('uploadIcon').textContent = '⏳';
    document.getElementById('uploadText').innerHTML =
        `<strong>${file.name}</strong><span>${formatSize(file.size)}</span>`;

    const fd = new FormData();
    fd.append('file', file);

    const xhr = new XMLHttpRequest();

    xhr.upload.addEventListener('progress', e => {
        if (e.lengthComputable) {
            const pct = Math.round(e.loaded / e.total * 80);
            setProgress(pct, `Đang tải lên... ${formatSize(e.loaded)} / ${formatSize(e.total)}`);
        }
    });

    xhr.addEventListener('load', () => {
        setProgress(95, 'Đang phân tích IPA...');
        try {
            const data = JSON.parse(xhr.responseText);
            setTimeout(() => {
                progressWrap.style.display = 'none';
                document.getElementById('uploadIcon').textContent = '✅';
                document.getElementById('uploadText').innerHTML =
                    `<strong>Tải lên thành công</strong><span>Kéo file khác để quét tiếp</span>`;
                if (data.error) {
                    showError(data.error);
                } else {
                    renderResults(data);
                    document.dispatchEvent(new Event('ipa-uploaded'));
                }
            }, 400);
        } catch {
            showError('Phản hồi server không hợp lệ');
            progressWrap.style.display = 'none';
        }
    });

    xhr.addEventListener('error', () => {
        showError('Không thể kết nối đến server. Kiểm tra lại Flask backend đang chạy.');
        progressWrap.style.display = 'none';
        document.getElementById('uploadIcon').textContent = '📦';
    });

    xhr.open('POST', '/api/scan');
    xhr.send(fd);
}

function setProgress(pct, label) {
    document.getElementById('progressFill').style.width = pct + '%';
    document.getElementById('progressLabel').textContent = label;
    document.getElementById('progressPct').textContent  = pct + '%';
}

// ─── Render results ────────────────────────────────────────────────────────

function renderResults(data) {
    const app   = data.app_info   || {};
    const prov  = data.provision;
    const certs = data.certificates || [];

    // Download Links
    const dlCard  = document.getElementById('cardDownload');
    const dlLinks = document.getElementById('downloadLinks');
    if (data.ipa_stored && data.download_url) {
        const shortUrl    = data.short_url    || data.install_page || '';
        const installUrl  = data.install_url  || '';
        const downloadUrl = data.download_url || '';
        dlLinks.innerHTML = `
            <div class="dl-short-box">
                <div class="dl-short-label">🔗 Link chia sẻ</div>
                <div class="dl-short-row">
                    <div class="dl-short-url">${shortUrl}</div>
                    <button class="dl-copy dl-copy-big" onclick="copyText('${shortUrl}', this)" title="Sao chép">📋 Copy</button>
                </div>
                <div class="dl-short-hint">Gửi link này cho người dùng — mở trên Safari để cài đặt</div>
            </div>
            <div class="dl-row" style="margin-top:12px">
                <a class="dl-btn dl-direct" href="${downloadUrl}" download>
                    <span class="dl-icon">⬇</span>
                    <span class="dl-label">Tải file IPA</span>
                </a>
                <button class="dl-copy" title="Sao chép link tải" onclick="copyText('${downloadUrl}', this)">📋</button>
            </div>
            <div class="dl-row">
                <a class="dl-btn dl-ota" href="${shortUrl}" target="_blank">
                    <span class="dl-icon">📲</span>
                    <span class="dl-label">Mở trang cài đặt</span>
                </a>
                <button class="dl-copy" title="Sao chép itms-services://" onclick="copyText('${installUrl}', this)" title="Copy itms://"> 🍎</button>
            </div>
            <p class="dl-note">OTA install chỉ hoạt động trên iPhone/iPad qua Safari và yêu cầu HTTPS khi deploy thực tế.</p>`;
        dlCard.style.display = 'block';
    } else {
        dlCard.style.display = 'none';
    }

    // App header
    if (data.icon_base64) {
        const img = document.getElementById('appIcon');
        img.src = 'data:image/png;base64,' + data.icon_base64;
        img.style.display = 'block';
        document.getElementById('appIconFallback').style.display = 'none';
    } else {
        document.getElementById('appIcon').style.display = 'none';
        document.getElementById('appIconFallback').style.display = 'flex';
    }

    document.getElementById('rAppName').textContent  = app.display_name || '—';
    document.getElementById('rBundleId').textContent = app.bundle_id    || '—';

    const tagsEl = document.getElementById('appTags');
    tagsEl.innerHTML = '';
    if (app.version) tagsEl.innerHTML  += `<span class="tag tag-version">v${app.version} (${app.build})</span>`;
    if ((app.platforms||[]).includes('iPhone')) tagsEl.innerHTML += `<span class="tag tag-ios">iPhone</span>`;
    if ((app.platforms||[]).includes('iPad'))   tagsEl.innerHTML += `<span class="tag tag-ipad">iPad</span>`;
    if (app.min_os) tagsEl.innerHTML += `<span class="tag tag-ios-ver">iOS ${app.min_os}+</span>`;

    const metaEl = document.getElementById('appMeta');
    metaEl.innerHTML = `
        <div class="meta-item"><strong>${data.file_size || '—'}</strong>Kích thước</div>
        <div class="meta-item"><strong>${app.min_os ? 'iOS ' + app.min_os : '—'}</strong>Yêu cầu</div>
    `;

    // App Info
    const infoRows = [
        ['Bundle ID',    app.bundle_id,    'mono'],
        ['Tên hiển thị', app.display_name, ''],
        ['Phiên bản',    app.version,      ''],
        ['Build number', app.build,        'mono'],
        ['iOS tối thiểu', app.min_os ? `iOS ${app.min_os}+` : '—', ''],
        ['Executable',   app.executable,   'mono'],
        ['Nền tảng',     (app.platforms||[]).join(', ') || '—', ''],
    ];
    document.getElementById('infoList').innerHTML = infoRows.map(([k, v, cls]) =>
        `<div class="info-row">
            <span class="info-key">${k}</span>
            <span class="info-val ${cls === 'mono' ? '' : ''}">${v || '—'}</span>
        </div>`
    ).join('');

    // Provision
    if (prov) {
        const typeBadge = document.getElementById('profileTypeBadge');
        const typeClass = {
            'Enterprise (In-House)': 'ptype-enterprise',
            'Ad Hoc':    'ptype-adhoc',
            'Development': 'ptype-dev',
            'App Store':  'ptype-appstore',
        }[prov.profile_type] || 'ptype-appstore';
        typeBadge.textContent  = prov.profile_type;
        typeBadge.className    = `profile-type-badge ${typeClass}`;

        const daysLeft  = prov.days_left;
        const isExpired = prov.is_expired;

        const daysClass = isExpired ? 'val-danger' : daysLeft < 30 ? 'val-warning' : 'val-good';
        const daysText  = isExpired ? '❌ Đã hết hạn' : `✅ Còn ${daysLeft} ngày`;

        const provRows = [
            ['Tên profile',   prov.name,       ''],
            ['UUID',          prov.uuid,        'mono'],
            ['Team Name',     prov.team_name,   ''],
            ['Team ID',       prov.team_id,     'mono'],
            ['App ID Name',   prov.app_id_name, ''],
            ['Ngày tạo',      prov.creation,    ''],
            ['Ngày hết hạn',  prov.expiry,      isExpired ? 'val-danger' : daysLeft < 30 ? 'val-warning' : ''],
            ['Trạng thái',    daysText,         daysClass],
            ['Số thiết bị',   prov.device_count > 0 ? `${prov.device_count} thiết bị` : 'Không giới hạn', ''],
        ];

        document.getElementById('provList').innerHTML = provRows.map(([k, v, cls]) =>
            `<div class="info-row">
                <span class="info-key">${k}</span>
                <span class="info-val ${cls}">${v || '—'}</span>
            </div>`
        ).join('');

        // Expiry bar
        if (daysLeft !== null && daysLeft !== undefined) {
            const sec = document.getElementById('expirySection');
            sec.style.display = 'block';
            const maxDays = 365;
            const pct     = Math.max(0, Math.min(100, (daysLeft / maxDays) * 100));
            const fill    = document.getElementById('expiryFill');
            fill.style.width = pct + '%';
            fill.className = 'expiry-fill ' + (isExpired ? 'fill-danger' : daysLeft < 30 ? 'fill-warning' : 'fill-good');
            document.getElementById('expiryDaysLabel').textContent = isExpired ? 'Đã hết hạn' : `${daysLeft} ngày`;
        }

        // Entitlements
        if ((prov.entitlements||[]).length > 0) {
            document.getElementById('entitlementsSection').style.display = 'block';
            document.getElementById('entitlementsList').innerHTML =
                prov.entitlements.map(e =>
                    `<span class="entitlement-tag">${e}</span>`
                ).join('');
        }

        // Devices
        if ((prov.devices||[]).length > 0) {
            document.getElementById('devicesSection').style.display = 'block';
            document.getElementById('deviceCountLabel').textContent = prov.total_devices;
            document.getElementById('devicesList').innerHTML =
                prov.devices.map(d => `<div class="device-item">${d}</div>`).join('') +
                (prov.total_devices > prov.devices.length
                    ? `<div class="device-item" style="color:var(--text3)">...và ${prov.total_devices - prov.devices.length} thiết bị khác</div>`
                    : '');
        }
    } else {
        document.getElementById('cardProvision').innerHTML = `
            <div class="card-header">
                <span class="card-icon">🔐</span>
                <h3>Provisioning Profile</h3>
            </div>
            <div style="text-align:center;padding:32px;color:var(--text2)">
                <div style="font-size:36px;margin-bottom:12px">⚠️</div>
                <p>Không tìm thấy embedded.mobileprovision</p>
                <p style="font-size:12px;margin-top:8px;color:var(--text3)">File này có thể là build AppStore distribution</p>
            </div>`;
    }

    // Certificates
    document.getElementById('certCount').textContent = certs.length;

    if (certs.length > 0) {
        const html = `<div class="certs-grid">${certs.map(renderCert).join('')}</div>`;
        document.getElementById('certsList').innerHTML = html;
    } else {
        document.getElementById('certsList').innerHTML = `
            <div style="text-align:center;padding:32px;color:var(--text2)">
                Không tìm thấy certificate trong provisioning profile
            </div>`;
    }

    // Raw JSON
    document.getElementById('rawJson').textContent = JSON.stringify(
        { ...data, icon_base64: data.icon_base64 ? '[base64 image data]' : null },
        null, 2
    );

    results.style.display = 'block';
    results.classList.add('fade-up');
    setTimeout(() => results.scrollIntoView({ behavior: 'smooth', block: 'start' }), 100);
}

function renderCert(cert) {
    if (cert.error) {
        return `<div class="cert-card">
            <div class="cert-name" style="color:var(--text2)">Parse Error</div>
            <div class="cert-org" style="color:var(--red)">${cert.error}</div>
        </div>`;
    }

    const isExpired = cert.is_expired;
    const daysLeft  = cert.days_left;
    const statusClass = isExpired ? 'cert-expired' : daysLeft < 30 ? 'cert-warning' : 'cert-valid';
    const statusLabel = isExpired ? '❌ Hết hạn'  : daysLeft < 30 ? `⚠️ ${daysLeft} ngày` : `✅ ${daysLeft} ngày`;
    const statusBadge = isExpired ? 'status-expired' : daysLeft < 30 ? 'status-warning' : 'status-valid';

    return `
    <div class="cert-card ${statusClass}">
        <div class="cert-header">
            <div>
                <div class="cert-name">${cert.common_name || '—'}</div>
                <div class="cert-org">${cert.org || cert.country || '—'}</div>
            </div>
            <span class="cert-status ${statusBadge}">${statusLabel}</span>
        </div>
        <div class="cert-details">
            <div class="cert-row"><span class="k">Ngày cấp</span>  <span class="v">${cert.not_before}</span></div>
            <div class="cert-row"><span class="k">Hết hạn</span>   <span class="v">${cert.not_after}</span></div>
            <div class="cert-row"><span class="k">Serial</span>    <span class="v">${cert.serial?.slice(0,16)}...</span></div>
        </div>
        ${cert.fingerprint ? `<div class="cert-fingerprint">SHA-256: ${cert.fingerprint.match(/.{1,2}/g).join(':').slice(0,64)}...</div>` : ''}
    </div>`;
}

// ─── Helpers ───────────────────────────────────────────────────────────────

function showError(msg) {
    errorBox.style.display = 'flex';
    document.getElementById('errorMsg').textContent = msg;
    errorBox.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

function copyText(text, btn) {
    navigator.clipboard.writeText(text).then(() => {
        const orig = btn.textContent;
        btn.textContent = '✅';
        setTimeout(() => { btn.textContent = orig; }, 1500);
    }).catch(() => {
        const ta = document.createElement('textarea');
        ta.value = text;
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
        const orig = btn.textContent;
        btn.textContent = '✅';
        setTimeout(() => { btn.textContent = orig; }, 1500);
    });
}

function formatSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    if (bytes < 1024 * 1024 * 1024) return (bytes / 1024 / 1024).toFixed(1) + ' MB';
    return (bytes / 1024 / 1024 / 1024).toFixed(2) + ' GB';
}
