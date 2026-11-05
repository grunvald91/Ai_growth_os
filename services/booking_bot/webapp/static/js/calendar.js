// Telegram WebApp API
const tg = window.Telegram.WebApp;

// Debug mode
const DEBUG = true;

function debugLog(msg, data = null) {
    if (DEBUG) {
        console.log(`[Calendar Debug] ${msg}`, data || '');
    }
}

// Apply Telegram theme
function applyTelegramTheme() {
    const root = document.documentElement;
    if (tg.themeParams) {
        if (tg.themeParams.bg_color) {
            root.style.setProperty('--tg-theme-bg-color', tg.themeParams.bg_color);
        }
        if (tg.themeParams.text_color) {
            root.style.setProperty('--tg-theme-text-color', tg.themeParams.text_color);
        }
        if (tg.themeParams.hint_color) {
            root.style.setProperty('--tg-theme-hint-color', tg.themeParams.hint_color);
        }
        if (tg.themeParams.link_color) {
            root.style.setProperty('--tg-theme-link-color', tg.themeParams.link_color);
        }
        if (tg.themeParams.button_color) {
            root.style.setProperty('--tg-theme-button-color', tg.themeParams.button_color);
        }
        if (tg.themeParams.button_text_color) {
            root.style.setProperty('--tg-theme-button-text-color', tg.themeParams.button_text_color);
        }
        if (tg.themeParams.secondary_bg_color) {
            root.style.setProperty('--tg-theme-secondary-bg-color', tg.themeParams.secondary_bg_color);
        }
    }
}

// State
let currentYear = new Date().getFullYear();
let currentMonth = new Date().getMonth() + 1;
let calendarData = {};
let masters = [];
let selectedMasterId = null;

// Month names
const monthNames = [
    '', 'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
    'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'
];

// Status translations
const statusLabels = {
    pending: 'Ожидает',
    confirmed: 'Подтверждена',
    completed: 'Завершена',
    cancelled: 'Отменена'
};

// DOM elements
const monthTitle = document.getElementById('monthTitle');
const prevMonthBtn = document.getElementById('prevMonth');
const nextMonthBtn = document.getElementById('nextMonth');
const todayBtn = document.getElementById('todayBtn');
const masterFilter = document.getElementById('masterFilter');
const calendarGrid = document.getElementById('calendarGrid');
const dayModal = document.getElementById('dayModal');
const bookingModal = document.getElementById('bookingModal');
const loadingOverlay = document.getElementById('loadingOverlay');

// Show/hide loading
function showLoading() {
    loadingOverlay.classList.add('active');
}

function hideLoading() {
    loadingOverlay.classList.remove('active');
}

// Show error in UI
function showError(message) {
    hideLoading();
    calendarGrid.innerHTML = `
        <div style="grid-column: 1/-1; text-align: center; padding: 40px 20px; color: var(--tg-theme-hint-color);">
            <div style="font-size: 48px; margin-bottom: 16px;">⚠️</div>
            <div style="font-size: 14px; margin-bottom: 8px;">${message}</div>
            <button onclick="loadCalendarData()" style="margin-top: 16px; padding: 10px 20px; background: var(--tg-theme-button-color); color: var(--tg-theme-button-text-color); border: none; border-radius: 8px; cursor: pointer;">
                Повторить
            </button>
        </div>
    `;
}

// API calls
async function apiCall(endpoint, options = {}) {
    debugLog('API call to:', endpoint);
    debugLog('initData available:', !!tg.initData);
    debugLog('initData length:', tg.initData ? tg.initData.length : 0);

    const headers = {
        'Content-Type': 'application/json'
    };
    if (tg?.initData) {
        headers['X-Telegram-Init-Data'] = tg.initData;
    }

    try {
        const response = await fetch(endpoint, {
            ...options,
            headers: { ...headers, ...options.headers },
            credentials: 'same-origin'
        });

        debugLog('Response status:', response.status);

        if (!response.ok) {
            // Если 401 и нет Telegram — перенаправляем на логин
            if (response.status === 401 && !tg?.initData) {
                window.location.href = '/login?to=/';
                return null;
            }
            const error = await response.json();
            debugLog('API Error:', error);
            throw new Error(error.error || `HTTP ${response.status}`);
        }

        const data = await response.json();
        debugLog('API Response:', data);
        return data;
    } catch (error) {
        debugLog('Fetch error:', error.message);
        throw error;
    }
}

