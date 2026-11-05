/**
 * Analytics WebApp for Booking Bot
 */

// Telegram WebApp instance
const tg = window.Telegram?.WebApp;

// State
let currentPeriod = 'today';
let currentBusinessId = null;
let businesses = [];
let analyticsData = null;

// Charts instances
let revenueChart = null;
let statusChart = null;
let hoursChart = null;
let daysChart = null;

// Schedule state
let scheduleWeekOffset = 0;
let scheduleData = null;
let scheduleEmployees = [];
let selectedEmployeeId = null;
let clientColorMap = {}; // Map client_id to color class

// Initialize
document.addEventListener('DOMContentLoaded', async () => {
    // Init Telegram WebApp
    if (tg) {
        tg.ready();
        tg.expand();
        document.body.classList.add('tg-theme');

        // Apply Telegram theme variables
        if (tg.themeParams) {
            const root = document.documentElement;
            const params = tg.themeParams;

            if (params.bg_color) {
                root.style.setProperty('--tg-theme-bg-color', params.bg_color);
            }
            if (params.text_color) {
                root.style.setProperty('--tg-theme-text-color', params.text_color);
            }
            if (params.hint_color) {
                root.style.setProperty('--tg-theme-hint-color', params.hint_color);
            }
            if (params.button_color) {
                root.style.setProperty('--tg-theme-button-color', params.button_color);
            }
            if (params.button_text_color) {
                root.style.setProperty('--tg-theme-button-text-color', params.button_text_color);
            }
            if (params.secondary_bg_color) {
                root.style.setProperty('--tg-theme-secondary-bg-color', params.secondary_bg_color);
            }

            // Set background color for body
            document.body.style.backgroundColor = params.bg_color || '#f8fafc';
        }
    }

    // Setup event listeners
    setupEventListeners();

    // Load initial data
    await loadBusinesses();
});

function setupEventListeners() {
    // Period buttons
    document.querySelectorAll('.period-btn').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            document.querySelectorAll('.period-btn').forEach(b => b.classList.remove('active'));
            e.target.classList.add('active');
            currentPeriod = e.target.dataset.period;
            await loadAnalytics();
        });
    });

    // Business selector
    document.getElementById('businessSelect').addEventListener('change', async (e) => {
        currentBusinessId = e.target.value ? parseInt(e.target.value) : null;
        if (currentBusinessId) {
            await loadAnalytics();
        }
    });
}

async function apiRequest(endpoint, method = 'GET', body = null) {
    const headers = {
        'Content-Type': 'application/json'
    };

    // Add Telegram init data for authentication
    if (tg?.initData) {
        headers['X-Telegram-Init-Data'] = tg.initData;
    }
    // Без Telegram — cookie отправляется автоматически

    const options = { method, headers, credentials: 'same-origin' };
    if (body) {
        options.body = JSON.stringify(body);
    }

    const response = await fetch(endpoint, options);
    if (!response.ok) {
        // Если 401 и нет Telegram — перенаправляем на логин
        if (response.status === 401 && !tg?.initData) {
            window.location.href = '/login?to=/analytics';
            return null;
        }
        throw new Error(`API error: ${response.status}`);
    }
    return response.json();
}

async function loadBusinesses() {
    try {
        const data = await apiRequest('/api/analytics/businesses');
        businesses = data.businesses || [];

        const select = document.getElementById('businessSelect');
        select.innerHTML = '<option value="">Выберите бизнес</option>';

        businesses.forEach(b => {
            const option = document.createElement('option');
            option.value = b.id;
            option.textContent = b.name;
            select.appendChild(option);
        });

        // Auto-select first business
        if (businesses.length > 0) {
            currentBusinessId = businesses[0].id;
            select.value = currentBusinessId;
            await loadAnalytics();
        }

        document.getElementById('loading').style.display = 'none';
        document.getElementById('mainContent').style.display = 'block';
    } catch (error) {
        console.error('Error loading businesses:', error);
        showError('Ошибка загрузки данных');
    }
}

async function loadAnalytics() {
    if (!currentBusinessId) return;

    try {
        const data = await apiRequest(`/api/analytics/data?business_id=${currentBusinessId}&period=${currentPeriod}`);
        analyticsData = data;
        renderAnalytics(data);

        // Also load schedule
        await loadSchedule();
    } catch (error) {
        console.error('Error loading analytics:', error);
        showError('Ошибка загрузки аналитики');
    }
}

function renderAnalytics(data) {
    // Summary cards
    document.getElementById('totalRevenue').textContent = formatMoney(data.summary.revenue);
    document.getElementById('totalBookings').textContent = data.summary.bookings_count;
    document.getElementById('uniqueClients').textContent = data.summary.unique_clients;
    document.getElementById('completionRate').textContent = `${data.summary.completion_rate}%`;
    document.getElementById('cancelRate').textContent = `Отмен: ${data.summary.cancellation_rate}%`;

    // Changes
    renderChange('revenueChange', data.summary.revenue_change);
    renderChange('bookingsChange', data.summary.bookings_change);
    renderChange('clientsChange', data.summary.clients_change);

    // Charts
    renderRevenueChart(data.revenue_by_date);
    renderStatusChart(data.bookings_by_status);
    renderHoursChart(data.bookings_by_hour);
    renderDaysChart(data.bookings_by_day);

    // Top services
    renderTopServices(data.top_services);

    // Masters stats
    renderMastersStats(data.masters_stats);

    // Client analysis
    document.getElementById('newClients').textContent = data.clients.new_clients;
    document.getElementById('returningClients').textContent = data.clients.returning_clients;
    document.getElementById('avgCheck').textContent = formatMoney(data.clients.avg_check);
    document.getElementById('bookingsPerClient').textContent = data.clients.bookings_per_client.toFixed(1);

    // Subscriptions
    document.getElementById('activeSubscriptions').textContent = data.subscriptions.active_count;
    document.getElementById('soldSubscriptions').textContent = data.subscriptions.sold_count;
    document.getElementById('subscriptionsRevenue').textContent = formatMoney(data.subscriptions.revenue);

    // Prepayments
    document.getElementById('prepaymentBookings').textContent = data.prepayments.bookings_count;
    document.getElementById('prepaymentTotal').textContent = formatMoney(data.prepayments.total_amount);
    document.getElementById('prepaymentConfirmed').textContent = formatMoney(data.prepayments.confirmed_amount);
}

function renderChange(elementId, change) {
    const el = document.getElementById(elementId);
    if (change > 0) {
        el.textContent = `+${change}%`;
        el.className = 'card-change positive';
    } else if (change < 0) {
        el.textContent = `${change}%`;
        el.className = 'card-change negative';
    } else {
        el.textContent = '0%';
        el.className = 'card-change';
    }
}

function getChartColors() {
    // Modern color palette
    return {
        primary: '#6366f1',
        primaryLight: 'rgba(99, 102, 241, 0.1)',
        text: '#64748b',
        grid: 'rgba(148, 163, 184, 0.1)'
    };
}

function renderRevenueChart(data) {
    const ctx = document.getElementById('revenueChart').getContext('2d');
    const colors = getChartColors();

    if (revenueChart) {
        revenueChart.destroy();
    }

    const labels = data.map(d => d.label);
    const values = data.map(d => d.value);

    revenueChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [{
                label: 'Выручка',
                data: values,
                borderColor: colors.primary,
                backgroundColor: colors.primaryLight,
                fill: true,
                tension: 0.4,
                pointRadius: 4,
                pointHoverRadius: 6,
                pointBackgroundColor: colors.primary
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: '#1e293b',
                    titleColor: '#f1f5f9',
                    bodyColor: '#f1f5f9',
                    padding: 12,
                    cornerRadius: 8,
                    callbacks: {
                        label: (context) => formatMoney(context.raw)
                    }
                }
            },
            scales: {
                x: {
                    grid: { display: false },
                    ticks: {
                        color: colors.text,
                        maxRotation: 45,
                        font: { size: 11 }
                    }
                },
                y: {
                    beginAtZero: true,
                    grid: { color: colors.grid },
                    ticks: {
                        color: colors.text,
                        font: { size: 11 },
                        callback: (value) => formatShortMoney(value)
                    }
                }
            }
        }
    });
}