// Load calendar data
async function loadCalendarData() {
    showLoading();
    debugLog('Loading calendar data for:', `${currentYear}-${currentMonth}`);

    try {
        const data = await apiCall(`/api/calendar?year=${currentYear}&month=${currentMonth}`);
        calendarData = data.bookings || {};
        masters = data.masters || [];
        updateMasterFilter();
        renderCalendar();
        debugLog('Calendar loaded successfully');
    } catch (error) {
        console.error('Failed to load calendar:', error);
        showError(`Ошибка загрузки: ${error.message}`);
    } finally {
        hideLoading();
    }
}

// Update master filter dropdown
function updateMasterFilter() {
    masterFilter.innerHTML = '<option value="">Все мастера</option>';
    masters.forEach(master => {
        const option = document.createElement('option');
        option.value = master.id;
        option.textContent = master.name;
        masterFilter.appendChild(option);
    });
}

// Render calendar grid
function renderCalendar() {
    monthTitle.textContent = `${monthNames[currentMonth]} ${currentYear}`;

    const firstDay = new Date(currentYear, currentMonth - 1, 1);
    const lastDay = new Date(currentYear, currentMonth, 0);
    const startDayOfWeek = (firstDay.getDay() + 6) % 7; // Convert to Monday-first
    const daysInMonth = lastDay.getDate();

    const today = new Date();
    const todayStr = today.toISOString().split('T')[0];

    let html = '';

    // Empty cells before first day
    for (let i = 0; i < startDayOfWeek; i++) {
        html += '<div class="day-cell empty"><div class="day-cell-inner"></div></div>';
    }

    // Days of month
    for (let day = 1; day <= daysInMonth; day++) {
        const dateStr = `${currentYear}-${String(currentMonth).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
        const isToday = dateStr === todayStr;
        const isPast = new Date(dateStr) < new Date(todayStr);
        const dayBookings = getFilteredBookings(dateStr);
        const hasBookings = dayBookings.length > 0;

        let classes = ['day-cell'];
        if (isToday) classes.push('today');
        if (isPast) classes.push('past');
        if (hasBookings) classes.push('has-bookings');

        html += `<div class="${classes.join(' ')}" data-date="${dateStr}">`;
        html += '<div class="day-cell-inner">';
        html += `<div class="day-number">${day}</div>`;

        if (hasBookings) {
            html += '<div class="booking-indicators">';
            // Show up to 4 dots
            const dotsToShow = Math.min(dayBookings.length, 4);
            for (let i = 0; i < dotsToShow; i++) {
                html += `<div class="booking-dot ${dayBookings[i].status}"></div>`;
            }
            html += '</div>';
            if (dayBookings.length > 1) {
                html += `<div class="booking-count">${dayBookings.length}</div>`;
            }
        }

        html += '</div></div>';
    }

    calendarGrid.innerHTML = html;

    // Add click handlers
    document.querySelectorAll('.day-cell:not(.empty)').forEach(cell => {
        cell.addEventListener('click', () => {
            const date = cell.dataset.date;
            openDayModal(date);
        });
    });
}

// Get filtered bookings for a date
function getFilteredBookings(dateStr) {
    const bookings = calendarData[dateStr] || [];
    if (!selectedMasterId) return bookings;
    return bookings.filter(b => b.employee_id === selectedMasterId);
}

// Format date for display
function formatDate(dateStr) {
    const date = new Date(dateStr);
    const day = date.getDate();
    const month = monthNames[date.getMonth() + 1];
    const weekdays = ['воскресенье', 'понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота'];
    const weekday = weekdays[date.getDay()];
    return `${day} ${month}, ${weekday}`;
}

// Open day modal
function openDayModal(dateStr) {
    const bookings = getFilteredBookings(dateStr);
    const modalTitle = document.getElementById('dayModalTitle');
    const bookingsList = document.getElementById('dayBookingsList');

    modalTitle.textContent = formatDate(dateStr);

    if (bookings.length === 0) {
        bookingsList.innerHTML = `
            <div class="empty-state">
                <div class="empty-state-icon">📅</div>
                <div class="empty-state-text">Нет записей на этот день</div>
            </div>
        `;
    } else {
        let html = '';
        bookings.forEach(booking => {
            html += `
                <div class="booking-card" data-booking-id="${booking.id}">
                    <div class="booking-time">
                        ${booking.time}
                        <span class="status-badge ${booking.status}">${statusLabels[booking.status]}</span>
                    </div>
                    <div class="booking-client">${booking.client_name}</div>
                    <div class="booking-service">${booking.service_name} • ${booking.service_duration} мин</div>
                    <div class="booking-master">👤 ${booking.employee_name}</div>
                </div>
            `;
        });
        bookingsList.innerHTML = html;

        // Add click handlers to booking cards
        document.querySelectorAll('.booking-card').forEach(card => {
            card.addEventListener('click', () => {
                const bookingId = card.dataset.bookingId;
                openBookingModal(bookingId);
            });
        });
    }

    dayModal.classList.add('active');
    tg.BackButton.show();
}

// Close day modal
function closeDayModal() {
    dayModal.classList.remove('active');
    if (!bookingModal.classList.contains('active')) {
        tg.BackButton.hide();
    }
}

// Open booking details modal
async function openBookingModal(bookingId) {
    showLoading();
    try {
        const booking = await apiCall(`/api/booking/${bookingId}`);
        const detailsDiv = document.getElementById('bookingDetails');
        const actionsDiv = document.getElementById('bookingActions');

        let phoneLink = booking.client_phone
            ? `<a href="tel:${booking.client_phone}">${booking.client_phone}</a>`
            : 'Не указан';

        detailsDiv.innerHTML = `
            <div class="detail-row">
                <div class="detail-label">Дата</div>
                <div class="detail-value">${formatDate(booking.date)}</div>
            </div>
            <div class="detail-row">
                <div class="detail-label">Время</div>
                <div class="detail-value">${booking.time}</div>
            </div>
            <div class="detail-row">
                <div class="detail-label">Клиент</div>
                <div class="detail-value">${booking.client_name}</div>
            </div>
            <div class="detail-row">
                <div class="detail-label">Телефон</div>
                <div class="detail-value">${phoneLink}</div>
            </div>
            <div class="detail-row">
                <div class="detail-label">Услуга</div>
                <div class="detail-value">${booking.service_name}</div>
            </div>
            <div class="detail-row">
                <div class="detail-label">Длительность</div>
                <div class="detail-value">${booking.service_duration} мин</div>
            </div>
            <div class="detail-row">
                <div class="detail-label">Стоимость</div>
                <div class="detail-value">${booking.service_price ? booking.service_price + ' ₽' : 'Не указана'}</div>
            </div>
            <div class="detail-row">
                <div class="detail-label">Мастер</div>
                <div class="detail-value">${booking.employee_name}</div>
            </div>
            <div class="detail-row">
                <div class="detail-label">Статус</div>
                <div class="detail-value"><span class="status-badge ${booking.status}">${statusLabels[booking.status]}</span></div>
            </div>
            ${booking.notes ? `
            <div class="detail-row">
                <div class="detail-label">Заметки</div>
                <div class="detail-value">${booking.notes}</div>
            </div>
            ` : ''}
        `;

        // Action buttons based on status
        let actionsHtml = '';
        if (booking.status === 'pending') {
            actionsHtml = `
                <button class="action-btn success" onclick="updateBookingStatus(${bookingId}, 'confirmed')">Подтвердить</button>
                <button class="action-btn danger" onclick="updateBookingStatus(${bookingId}, 'cancelled')">Отменить</button>
            `;
        } else if (booking.status === 'confirmed') {
            actionsHtml = `
                <button class="action-btn primary" onclick="updateBookingStatus(${bookingId}, 'completed')">Завершить</button>
                <button class="action-btn danger" onclick="updateBookingStatus(${bookingId}, 'cancelled')">Отменить</button>
            `;
        } else if (booking.status === 'cancelled') {
            actionsHtml = `
                <button class="action-btn success" onclick="updateBookingStatus(${bookingId}, 'confirmed')">Восстановить</button>
            `;
        }
        actionsDiv.innerHTML = actionsHtml;

        bookingModal.classList.add('active');
        dayModal.classList.remove('active');

    } catch (error) {
        console.error('Failed to load booking:', error);
        tg.showAlert('Ошибка загрузки записи');
    } finally {
        hideLoading();
    }
}

// Close booking modal
function closeBookingModal() {
    bookingModal.classList.remove('active');
    dayModal.classList.add('active');
}

// Update booking status
async function updateBookingStatus(bookingId, newStatus) {
    const confirmMessages = {
        confirmed: 'Подтвердить запись?',
        completed: 'Завершить запись?',
        cancelled: 'Отменить запись?'
    };

    tg.showConfirm(confirmMessages[newStatus] || 'Изменить статус?', async (confirmed) => {
        if (!confirmed) return;

        showLoading();
        try {
            await apiCall(`/api/booking/${bookingId}/status`, {
                method: 'POST',
                body: JSON.stringify({ status: newStatus })
            });

            // Reload calendar data
            await loadCalendarData();

            // Close modals
            bookingModal.classList.remove('active');
            dayModal.classList.remove('active');
            tg.BackButton.hide();

            tg.showAlert('Статус изменен');
        } catch (error) {
            console.error('Failed to update status:', error);
            tg.showAlert('Ошибка изменения статуса');
        } finally {
            hideLoading();
        }
    });
}

// Navigation
function goToPrevMonth() {
    currentMonth--;
    if (currentMonth < 1) {
        currentMonth = 12;
        currentYear--;
    }
    loadCalendarData();
}

function goToNextMonth() {
    currentMonth++;
    if (currentMonth > 12) {
        currentMonth = 1;
        currentYear++;
    }
    loadCalendarData();
}

function goToToday() {
    const today = new Date();
    currentYear = today.getFullYear();
    currentMonth = today.getMonth() + 1;
    loadCalendarData();
}

// Event listeners
prevMonthBtn.addEventListener('click', goToPrevMonth);
nextMonthBtn.addEventListener('click', goToNextMonth);
todayBtn.addEventListener('click', goToToday);

masterFilter.addEventListener('change', (e) => {
    selectedMasterId = e.target.value ? parseInt(e.target.value) : null;
    renderCalendar();
});

document.getElementById('closeDayModal').addEventListener('click', closeDayModal);
document.getElementById('closeBookingModal').addEventListener('click', closeBookingModal);

// Close modals on backdrop click
dayModal.addEventListener('click', (e) => {
    if (e.target === dayModal) closeDayModal();
});

bookingModal.addEventListener('click', (e) => {
    if (e.target === bookingModal) closeBookingModal();
});

// Telegram back button
tg.BackButton.onClick(() => {
    if (bookingModal.classList.contains('active')) {
        closeBookingModal();
    } else if (dayModal.classList.contains('active')) {
        closeDayModal();
    }
});

// Show main content
function showMainContent() {
    const initMsg = document.getElementById('initMessage');
    const mainContent = document.getElementById('mainContent');
    const calendarContainer = document.getElementById('calendarContainer');

    if (initMsg) initMsg.style.display = 'none';
    if (mainContent) mainContent.style.display = 'block';
    if (calendarContainer) calendarContainer.style.display = 'block';
}

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    debugLog('DOM loaded');
    debugLog('Telegram WebApp version:', tg.version);
    debugLog('initData:', tg.initData ? tg.initData.substring(0, 50) + '...' : 'EMPTY');
    debugLog('initDataUnsafe:', tg.initDataUnsafe);

    applyTelegramTheme();
    tg.ready();
    tg.expand();

    // Всегда пробуем загрузить данные (сервер использует fallback если нет initData)
    showMainContent();
    loadCalendarData();
});

// Make updateBookingStatus available globally
window.updateBookingStatus = updateBookingStatus;