function renderStatusChart(data) {
    const ctx = document.getElementById('statusChart').getContext('2d');

    if (statusChart) {
        statusChart.destroy();
    }

    const statusLabels = {
        'completed': 'Завершено',
        'confirmed': 'Подтверждено',
        'pending': 'Ожидает',
        'cancelled': 'Отменено'
    };

    const statusColors = {
        'completed': '#10b981',
        'confirmed': '#6366f1',
        'pending': '#f59e0b',
        'cancelled': '#ef4444'
    };

    const labels = Object.keys(data).map(k => statusLabels[k] || k);
    const values = Object.values(data);
    const chartColors = Object.keys(data).map(k => statusColors[k] || '#94a3b8');

    statusChart = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: labels,
            datasets: [{
                data: values,
                backgroundColor: chartColors,
                borderWidth: 0,
                borderRadius: 4
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: 'bottom',
                    labels: {
                        padding: 16,
                        usePointStyle: true,
                        pointStyle: 'circle',
                        color: '#64748b',
                        font: { size: 12, weight: '500' }
                    }
                },
                tooltip: {
                    backgroundColor: '#1e293b',
                    titleColor: '#f1f5f9',
                    bodyColor: '#f1f5f9',
                    padding: 12,
                    cornerRadius: 8
                }
            },
            cutout: '65%'
        }
    });
}

function renderHoursChart(data) {
    const ctx = document.getElementById('hoursChart').getContext('2d');
    const colors = getChartColors();

    if (hoursChart) {
        hoursChart.destroy();
    }

    const labels = data.map(d => `${d.hour}:00`);
    const values = data.map(d => d.count);

    hoursChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [{
                label: 'Записей',
                data: values,
                backgroundColor: colors.primary,
                borderRadius: 6,
                borderSkipped: false
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: '#1e293b',
                    titleColor: '#f1f5f9',
                    bodyColor: '#f1f5f9',
                    padding: 12,
                    cornerRadius: 8
                }
            },
            scales: {
                x: {
                    grid: { display: false },
                    ticks: {
                        color: colors.text,
                        font: { size: 10 }
                    }
                },
                y: {
                    beginAtZero: true,
                    grid: { color: colors.grid },
                    ticks: {
                        color: colors.text,
                        stepSize: 1,
                        font: { size: 11 }
                    }
                }
            }
        }
    });
}

function renderDaysChart(data) {
    const ctx = document.getElementById('daysChart').getContext('2d');
    const colors = getChartColors();

    if (daysChart) {
        daysChart.destroy();
    }

    const dayNames = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
    const labels = data.map(d => dayNames[d.day]);
    const values = data.map(d => d.count);

    daysChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [{
                label: 'Записей',
                data: values,
                backgroundColor: data.map((d, i) =>
                    i >= 5 ? '#f59e0b' : colors.primary
                ),
                borderRadius: 6,
                borderSkipped: false
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: '#1e293b',
                    titleColor: '#f1f5f9',
                    bodyColor: '#f1f5f9',
                    padding: 12,
                    cornerRadius: 8
                }
            },
            scales: {
                x: {
                    grid: { display: false },
                    ticks: {
                        color: colors.text,
                        font: { size: 12, weight: '500' }
                    }
                },
                y: {
                    beginAtZero: true,
                    grid: { color: colors.grid },
                    ticks: {
                        color: colors.text,
                        stepSize: 1,
                        font: { size: 11 }
                    }
                }
            }
        }
    });
}

function renderTopServices(services) {
    const container = document.getElementById('topServicesList');

    if (!services || services.length === 0) {
        container.innerHTML = '<div class="empty-state"><div class="empty-state-icon">📋</div><p>Нет данных об услугах</p></div>';
        return;
    }

    const maxCount = Math.max(...services.map(s => s.count));

    container.innerHTML = services.map((service, index) => {
        const rankClass = index === 0 ? 'gold' : (index === 1 ? 'silver' : (index === 2 ? 'bronze' : ''));
        const progress = (service.count / maxCount) * 100;

        return `
            <div class="list-item">
                <div class="list-item-rank ${rankClass}">${index + 1}</div>
                <div class="list-item-info">
                    <div class="list-item-name">${escapeHtml(service.name)}</div>
                    <div class="list-item-details">${service.count} записей</div>
                    <div class="progress-bar">
                        <div class="progress-bar-fill" style="width: ${progress}%"></div>
                    </div>
                </div>
                <div class="list-item-stats">
                    <div class="list-item-value">${formatMoney(service.revenue)}</div>
                    <div class="list-item-sublabel">выручка</div>
                </div>
            </div>
        `;
    }).join('');
}

function renderMastersStats(masters) {
    const container = document.getElementById('mastersStatsList');

    if (!masters || masters.length === 0) {
        container.innerHTML = '<div class="empty-state"><div class="empty-state-icon">👨‍🔧</div><p>Нет данных о мастерах</p></div>';
        return;
    }

    const maxBookings = Math.max(...masters.map(m => m.bookings_count));

    container.innerHTML = masters.map((master, index) => {
        const progress = (master.bookings_count / maxBookings) * 100;

        return `
            <div class="list-item">
                <div class="list-item-info">
                    <div class="list-item-name">${escapeHtml(master.name)}</div>
                    <div class="list-item-details">${master.specialization || 'Мастер'}</div>
                    <div class="progress-bar">
                        <div class="progress-bar-fill" style="width: ${progress}%"></div>
                    </div>
                </div>
                <div class="list-item-stats">
                    <div class="list-item-value">${master.bookings_count}</div>
                    <div class="list-item-sublabel">записей | ${formatMoney(master.revenue)}</div>
                </div>
            </div>
        `;
    }).join('');
}

// Utility functions
function formatMoney(amount) {
    if (amount === null || amount === undefined) return '0 ₽';
    return new Intl.NumberFormat('ru-RU').format(amount) + ' ₽';
}

function formatShortMoney(amount) {
    if (amount >= 1000000) {
        return (amount / 1000000).toFixed(1) + 'М';
    }
    if (amount >= 1000) {
        return (amount / 1000).toFixed(0) + 'К';
    }
    return amount.toString();
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function showError(message) {
    document.getElementById('loading').innerHTML = `
        <div class="error-state">
            <div style="font-size: 48px; margin-bottom: 12px;">❌</div>
            <p>${message}</p>
        </div>
    `;
}

// ========== Schedule Functions ==========

async function loadSchedule() {
    if (!currentBusinessId) return;

    try {
        // Get week dates
        const today = new Date();
        const startOfWeek = new Date(today);
        startOfWeek.setDate(today.getDate() - today.getDay() + 1 + (scheduleWeekOffset * 7)); // Monday

        const startDate = formatDateISO(startOfWeek);
        const endDate = formatDateISO(new Date(startOfWeek.getTime() + 6 * 24 * 60 * 60 * 1000));

        const data = await apiRequest(`/api/analytics/schedule?business_id=${currentBusinessId}&start_date=${startDate}&end_date=${endDate}`);
        scheduleData = data;
        scheduleEmployees = data.employees || [];

        // Auto-select first employee/room
        if (scheduleEmployees.length > 0 && !selectedEmployeeId) {
            selectedEmployeeId = scheduleEmployees[0].id;
        }

        renderScheduleTabs();
        renderScheduleTable(startOfWeek);
        updateWeekLabel(startOfWeek);

        // Also render detailed bookings list
        renderDetailedBookings(data.bookings || []);
    } catch (error) {
        console.error('Error loading schedule:', error);
    }
}

function scheduleNavigate(direction) {
    scheduleWeekOffset += direction;
    loadSchedule();
    if (tg) tg.HapticFeedback?.selectionChanged();
}

function updateWeekLabel(startOfWeek) {
    const endOfWeek = new Date(startOfWeek.getTime() + 6 * 24 * 60 * 60 * 1000);
    const months = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];

    const startStr = `${startOfWeek.getDate()} ${months[startOfWeek.getMonth()]}`;
    const endStr = `${endOfWeek.getDate()} ${months[endOfWeek.getMonth()]}`;

    document.getElementById('scheduleWeekLabel').textContent = `${startStr} - ${endStr}`;
}

function renderScheduleTabs() {
    const container = document.getElementById('scheduleTabs');

    if (scheduleEmployees.length === 0) {
        container.innerHTML = '<div class="schedule-tab active"><span class="tab-icon">📅</span>Все записи</div>';
        return;
    }

    container.innerHTML = scheduleEmployees.map(emp => {
        const isActive = emp.id === selectedEmployeeId;
        const icon = emp.is_venue ? '🚪' : '👤';
        return `
            <button class="schedule-tab ${isActive ? 'active' : ''}" onclick="selectScheduleTab(${emp.id})">
                <span class="tab-icon">${icon}</span>${escapeHtml(emp.name)}
            </button>
        `;
    }).join('');
}

function selectScheduleTab(employeeId) {
    selectedEmployeeId = employeeId;
    renderScheduleTabs();

    // Re-render table with new filter
    const today = new Date();
    const startOfWeek = new Date(today);
    startOfWeek.setDate(today.getDate() - today.getDay() + 1 + (scheduleWeekOffset * 7));
    renderScheduleTable(startOfWeek);

    if (tg) tg.HapticFeedback?.selectionChanged();
}

function renderScheduleTable(startOfWeek) {
    const thead = document.getElementById('scheduleHead');
    const tbody = document.getElementById('scheduleBody');

    // Get business working hours (default 9-22)
    const startHour = scheduleData?.working_hours?.start || 9;
    const endHour = scheduleData?.working_hours?.end || 22;

    // Generate header with days
    const weekDays = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
    let headerHtml = '<tr><th>Время</th>';

    for (let i = 0; i < 7; i++) {
        const date = new Date(startOfWeek.getTime() + i * 24 * 60 * 60 * 1000);
        const isToday = isSameDay(date, new Date());
        headerHtml += `
            <th class="${isToday ? 'today' : ''}">
                <div class="day-name">${weekDays[i]}</div>
                <div class="day-date">${date.getDate().toString().padStart(2, '0')}.${(date.getMonth() + 1).toString().padStart(2, '0')}</div>
            </th>
        `;
    }
    headerHtml += '</tr>';
    thead.innerHTML = headerHtml;

    // Filter bookings by selected employee
    const bookings = (scheduleData?.bookings || []).filter(b => {
        if (!selectedEmployeeId) return true;
        return b.employee_id === selectedEmployeeId;
    });

    // Build color map for clients
    const uniqueClients = [...new Set(bookings.map(b => b.client_id))];
    uniqueClients.forEach((clientId, index) => {
        if (!clientColorMap[clientId]) {
            clientColorMap[clientId] = (index % 10) + 1;
        }
    });

    // Generate time slots
    let bodyHtml = '';
    for (let hour = startHour; hour < endHour; hour++) {
        bodyHtml += `<tr>
            <td>${hour.toString().padStart(2, '0')}:00</td>`;

        for (let day = 0; day < 7; day++) {
            const date = new Date(startOfWeek.getTime() + day * 24 * 60 * 60 * 1000);
            const dateStr = formatDateISO(date);

            // Find bookings for this cell
            const cellBookings = bookings.filter(b => {
                const bookingDate = b.date;
                const bookingHour = parseInt(b.time.split(':')[0]);
                return bookingDate === dateStr && bookingHour === hour;
            });

            bodyHtml += '<td>';
            cellBookings.forEach(booking => {
                const colorClass = `color-${clientColorMap[booking.client_id] || 1}`;
                const statusClass = `status-${booking.status}`;
                const duration = booking.duration || 60;
                const heightPx = Math.max(32, (duration / 60) * 36 - 4);

                bodyHtml += `
                    <div class="schedule-booking ${colorClass} ${statusClass}"
                         style="height: ${heightPx}px"
                         onclick="showBookingDetails(${booking.id})"
                         title="${escapeHtml(booking.client_name)}: ${escapeHtml(booking.service_name)}">
                        <div class="booking-name">${escapeHtml(booking.client_name)}</div>
                        <div class="booking-service">${escapeHtml(booking.service_name)}</div>
                    </div>
                `;
            });
            bodyHtml += '</td>';
        }

        bodyHtml += '</tr>';
    }

    tbody.innerHTML = bodyHtml;
}

function renderDetailedBookings(bookings) {
    const container = document.getElementById('detailedBookingsList');

    if (!bookings || bookings.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-state-icon">📋</div>
                <p>Нет записей за выбранный период</p>
            </div>
        `;
        return;
    }

    // Sort by date and time
    bookings.sort((a, b) => {
        if (a.date !== b.date) return a.date.localeCompare(b.date);
        return a.time.localeCompare(b.time);
    });

    const statusLabels = {
        'completed': 'Завершено',
        'confirmed': 'Подтверждено',
        'pending': 'Ожидает',
        'cancelled': 'Отменено',
        'pending_prepayment': 'Ожидает оплаты',
        'prepayment_sent': 'Оплата отправлена'
    };

    container.innerHTML = bookings.map(booking => {
        const statusLabel = statusLabels[booking.status] || booking.status;
        const price = booking.price ? formatMoney(booking.price) : '';

        return `
            <div class="booking-item status-${booking.status}">
                <div class="booking-time">
                    <div>${booking.time}</div>
                    <div style="font-size:10px;color:var(--text-muted)">${formatShortDate(booking.date)}</div>
                </div>
                <div class="booking-info">
                    <div class="booking-client">${escapeHtml(booking.client_name)}</div>
                    <div class="booking-service-name">${escapeHtml(booking.service_name)}</div>
                    <div class="booking-employee">👤 ${escapeHtml(booking.employee_name || 'Не указан')}</div>
                </div>
                <div style="text-align:right">
                    <div class="booking-price">${price}</div>
                    <span class="booking-status-badge ${booking.status}">${statusLabel}</span>
                </div>
            </div>
        `;
    }).join('');
}

function showBookingDetails(bookingId) {
    const booking = scheduleData?.bookings?.find(b => b.id === bookingId);
    if (!booking) return;

    const statusLabels = {
        'completed': 'Завершено',
        'confirmed': 'Подтверждено',
        'pending': 'Ожидает',
        'cancelled': 'Отменено',
        'pending_prepayment': 'Ожидает оплаты'
    };

    if (tg) {
        tg.showPopup({
            title: booking.client_name,
            message: `📅 ${formatShortDate(booking.date)} в ${booking.time}\n📋 ${booking.service_name}\n👤 ${booking.employee_name || 'Не указан'}\n💰 ${booking.price ? formatMoney(booking.price) : 'Не указано'}\n📱 ${booking.client_phone || 'Не указан'}\n\nСтатус: ${statusLabels[booking.status] || booking.status}`,
            buttons: [{ type: 'ok' }]
        });
    } else {
        alert(`${booking.client_name}\n${booking.date} ${booking.time}\n${booking.service_name}\n${booking.price ? formatMoney(booking.price) : ''}`);
    }
}

function formatDateISO(date) {
    return date.toISOString().split('T')[0];
}

function formatShortDate(dateStr) {
    const date = new Date(dateStr);
    const day = date.getDate().toString().padStart(2, '0');
    const month = (date.getMonth() + 1).toString().padStart(2, '0');
    return `${day}.${month}`;
}

function isSameDay(d1, d2) {
    return d1.getFullYear() === d2.getFullYear() &&
           d1.getMonth() === d2.getMonth() &&
           d1.getDate() === d2.getDate();
}

// Open schedule page
function openSchedule() {
    // Get user_id from Telegram or current URL
    let userId = tg?.initDataUnsafe?.user?.id;
    if (!userId) {
        const urlParams = new URLSearchParams(window.location.search);
        userId = urlParams.get('user_id');
    }

    // Navigate to schedule page with user_id
    let scheduleUrl = '/schedule';
    if (userId) {
        scheduleUrl += `?user_id=${userId}`;
    }

    // Also pass init_data if available
    if (tg?.initData) {
        const separator = scheduleUrl.includes('?') ? '&' : '?';
        scheduleUrl += `${separator}init_data=${encodeURIComponent(tg.initData)}`;
    }

    window.location.href = scheduleUrl;
}
