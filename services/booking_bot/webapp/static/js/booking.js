// Telegram WebApp initialization
const tg = window.Telegram?.WebApp;

// State
const state = {
    businesses: [],
    services: [],
    employees: [],
    selectedBusiness: null,
    selectedServices: [],
    selectedEmployee: null,
    selectedDate: null,
    selectedSlot: null,
    availableSlots: [],
    additionalServices: [],
    selectedAdditionalServices: [],
    additionalServiceHours: {},
    isSoloBooking: false,
    soloPrice: null,
    isVenue: false,
    hasTimePricing: false,
    timePriceData: null,
    currentStep: 'business',
    // Reschedule state
    rescheduleBookingId: null,
    rescheduleBooking: null,
    rescheduleOldDate: null,
    rescheduleOldTime: null,
    rescheduleDate: null,
    rescheduleSlot: null,
    rescheduleSlots: [],
    // Prepayment state
    prepaymentBookingId: null,
    prepaymentData: null,
};

let webAuthUser = null;
const telegramLoginBot = document.body?.dataset.telegramBot || 'Sochiservice_bot';

// Business type icons
const businessIcons = {
    salon: '💇',
    psychologist: '🧠',
    auto_service: '🚗',
    tire_service: '🔧',
    spa: '💆',
    medical: '🏥',
    education: '📚',
    venue_rental: '🏠',
    other: '🏢'
};

// Initialize
document.addEventListener('DOMContentLoaded', async () => {
    if (tg) {
        tg.ready();
        tg.expand();
        applyTelegramTheme();
    }
    await checkWebAuth();
    loadBusinesses();
    initSwipeGestures();
});

function hasAuthenticatedClient() {
    return !!(tg && tg.initData) || !!webAuthUser;
}

async function checkWebAuth() {
    if (tg?.initData) {
        hideWebAuthPanel();
        return;
    }

    try {
        const response = await fetch('/api/auth/me', { credentials: 'same-origin' });
        if (response.ok) {
            const data = await response.json();
            webAuthUser = data.user || null;
            updateWebAuthPanel();
            return;
        }
    } catch (e) {
        console.warn('Auth check failed', e);
    }

    webAuthUser = null;
    updateWebAuthPanel();
    loadTelegramLoginWidget();
}

function updateWebAuthPanel() {
    const panel = document.getElementById('web-auth-panel');
    const title = document.getElementById('web-auth-title');
    const subtitle = document.getElementById('web-auth-subtitle');
    const widget = document.getElementById('telegram-login-widget');
    const logout = document.getElementById('web-auth-logout');
    if (!panel) return;

    panel.classList.remove('hidden');
    if (webAuthUser) {
        const name = webAuthUser.first_name || webAuthUser.username || 'Telegram';
        title.textContent = `Вы вошли как ${name}`;
        subtitle.textContent = 'Ваши записи и абонементы доступны на этом устройстве.';
        if (widget) widget.classList.add('hidden');
        if (logout) logout.classList.remove('hidden');
    } else {
        title.textContent = 'Войти через Telegram';
        subtitle.textContent = 'Так вы сможете видеть свои записи и абонементы на сайте.';
        if (widget) widget.classList.remove('hidden');
        if (logout) logout.classList.add('hidden');
    }
}

function hideWebAuthPanel() {
    const panel = document.getElementById('web-auth-panel');
    if (panel) panel.classList.add('hidden');
}

function loadTelegramLoginWidget() {
    const container = document.getElementById('telegram-login-widget');
    if (!container || container.querySelector('script')) return;

    const script = document.createElement('script');
    script.src = 'https://telegram.org/js/telegram-widget.js?22';
    script.setAttribute('data-telegram-login', telegramLoginBot);
    script.setAttribute('data-size', 'medium');
    script.setAttribute('data-onauth', 'onTelegramAuth(user)');
    script.setAttribute('data-request-access', 'write');
    script.async = true;
    container.appendChild(script);
}

window.onTelegramAuth = async function(user) {
    try {
        const response = await fetch('/api/auth/telegram', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify(user),
        });
        if (!response.ok) {
            showError('Не удалось войти через Telegram');
            return;
        }
        const data = await response.json();
        webAuthUser = data.user || null;
        updateWebAuthPanel();
        await refreshAuthenticatedSections();
    } catch (e) {
        console.error('Telegram auth error', e);
        showError('Ошибка авторизации через Telegram');
    }
};

async function logoutWebAuth() {
    await fetch('/api/auth/logout', {
        method: 'POST',
        credentials: 'same-origin',
    }).catch(() => {});
    webAuthUser = null;
    const container = document.getElementById('telegram-login-widget');
    if (container) container.innerHTML = '';
    updateWebAuthPanel();
    loadTelegramLoginWidget();
}

async function refreshAuthenticatedSections() {
    if (state.currentStep === 'my-bookings') {
        await showMyBookings();
    } else if (state.currentStep === 'my-subscriptions') {
        await showMySubscriptions();
    }
}

// Swipe gesture support
let swipeHintShown = false;

function initSwipeGestures() {
    let touchStartX = 0;
    let touchStartY = 0;
    let touchEndX = 0;
    let touchEndY = 0;
    let touchStartElement = null;

    const minSwipeDistance = 80;
    const maxVerticalDistance = 100;

    document.addEventListener('touchstart', (e) => {
        touchStartX = e.changedTouches[0].screenX;
        touchStartY = e.changedTouches[0].screenY;
        touchStartElement = e.target;
    }, { passive: true });

    document.addEventListener('touchend', (e) => {
        touchEndX = e.changedTouches[0].screenX;
        touchEndY = e.changedTouches[0].screenY;
        handleSwipe();
    }, { passive: true });

    function handleSwipe() {
        const deltaX = touchEndX - touchStartX;
        const deltaY = Math.abs(touchEndY - touchStartY);

        // Игнорируем свайпы на календаре и горизонтально-скроллируемых элементах
        if (touchStartElement) {
            const scrollableParent = touchStartElement.closest('.calendar-strip, #calendar-strip, #reschedule-calendar-strip, .slots-grid, .horizontal-scroll, .employee-tabs, #employee-tabs');
            if (scrollableParent) {
                return; // Не обрабатываем свайп как навигацию
            }
        }

        // Check if it's a horizontal swipe (left to right)
        if (deltaX > minSwipeDistance && deltaY < maxVerticalDistance) {
            handleSwipeBack();
        }
    }

    function handleSwipeBack() {
        // Determine which step to go back to based on current step
        switch (state.currentStep) {
            case 'services':
                goBack('business');
                break;
            case 'rooms':
                goBack('services');
                break;
            case 'tariff-info':
                goBack('services');
                break;
            case 'date':
                goBack('services');
                break;
            case 'venue-price':
                goBack('date');
                break;
            case 'booking-format':
                goBack('venue-price');
                break;
            case 'additional-services':
                if (hasBookingFormatStep()) {
                    goBack('booking-format');
                } else if (state.hasTimePricing) {
                    goBack('venue-price');
                } else {
                    goBack('date');
                }
                break;
            case 'confirm':
                goBackFromConfirm();
                break;
            // Reschedule screens
            case 'reschedule-date':
                goBackFromReschedule();
                break;
            case 'reschedule-confirm':
                goBack('reschedule-date');
                break;
            // Drawer menu screens - swipe back to home
            case 'my-bookings':
            case 'subscriptions':
            case 'my-subscriptions':
            case 'payment-pending':
                goBackFromDrawerScreen();
                break;
            case 'subscription-details':
                goBack('subscriptions');
                break;
            case 'subscription-payment':
                goBack('subscription-details');
                break;
        }
    }
}

// Show swipe hint animation once
function showSwipeHint() {
    if (swipeHintShown) return;

    const hint = document.getElementById('swipe-hint');
    if (hint) {
        hint.classList.remove('hidden');
        swipeHintShown = true;

        // Click anywhere to dismiss
        const dismiss = () => {
            hint.classList.add('fade-out');
            setTimeout(() => {
                hint.classList.add('hidden');
                hint.classList.remove('fade-out');
            }, 500);
            hint.removeEventListener('click', dismiss);
        };

        hint.addEventListener('click', dismiss);

        // Auto-hide after 3 seconds
        setTimeout(() => {
            if (!hint.classList.contains('hidden')) {
                dismiss();
            }
        }, 3000);
    }
}

// Apply Telegram theme colors
function applyTelegramTheme() {
    if (!tg?.themeParams) return;

    const theme = tg.themeParams;
    const root = document.documentElement;

    if (theme.bg_color) root.style.setProperty('--tg-theme-bg-color', theme.bg_color);
    if (theme.text_color) root.style.setProperty('--tg-theme-text-color', theme.text_color);
    if (theme.hint_color) root.style.setProperty('--tg-theme-hint-color', theme.hint_color);
    if (theme.link_color) root.style.setProperty('--tg-theme-link-color', theme.link_color);
    if (theme.button_color) root.style.setProperty('--tg-theme-button-color', theme.button_color);
    if (theme.button_text_color) root.style.setProperty('--tg-theme-button-text-color', theme.button_text_color);
    if (theme.secondary_bg_color) root.style.setProperty('--tg-theme-secondary-bg-color', theme.secondary_bg_color);
    if (theme.accent_text_color) root.style.setProperty('--tg-theme-accent-text-color', theme.accent_text_color);
    if (theme.destructive_text_color) root.style.setProperty('--tg-theme-destructive-text-color', theme.destructive_text_color);
    if (theme.section_bg_color) root.style.setProperty('--tg-theme-section-bg-color', theme.section_bg_color);
}

// API calls
async function apiCall(endpoint, options = {}) {
    const headers = {
        'Content-Type': 'application/json',
        ...(options.headers || {})
    };

    if (tg?.initData) {
        headers['X-Telegram-Init-Data'] = tg.initData;
    }

    const response = await fetch(endpoint, { ...options, headers, credentials: 'same-origin' });

    // Try to parse JSON response
    let data;
    try {
        data = await response.json();
    } catch (e) {
        if (!response.ok) {
            throw new Error(`Ошибка сервера: ${response.status}`);
        }
        throw e;
    }

    if (!response.ok) {
        // Use error message from server if available
        throw new Error(data.error || data.message || `Ошибка: ${response.status}`);
    }

    return data;
}

// Load businesses
async function loadBusinesses() {
    try {
        const data = await apiCall('/api/client/businesses');
        state.businesses = data.businesses || [];

        hideElement('skeleton-loader');
        renderBusinesses();
        showStep('business');

        // Check for last booking after businesses are loaded
        await checkLastBooking();
    } catch (error) {
        console.error('Error loading businesses:', error);
        showError('Не удалось загрузить список заведений');
    }
}

// Render businesses
function renderBusinesses() {
    const container = document.getElementById('businesses-list');

    if (state.businesses.length === 0) {
        container.innerHTML = `
            <div class="no-slots">
                <div class="no-slots-icon">🏢</div>
                <p>Нет доступных заведений</p>
            </div>
        `;
        return;
    }

    container.innerHTML = state.businesses.map(business => `
        <div class="business-card">
            <div class="business-card-main" onclick="selectBusiness(${business.id})">
                <div class="business-logo">
                    ${businessIcons[business.type] || '🏢'}
                </div>
                <div class="business-info">
                    <div class="business-name">${escapeHtml(business.name)}</div>
                    <div class="business-type">${escapeHtml(business.type_name || '')}</div>
                </div>
            </div>
            <button class="business-info-btn" onclick="event.stopPropagation(); showBusinessInfo(${business.id})">
                <span>i</span>
            </button>
        </div>
    `).join('');
}

// Select business
async function selectBusiness(businessId) {
    const business = state.businesses.find(b => b.id === businessId);
    if (!business) return;

    state.selectedBusiness = business;
    state.isVenue = business.type === 'venue_rental';
    state.selectedServices = [];
    state.selectedEmployee = 'any'; // Default to any

    try {
        const data = await apiCall(`/api/client/business/${businessId}/services`);
        // Merge business details (owner_username etc.) into selectedBusiness
        if (data.business) {
            Object.assign(state.selectedBusiness, data.business);
        }
        state.services = data.services || [];
        state.additionalServices = data.additional_services || [];
        state.selectedAdditionalServices = [];
        state.employees = data.employees || [];
        state.hasTimePricing = data.has_time_pricing || false;
        state.timePriceData = null;

        renderBusinessHeader();
        renderServices();
        showStep('services');
        updateBottomAction();

        // Check if business has subscriptions and show button
        checkBusinessSubscriptions(businessId);
    } catch (error) {
        console.error('Error loading services:', error);
        showError('Не удалось загрузить услуги');
    }
}

// Check if business has subscriptions
async function checkBusinessSubscriptions(businessId) {
    const btn = document.getElementById('subscriptions-btn');
    try {
        // Filter from all subscriptions
        const data = await apiCall('/api/client/all-subscriptions');
        const businessSubs = (data.subscriptions || []).filter(s => s.business_id === businessId);
        state.businessSubscriptions = businessSubs;

        if (businessSubs.length > 0) {
            btn.classList.remove('hidden');
        } else {
            btn.classList.add('hidden');
        }
    } catch (e) {
        btn.classList.add('hidden');
    }
}

// Show subscriptions for current business
function showBusinessSubscriptions() {
    if (!state.businessSubscriptions || state.businessSubscriptions.length === 0) return;

    subscriptionsData = state.businessSubscriptions;
    showStep('subscriptions');
    renderSubscriptions();

    if (tg) tg.HapticFeedback?.selectionChanged();
}

// Render business header
function renderBusinessHeader() {
    const container = document.getElementById('business-info');
    const business = state.selectedBusiness;

    const roomsLink = state.isVenue ? `<div class="view-rooms-link" onclick="goToRoomsGallery()">👀 Посмотреть залы</div>` : '';
    const videoLink = business.video_id ? `<div class="view-rooms-link" onclick="goToBusinessVideo()">🎬 Видеообзор</div>` : '';
    container.innerHTML = `
        <div class="business-header-compact">
            <div class="business-logo">
                ${business.photo_url
                    ? `<img src="${business.photo_url}" alt="${business.name}">`
                    : businessIcons[business.type] || '🏢'
                }
            </div>
            <div>
                <div class="business-name">${escapeHtml(business.name)}</div>
                ${videoLink}
                ${roomsLink}
            </div>
        </div>
    `;
}

// Render services
function renderServices() {
    const container = document.getElementById('services-list');

    if (state.services.length === 0) {
        container.innerHTML = `
            <div class="no-slots">
                <div class="no-slots-icon">📋</div>
                <p>Нет доступных услуг</p>
            </div>
        `;
        return;
    }

    container.innerHTML = state.services.map(service => {
        const isSelected = state.selectedServices.includes(service.id);
        const isContactOnly = !!service.tariff_description;
        const metaHtml = service.duration > 0 ? `<span>⏱ ${service.duration} мин</span>` : '';
        let priceHtml;
        if (isContactOnly) {
            priceHtml = '<span class="time-price-hint">по запросу</span>';
        } else if (state.isVenue && state.hasTimePricing) {
            priceHtml = '<span class="time-price-hint">по тарифу</span>';
        } else {
            priceHtml = formatPrice(service.price);
        }
        return `
            <div class="service-card ${isSelected ? 'selected' : ''}" onclick="toggleService(${service.id})">
                <div class="service-checkbox">
                    <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                        <path d="M2 7L5.5 10.5L12 4" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                    </svg>
                </div>
                <div class="service-content">
                    <div class="service-name">${escapeHtml(service.name)}</div>
                    ${metaHtml ? `<div class="service-meta">${metaHtml}</div>` : ''}
                </div>
                <div class="service-price">${priceHtml}</div>
            </div>
        `;
    }).join('');
}

// Toggle service selection
function toggleService(serviceId) {
    const allServices = state.services || [];
    const svc = allServices.find(s => s.id === serviceId);
    const isContactOnly = svc && svc.tariff_description;

    const index = state.selectedServices.indexOf(serviceId);
    if (index > -1) {
        state.selectedServices.splice(index, 1);
    } else {
        // Contact-only services are exclusive — deselect others
        if (isContactOnly) {
            state.selectedServices = [serviceId];
        } else {
            // Deselect any contact-only services when selecting regular
            state.selectedServices = state.selectedServices.filter(sid => {
                const s = allServices.find(x => x.id === sid);
                return !(s && s.tariff_description);
            });
            state.selectedServices.push(serviceId);
        }
    }
    renderServices();
    updateSummary();
    updateBottomAction();
}

// Update summary bar
function updateSummary() {
    const summary = document.getElementById('selected-services-summary');

    if (state.selectedServices.length === 0) {
        summary.classList.add('hidden');
        return;
    }

    const selectedData = state.services.filter(s => state.selectedServices.includes(s.id));
    const totalDuration = selectedData.reduce((sum, s) => sum + s.duration, 0);
    const totalPrice = selectedData.reduce((sum, s) => sum + (s.price || 0), 0);

    document.getElementById('summary-count').textContent = `${state.selectedServices.length} ${pluralize(state.selectedServices.length, 'услуга', 'услуги', 'услуг')}`;
    document.getElementById('summary-duration').textContent = `${totalDuration} мин`;
    document.getElementById('summary-price').textContent = state.isVenue && state.hasTimePricing ? 'по тарифу' : formatPrice(totalPrice);

    summary.classList.remove('hidden');
}

// Show step
function showStep(step) {
    state.currentStep = step;

    ['step-business', 'step-services', 'step-rooms', 'step-tariff-info', 'step-employee', 'step-date', 'step-venue-price', 'step-booking-format', 'step-additional-services', 'step-confirm', 'step-success', 'step-error',
     'step-my-bookings', 'step-reschedule-date', 'step-reschedule-confirm',
     'step-subscriptions', 'step-subscription-details', 'step-subscription-payment', 'step-my-subscriptions', 'step-payment-pending',
     'step-prepayment', 'step-prepayment-pending'].forEach(id => {
        hideElement(id);
    });

    showElement(`step-${step}`);
    updateBottomAction();

    // Hide employee tabs when not on date or venue-price step
    if (step !== 'date' && step !== 'venue-price') {
        const tabsContainer = document.getElementById('employee-tabs');
        const bottomAction = document.getElementById('bottom-action');
        if (tabsContainer) tabsContainer.classList.add('hidden');
        document.body.classList.remove('has-employee-tabs');
        if (bottomAction) bottomAction.classList.remove('with-tabs');
    }

    // Scroll to top
    window.scrollTo(0, 0);

    // Show swipe hint on first non-business step (but not drawer screens)
    const specialScreens = ['my-bookings', 'reschedule-date', 'reschedule-confirm', 'subscriptions', 'subscription-details', 'subscription-payment', 'my-subscriptions', 'payment-pending'];
    if (step !== 'business' && step !== 'success' && step !== 'error' && !specialScreens.includes(step)) {
        showSwipeHint();
    }
}

// Go back
function goBack(targetStep) {
    if (tg) tg.HapticFeedback?.impactOccurred('light');

    switch (targetStep) {
        case 'business':
            state.selectedBusiness = null;
            state.selectedServices = [];
            state.selectedEmployee = 'any';
            showStep('business');
            break;
        case 'services':
            showStep('services');
            updateBottomAction();
            break;
        case 'date':
            state.selectedSlot = null;
            state.timePriceData = null;
            showStep('date');
            break;
        case 'venue-price':
            showStep('venue-price');
            break;
        case 'additional-services':
            showStep('additional-services');
            break;
        case 'reschedule-date':
            state.rescheduleSlot = null;
            showStep('reschedule-date');
            break;
        case 'subscriptions':
            showStep('subscriptions');
            break;
        case 'subscription-details':
            showStep('subscription-details');
            break;
    }
}

// Update bottom action button
function updateBottomAction() {
    const container = document.getElementById('bottom-action');
    const btn = document.getElementById('action-btn');

    switch (state.currentStep) {
        case 'business':
            container.classList.add('hidden');
            break;
        case 'services':
            if (state.selectedServices.length > 0) {
                container.classList.remove('hidden');
                if (getContactOnlyService()) {
                    btn.textContent = 'Подробнее';
                    btn.disabled = false;
                    btn.onclick = goToTariffInfo;
                } else {
                    btn.textContent = 'Выбрать дату';
                    btn.disabled = false;
                    btn.onclick = goToDateSelection;
                }
            } else {
                container.classList.add('hidden');
            }
            break;
        case 'date':
            // Handle tabs positioning
            if (state.employees.length > 1) {
                container.classList.add('with-tabs');
                document.body.classList.add('has-employee-tabs');
            } else {
                container.classList.remove('with-tabs');
                document.body.classList.remove('has-employee-tabs');
            }

            if (state.selectedSlot) {
                container.classList.remove('hidden');
                btn.textContent = 'Продолжить';
                btn.disabled = false;
                if (state.isVenue && state.hasTimePricing && !hasFixedPriceServices()) {
                    btn.onclick = goToVenuePrice;
                } else if (state.isVenue && state.additionalServices.length > 0 && !hasFixedPriceServices()) {
                    btn.onclick = goToAdditionalServices;
                } else {
                    btn.onclick = goToConfirmation;
                }
            } else {
                container.classList.add('hidden');
            }
            break;
        case 'venue-price':
            container.classList.remove('hidden');
            btn.textContent = 'Продолжить';
            btn.disabled = false;
            if (hasBookingFormatStep()) {
                btn.onclick = goToBookingFormat;
            } else if (state.isVenue && state.additionalServices.length > 0 && !hasFixedPriceServices()) {
                btn.onclick = goToAdditionalServices;
            } else {
                btn.onclick = goToConfirmation;
            }
            break;
        case 'booking-format':
            container.classList.remove('hidden');
            btn.textContent = 'Продолжить';
            btn.disabled = false;
            btn.onclick = (state.isVenue && state.additionalServices.length > 0 && !hasFixedPriceServices()) ? goToAdditionalServices : goToConfirmation;
            break;
        case 'additional-services':
            container.classList.remove('hidden');
            if (state.selectedAdditionalServices.length > 0) {
                const selSvcData = state.services.filter(s => state.selectedServices.includes(s.id));
                const totDur = selSvcData.reduce((sum, s) => sum + s.duration, 0) || 60;
                const mxH = Math.max(1, Math.floor(totDur / 60));
                const addTotal = state.additionalServices
                    .filter(s => state.selectedAdditionalServices.includes(s.id))
                    .reduce((sum, s) => {
                        if (s.is_hourly_price) {
                            const h = state.additionalServiceHours[s.id] || mxH;
                            return sum + Math.round((s.price || 0) * h);
                        }
                        return sum + (s.price || 0);
                    }, 0);
                btn.textContent = `Готово (${formatPrice(addTotal)})`;
            } else {
                btn.textContent = 'Пропустить';
            }
            btn.disabled = false;
            btn.onclick = goToConfirmation;
            break;
        case 'confirm':
            container.classList.remove('hidden');
            btn.textContent = 'Забронировать';
            btn.disabled = false;
            btn.onclick = createBooking;
            break;
        case 'reschedule-date':
            if (state.rescheduleSlot) {
                container.classList.remove('hidden');
                btn.textContent = 'Продолжить';
                btn.disabled = false;
                btn.onclick = goToRescheduleConfirm;
            } else {
                container.classList.add('hidden');
            }
            break;
        case 'reschedule-confirm':
            container.classList.remove('hidden');
            btn.textContent = 'Подтвердить перенос';
            btn.disabled = false;
            btn.onclick = confirmReschedule;
            break;
        default:
            container.classList.add('hidden');
    }
}

// Render employees
function renderEmployees() {
    const container = document.getElementById('employees-list');

    let html = '';

    // "Any" option (for specialists, not for venues with specific rooms)
    if (state.employees.length > 1) {
        const anyIcon = state.isVenue ? '🏠' : '👤';
        const anyName = state.isVenue ? 'Любой зал' : 'Любой специалист';
        const anyDesc = state.isVenue ? 'Первый свободный зал' : 'Запись к первому свободному';

        html += `
            <div class="employee-card any-employee ${state.selectedEmployee === 'any' ? 'selected' : ''}" onclick="selectEmployee('any')">
                <div class="employee-avatar">${anyIcon}</div>
                <div class="employee-info">
                    <div class="employee-name">${anyName}</div>
                    <div class="employee-spec">${anyDesc}</div>
                </div>
            </div>
        `;
    }

    // Employees/Rooms list
    html += state.employees.map(emp => {
        let details = [];
        if (state.isVenue) {
            if (emp.capacity) details.push(`👥 ${emp.capacity} чел.`);
            if (emp.area_sqm) details.push(`📐 ${emp.area_sqm} м²`);
            if (emp.min_booking_minutes) {
                const h = Math.floor(emp.min_booking_minutes / 60);
                const m = emp.min_booking_minutes % 60;
                details.push(`⏱ от ${h ? h + 'ч' : ''}${m ? m + 'мин' : ''}`);
            }
        }
        const detailsHtml = details.length > 0 ? `<div class="employee-spec">${details.join(' · ')}</div>` : '';
        const descHtml = state.isVenue && emp.description ? `<div class="employee-desc">${escapeHtml(emp.description)}</div>` : '';
        const specHtml = !state.isVenue && emp.specialization ? `<div class="employee-spec">${escapeHtml(emp.specialization)}</div>` : '';
        return `
        <div class="employee-card ${state.selectedEmployee === emp.id ? 'selected' : ''}" onclick="selectEmployee(${emp.id})">
            <div class="employee-avatar">${state.isVenue ? '🚪' : getInitials(emp.name)}</div>
            <div class="employee-info">
                <div class="employee-name">${escapeHtml(emp.name)}</div>
                ${specHtml}${detailsHtml}${descHtml}
            </div>
        </div>
    `}).join('');

    container.innerHTML = html || `
        <div class="no-slots">
            <div class="no-slots-icon">${state.isVenue ? '🏠' : '👤'}</div>
            <p>Нет доступных ${state.isVenue ? 'залов' : 'специалистов'}</p>
        </div>
    `;
}

// Select employee
function selectEmployee(employeeId) {
    state.selectedEmployee = employeeId;
    renderEmployees();
    updateBottomAction();

    if (tg) tg.HapticFeedback?.selectionChanged();
}

// Go to date selection
async function goToDateSelection() {
    state.selectedDate = new Date();
    state.selectedSlot = null;

    renderCalendarStrip();
    renderEmployeeTabs();
    await loadSlots();
    showStep('date');
}

// Get filtered employees based on selected services' available_employee_ids
function getFilteredEmployees() {
    if (!state.selectedServices.length) return state.employees;

    const allServices = state.services || [];
    // Collect available_employee_ids constraints from selected services
    const constraints = state.selectedServices
        .map(sid => allServices.find(s => s.id === sid))
        .filter(s => s && s.available_employee_ids)
        .map(s => s.available_employee_ids);

    if (constraints.length === 0) return state.employees;

    // Intersect all constraints — employee must be in ALL lists
    return state.employees.filter(emp =>
        constraints.every(ids => ids.includes(emp.id))
    );
}

// Check if ALL selected services have a fixed price (not venue time pricing)
function hasFixedPriceServices() {
    const allServices = state.services || [];
    const selected = state.selectedServices.map(sid => allServices.find(s => s.id === sid)).filter(Boolean);
    // If any selected service has available_employee_ids AND a price, it's a fixed-price service
    return selected.some(s => s.available_employee_ids && s.price > 0);
}

// Check if selected service is contact-only (has tariff_description)
function getContactOnlyService() {
    const allServices = state.services || [];
    const selected = state.selectedServices.map(sid => allServices.find(s => s.id === sid)).filter(Boolean);
    return selected.find(s => s.tariff_description) || null;
}

// Show business video overlay
function goToBusinessVideo() {
    const business = state.selectedBusiness;
    if (!business || !business.video_id) return;

    const container = document.getElementById('rooms-gallery');
    const videoUrl = `/api/media/${encodeURIComponent(business.video_id)}`;
    container.innerHTML = `
        <div class="business-video-section">
            <div class="business-video-title">🎬 Видеообзор — ${escapeHtml(business.name)}</div>
            <video controls playsinline class="business-video-player" id="biz-video"
                   src="${videoUrl}">
            </video>
            <div id="video-fallback" class="video-fallback hidden">
                📱 Видео доступно в боте
            </div>
        </div>
    `;
    const vid = document.getElementById('biz-video');
    vid.onerror = () => {
        vid.classList.add('hidden');
        document.getElementById('video-fallback').classList.remove('hidden');
    };
    showStep('rooms');
}

// Show rooms gallery
function goToRoomsGallery() {
    const container = document.getElementById('rooms-gallery');
    const employees = state.employees || [];
    const business = state.selectedBusiness;

    // Business video at the top if available
    let videoHtml = '';
    if (business && business.video_id) {
        videoHtml = `
            <div class="business-video-section">
                <div class="business-video-title">🎬 Видеообзор</div>
                <video controls playsinline class="business-video-player"
                       src="/api/media/${encodeURIComponent(business.video_id)}">
                    Ваш браузер не поддерживает видео
                </video>
            </div>
        `;
    }

    const roomsHtml = employees.map(emp => {
        let details = '';
        if (emp.capacity) details += `<div class="room-detail">👥 Вместимость: ${emp.capacity} чел.</div>`;
        if (emp.area_sqm) details += `<div class="room-detail">📐 Площадь: ${emp.area_sqm} м²</div>`;
        if (emp.description) details += `<div class="room-description">${escapeHtml(emp.description)}</div>`;

        // Room photos
        let photosHtml = '';
        if (emp.photo_ids && emp.photo_ids.length > 0) {
            const slides = emp.photo_ids.map(pid =>
                `<img src="/api/media/${encodeURIComponent(pid)}" alt="${escapeHtml(emp.name)}" loading="lazy" onclick="openFullscreen(this)" onerror="this.style.display='none'">`
            ).join('');
            photosHtml = `<div class="room-photos-scroll">${slides}</div>`;
        }

        // Room video
        let videoEl = '';
        if (emp.video_id) {
            videoEl = `<video controls playsinline class="room-video-player"
                              src="/api/media/${encodeURIComponent(emp.video_id)}"
                              onerror="this.outerHTML='<div class=\\'video-fallback\\'>📱 Видео доступно в боте</div>'">
                       </video>`;
        }

        return `
            <div class="room-gallery-card">
                <div class="room-gallery-name">🏠 ${escapeHtml(emp.name)}</div>
                ${photosHtml}
                ${videoEl}
                ${details}
            </div>
        `;
    }).join('');

    container.innerHTML = videoHtml + roomsHtml;
    showStep('rooms');
}

// Fullscreen photo viewer
function openFullscreen(img) {
    const overlay = document.createElement('div');
    overlay.className = 'fullscreen-overlay';
    overlay.innerHTML = `<img src="${img.src}" alt="">`;
    overlay.onclick = () => overlay.remove();
    document.body.appendChild(overlay);
}

// Show tariff info screen for contact-only services
function goToTariffInfo() {
    const service = getContactOnlyService();
    if (!service) return;

    const container = document.getElementById('tariff-info-content');
    let html = `<div class="tariff-info-card">`;
    html += `<div class="tariff-info-title">${escapeHtml(service.name)}</div>`;
    html += `<div class="tariff-info-text">${service.tariff_description.replace(/\n/g, '<br>')}</div>`;
    html += `</div>`;

    if (state.selectedBusiness.owner_username) {
        html += `<a href="https://t.me/${state.selectedBusiness.owner_username}" target="_blank" class="contact-owner-btn">💬 Связаться с владельцем</a>`;
    }

    container.innerHTML = html;
    showStep('tariff-info');
}

// Render employee tabs (Excel-style)
function renderEmployeeTabs() {
    const tabsContainer = document.getElementById('employee-tabs');
    const tabsList = document.getElementById('employee-tabs-list');
    const bottomAction = document.getElementById('bottom-action');

    const filteredEmployees = getFilteredEmployees();

    // For venues, pre-select the red room by default instead of 'any'
    if (state.isVenue && state.selectedEmployee === 'any') {
        const redRoom = filteredEmployees.find(e => e.name && e.name.toLowerCase().includes('красн'));
        if (redRoom) state.selectedEmployee = redRoom.id;
    }

    // Show tabs only if more than 1 employee
    if (filteredEmployees.length <= 1) {
        tabsContainer.classList.add('hidden');
        document.body.classList.remove('has-employee-tabs');
        bottomAction.classList.remove('with-tabs');

        // Auto-select the only employee
        if (filteredEmployees.length === 1) {
            state.selectedEmployee = filteredEmployees[0].id;
        }
        return;
    }

    tabsContainer.classList.remove('hidden');
    document.body.classList.add('has-employee-tabs');
    bottomAction.classList.add('with-tabs');

    const icon = state.isVenue ? '🏠' : '👤';
    const anyLabel = state.isVenue ? 'Любой' : 'Любой';

    // Check if services have employee constraints — hide "any" if so
    const hasConstraints = state.selectedServices.some(sid => {
        const svc = (state.services || []).find(s => s.id === sid);
        return svc && svc.available_employee_ids;
    });

    let html = '';
    if (!hasConstraints) {
        html = `
            <button class="employee-tab ${state.selectedEmployee === 'any' ? 'active' : ''}"
                    onclick="selectEmployeeTab('any')">
                <span class="employee-tab-icon">${icon}</span>${anyLabel}
            </button>
        `;
    } else if (state.selectedEmployee === 'any') {
        state.selectedEmployee = filteredEmployees[0]?.id || 'any';
    }

    html += filteredEmployees.map(emp => `
        <button class="employee-tab ${state.selectedEmployee === emp.id ? 'active' : ''}"
                onclick="selectEmployeeTab(${emp.id})">
            ${escapeHtml(emp.name)}
        </button>
    `).join('');

    tabsList.innerHTML = html;

    // If selected employee is not in filtered list, reset to 'any'
    if (state.selectedEmployee !== 'any' && !filteredEmployees.find(e => e.id === state.selectedEmployee)) {
        state.selectedEmployee = filteredEmployees.length === 1 ? filteredEmployees[0].id : 'any';
    }

    // Scroll active tab into view
    setTimeout(() => {
        const activeTab = tabsList.querySelector('.active');
        if (activeTab) {
            activeTab.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' });
        }
    }, 100);
}

// Select employee from tab
async function selectEmployeeTab(employeeId) {
    if (state.selectedEmployee === employeeId) return;

    state.selectedEmployee = employeeId;
    state.selectedSlot = null;

    renderEmployeeTabs();
    await loadSlots();
    updateBottomAction();

    if (tg) tg.HapticFeedback?.selectionChanged();
}

// Render calendar strip
function renderCalendarStrip() {
    const container = document.getElementById('calendar-strip');
    const today = new Date();
    const days = [];

    // Generate 30 days
    for (let i = 0; i < 30; i++) {
        const date = new Date(today);
        date.setDate(today.getDate() + i);
        days.push(date);
    }

    const weekdays = ['Вс', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб'];
    const months = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];

    container.innerHTML = days.map(date => {
        const isSelected = state.selectedDate && isSameDay(date, state.selectedDate);
        const isToday = isSameDay(date, today);

        return `
            <div class="date-item ${isSelected ? 'selected' : ''} ${isToday ? 'today' : ''}"
                 onclick="selectDate('${date.toISOString()}')">
                <div class="date-weekday">${weekdays[date.getDay()]}</div>
                <div class="date-day">${date.getDate()}</div>
                <div class="date-month">${months[date.getMonth()]}</div>
            </div>
        `;
    }).join('');

    // Scroll to selected date
    setTimeout(() => {
        const selected = container.querySelector('.selected');
        if (selected) {
            selected.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' });
        }
    }, 100);
}

// Select date
async function selectDate(dateStr) {
    state.selectedDate = new Date(dateStr);
    state.selectedSlot = null;

    renderCalendarStrip();
    await loadSlots();

    if (tg) tg.HapticFeedback?.selectionChanged();
}

// Load available slots
async function loadSlots() {
    const slotsContainer = document.getElementById('slots-list');
    const noSlotsContainer = document.getElementById('no-slots');

    slotsContainer.innerHTML = '<div class="skeleton skeleton-card"></div>';
    hideElement('no-slots');

    try {
        const dateStr = formatDateISO(state.selectedDate);
        const employeeParam = state.selectedEmployee === 'any' ? '' : `&employee_id=${state.selectedEmployee}`;
        const servicesParam = state.selectedServices.join(',');

        const data = await apiCall(
            `/api/client/business/${state.selectedBusiness.id}/slots?date=${dateStr}&services=${servicesParam}${employeeParam}`
        );

        state.availableSlots = data.slots || [];
        // Если сервер сообщил о мин. времени бронирования
        if (data.min_booking_minutes && state.availableSlots.length === 0) {
            slotsContainer.innerHTML = '';
            const h = Math.floor(data.min_booking_minutes / 60);
            const m = data.min_booking_minutes % 60;
            const t = (h ? h + 'ч ' : '') + (m ? m + 'мин' : '');
            noSlotsContainer.querySelector('p').textContent = `Минимальное бронирование: ${t}`;
            showElement('no-slots');
            return;
        }
        renderSlots();
    } catch (error) {
        console.error('Error loading slots:', error);
        slotsContainer.innerHTML = '';
        noSlotsContainer.querySelector('p').textContent = 'Ошибка загрузки слотов';
        showElement('no-slots');
    }
}

// Render slots
function renderSlots() {
    const container = document.getElementById('slots-list');
    const noSlotsContainer = document.getElementById('no-slots');

    const hasAnyAvailable = state.availableSlots.some(s => s.available);
    if (state.availableSlots.length === 0 || !hasAnyAvailable) {
        container.innerHTML = '';
        showElement('no-slots');
        return;
    }

    hideElement('no-slots');
    container.innerHTML = state.availableSlots.map(slot => `
        <div class="slot-item ${state.selectedSlot === slot.time ? 'selected' : ''} ${slot.available ? '' : 'disabled'}"
             onclick="${slot.available ? `selectSlot('${slot.time}')` : ''}">
            ${slot.time}
        </div>
    `).join('');
}

// Select slot
function selectSlot(time) {
    state.selectedSlot = time;
    renderSlots();
    updateBottomAction();

    if (tg) tg.HapticFeedback?.selectionChanged();
}

// Go to venue price breakdown step
async function goToVenuePrice() {
    const container = document.getElementById('venue-price-details');
    container.innerHTML = '<div class="skeleton skeleton-card"></div>';
    showStep('venue-price');

    try {
        const selectedServicesData = state.services.filter(s => state.selectedServices.includes(s.id));
        const totalDuration = selectedServicesData.reduce((sum, s) => sum + s.duration, 0);
        const employeeId = state.selectedEmployee === 'any' ? state.employees[0]?.id : state.selectedEmployee;

        const dateParam = state.selectedDate ? `&date=${formatDateISO(state.selectedDate)}` : '';
        const data = await apiCall(
            `/api/client/business/${state.selectedBusiness.id}/calculate-price?employee_id=${employeeId}&time=${state.selectedSlot}&duration=${totalDuration}${dateParam}`
        );

        state.timePriceData = data;
        // Remember which employee was used for price calculation
        state.venuePriceEmployeeId = employeeId;
        renderVenuePrice(data, totalDuration, selectedServicesData);
    } catch (error) {
        console.error('Error calculating price:', error);
        container.innerHTML = `
            <div class="no-slots">
                <div class="no-slots-icon">❌</div>
                <p>Ошибка расчёта стоимости</p>
            </div>
        `;
    }
}

// Render venue price breakdown
function renderVenuePrice(data, totalDuration, selectedServicesData) {
    const container = document.getElementById('venue-price-details');
    const employee = state.selectedEmployee === 'any'
        ? state.employees[0]
        : state.employees.find(e => e.id === state.selectedEmployee);

    const employeeName = employee ? employee.name : 'Помещение';

    // Calculate end time (cross-midnight aware)
    const [h, m] = state.selectedSlot.split(':').map(Number);
    const endMinutes = h * 60 + m + totalDuration;
    const endH = Math.floor(endMinutes / 60) % 24;
    const endM = endMinutes % 60;
    const endStr = `${String(endH).padStart(2, '0')}:${String(endM).padStart(2, '0')}`;
    const nextDayMark = endMinutes >= 24 * 60 ? ' (+1 день)' : '';

    let html = `
        <div class="venue-price-card">
            <div class="venue-price-header">
                <div class="venue-price-room">🏠 ${escapeHtml(employeeName)}</div>
                <div class="venue-price-time">🕐 ${state.selectedSlot}–${endStr}${nextDayMark} (${totalDuration} мин)</div>
            </div>
            <div class="venue-price-segments">
    `;

    if (data.has_time_price && data.total > 0) {
        for (const seg of data.segments) {
            html += `
                <div class="venue-price-segment">
                    <span class="segment-time">${seg.time_from}–${seg.time_to} (${seg.hours_str})</span>
                    <span class="segment-rate">× ${seg.price_per_hour}₽/ч</span>
                    <span class="segment-subtotal">${formatPrice(seg.subtotal)}</span>
                </div>
            `;
        }
        html += `
            </div>
            <div class="venue-price-total">
                <span>Итого</span>
                <span>${formatPrice(data.total)}</span>
            </div>
        </div>
        `;
    } else {
        // Fallback to standard price
        const fallbackPrice = selectedServicesData.reduce((sum, s) => sum + (s.price || 0), 0);
        html += `
                <div class="venue-price-segment">
                    <span class="segment-time">Стандартный тариф</span>
                    <span class="segment-subtotal">${formatPrice(fallbackPrice)}</span>
                </div>
            </div>
            <div class="venue-price-total">
                <span>Итого</span>
                <span>${formatPrice(fallbackPrice)}</span>
            </div>
        </div>
        `;
    }

    container.innerHTML = html;
}

// Go to additional services step (venue_rental only)
function goToAdditionalServices() {
    renderAdditionalServices();
    showStep('additional-services');
}

// Render additional services
function renderAdditionalServices() {
    const container = document.getElementById('additional-services-list');

    // Рассчитываем общую длительность для почасовых услуг
    const selectedServicesData = state.services.filter(s => state.selectedServices.includes(s.id));
    const totalDuration = selectedServicesData.reduce((sum, s) => sum + s.duration, 0) || 60;
    const maxHours = Math.max(1, Math.floor(totalDuration / 60));

    container.innerHTML = state.additionalServices.map(service => {
        const isSelected = state.selectedAdditionalServices.includes(service.id);

        if (service.is_hourly_price && isSelected) {
            const hours = state.additionalServiceHours[service.id] || maxHours;
            const totalPrice = Math.round(service.price * hours);
            return `
            <div class="service-card additional-service-card selected">
                <div class="service-checkbox" onclick="toggleAdditionalService(${service.id})">
                    <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                        <path d="M2 7L5.5 10.5L12 4" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                    </svg>
                </div>
                <div class="service-content" onclick="toggleAdditionalService(${service.id})">
                    <div class="service-name">${escapeHtml(service.name)}</div>
                </div>
                <div class="svc-hours-control">
                    <button class="svc-hours-btn" onclick="changeAdditionalHours(${service.id}, -1)">−</button>
                    <span class="svc-hours-info">${hours}ч × ${service.price}₽ = ${totalPrice}₽</span>
                    <button class="svc-hours-btn" onclick="changeAdditionalHours(${service.id}, 1)">+</button>
                </div>
            </div>
            `;
        }

        let priceDisplay;
        if (service.is_hourly_price) {
            priceDisplay = `${service.price}₽/ч`;
        } else {
            priceDisplay = formatPrice(service.price);
        }
        return `
            <div class="service-card additional-service-card ${isSelected ? 'selected' : ''}" onclick="toggleAdditionalService(${service.id})">
                <div class="service-checkbox">
                    <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                        <path d="M2 7L5.5 10.5L12 4" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                    </svg>
                </div>
                <div class="service-content">
                    <div class="service-name">${escapeHtml(service.name)}</div>
                    ${service.is_hourly_price ? `<div class="service-hourly-hint">${service.price}₽/ч</div>` : ''}
                </div>
                <div class="service-price">${priceDisplay}</div>
            </div>
        `;
    }).join('');
}

// Change hours for hourly additional service
function changeAdditionalHours(serviceId, delta) {
    const selectedServicesData = state.services.filter(s => state.selectedServices.includes(s.id));
    const totalDuration = selectedServicesData.reduce((sum, s) => sum + s.duration, 0) || 60;
    const maxHours = Math.max(1, Math.floor(totalDuration / 60));

    const current = state.additionalServiceHours[serviceId] || maxHours;
    const newVal = current + delta;

    if (newVal < 1) {
        // Убираем услугу из выбора
        const idx = state.selectedAdditionalServices.indexOf(serviceId);
        if (idx > -1) state.selectedAdditionalServices.splice(idx, 1);
        delete state.additionalServiceHours[serviceId];
    } else if (newVal > maxHours) {
        return;
    } else {
        state.additionalServiceHours[serviceId] = newVal;
    }

    renderAdditionalServices();
    updateBottomAction();
    if (tg) tg.HapticFeedback?.selectionChanged();
}

// Toggle additional service selection
function toggleAdditionalService(serviceId) {
    const index = state.selectedAdditionalServices.indexOf(serviceId);
    if (index > -1) {
        state.selectedAdditionalServices.splice(index, 1);
        delete state.additionalServiceHours[serviceId];
    } else {
        state.selectedAdditionalServices.push(serviceId);
        // Для почасовых — ставим дефолт = maxHours
        const svc = state.additionalServices.find(s => s.id === serviceId);
        if (svc && svc.is_hourly_price) {
            const selectedServicesData = state.services.filter(s => state.selectedServices.includes(s.id));
            const totalDuration = selectedServicesData.reduce((sum, s) => sum + s.duration, 0) || 60;
            const maxHours = Math.max(1, Math.floor(totalDuration / 60));
            state.additionalServiceHours[serviceId] = maxHours;
        }
    }
    renderAdditionalServices();
    updateBottomAction();

    if (tg) tg.HapticFeedback?.selectionChanged();
}

// Go back from confirmation to the right step
function goBackFromConfirm() {
    if (state.isVenue && state.additionalServices.length > 0) {
        goBack('additional-services');
    } else if (hasBookingFormatStep()) {
        goBack('booking-format');
    } else if (state.isVenue && state.hasTimePricing) {
        goBack('venue-price');
    } else {
        goBack('date');
    }
}

// Change date/time from confirmation screen
function changeDateTime() {
    // Reset intermediate state
    state.isSoloBooking = false;
    state.soloPrice = null;
    state.selectedAdditionalServices = [];
    state.additionalServiceHours = {};
    state.timePriceData = null;
    goBack('date');
    if (tg) tg.HapticFeedback?.selectionChanged();
}

// Check if booking format step is needed (employee has solo_price_per_hour)
function hasBookingFormatStep() {
    if (!state.isVenue || !state.hasTimePricing) return false;
    // Skip format step for fixed-price services (e.g., drum lessons)
    if (hasFixedPriceServices()) return false;
    const empId = state.selectedEmployee === 'any' ? state.employees[0]?.id : state.selectedEmployee;
    const emp = state.employees.find(e => e.id === empId);
    return emp && emp.solo_price_per_hour;
}

// Go to booking format step
function goToBookingFormat() {
    const empId = state.selectedEmployee === 'any' ? state.employees[0]?.id : state.selectedEmployee;
    const emp = state.employees.find(e => e.id === empId);
    const selectedServicesData = state.services.filter(s => state.selectedServices.includes(s.id));
    const totalDuration = selectedServicesData.reduce((sum, s) => sum + s.duration, 0) || 60;
    const groupPrice = (state.timePriceData && state.timePriceData.total > 0) ? state.timePriceData.total : selectedServicesData.reduce((sum, s) => sum + (s.price || 0), 0);
    const soloPrice = Math.round(emp.solo_price_per_hour * totalDuration / 60);

    state.soloPrice = soloPrice;

    const container = document.getElementById('booking-format-list');
    container.innerHTML = `
        <div class="format-card ${!state.isSoloBooking ? 'selected' : ''}" onclick="selectBookingFormat(false, ${groupPrice})">
            <div class="format-icon">👥</div>
            <div class="format-info">
                <div class="format-title">Группа / дуэт</div>
                <div class="format-price">${formatPrice(groupPrice)}</div>
            </div>
        </div>
        <div class="format-card ${state.isSoloBooking ? 'selected' : ''}" onclick="selectBookingFormat(true, ${soloPrice})">
            <div class="format-icon">👤</div>
            <div class="format-info">
                <div class="format-title">Самостоятельно / Педагог + ученик</div>
                <div class="format-price">${formatPrice(soloPrice)}</div>
                <div class="format-hint">Спецтариф ${emp.solo_price_per_hour}₽/ч</div>
            </div>
        </div>
    `;

    showStep('booking-format');
}

// Select booking format
function selectBookingFormat(isSolo, price) {
    state.isSoloBooking = isSolo;
    if (isSolo) {
        state.soloPrice = price;
    }
    // Re-render to update selection
    goToBookingFormat();
    if (tg) tg.HapticFeedback?.selectionChanged();
}

// Go to confirmation
function goToConfirmation() {
    renderConfirmation();
    showStep('confirm');
}

// Render confirmation
function renderConfirmation() {
    const container = document.getElementById('confirm-details');
    const selectedServicesData = state.services.filter(s => state.selectedServices.includes(s.id));
    const selectedAdditionalData = state.additionalServices.filter(s => state.selectedAdditionalServices.includes(s.id));
    const totalDuration = selectedServicesData.reduce((sum, s) => sum + s.duration, 0);
    let mainPrice;
    if (state.isSoloBooking && state.soloPrice) {
        mainPrice = state.soloPrice;
    } else if (state.hasTimePricing && state.timePriceData && state.timePriceData.total > 0) {
        mainPrice = state.timePriceData.total;
    } else {
        mainPrice = selectedServicesData.reduce((sum, s) => sum + (s.price || 0), 0);
    }
    const maxHours = Math.max(1, Math.floor(totalDuration / 60));
    const additionalPrice = selectedAdditionalData.reduce((sum, s) => {
        if (s.is_hourly_price) {
            const hours = state.additionalServiceHours[s.id] || maxHours;
            return sum + Math.round((s.price || 0) * hours);
        }
        return sum + (s.price || 0);
    }, 0);
    const totalPrice = mainPrice + additionalPrice;

    const employee = state.selectedEmployee === 'any'
        ? { name: state.isVenue ? 'Любой зал' : 'Любой специалист' }
        : state.employees.find(e => e.id === state.selectedEmployee) || { name: 'Не выбран' };

    const dateStr = formatDateRu(state.selectedDate);

    let html = `
        <div class="confirm-row">
            <span class="confirm-label">Заведение</span>
            <span class="confirm-value">${escapeHtml(state.selectedBusiness.name)}</span>
        </div>
        <div class="confirm-row">
            <span class="confirm-label">${state.isVenue ? 'Зал' : 'Специалист'}</span>
            <span class="confirm-value">${escapeHtml(employee.name)}</span>
        </div>
        <div class="confirm-row confirm-row-clickable" onclick="changeDateTime()">
            <span class="confirm-label">Дата и время</span>
            <span class="confirm-value">${dateStr}, ${state.selectedSlot} <span class="confirm-edit-hint">✎</span></span>
        </div>
        <div class="confirm-row">
            <span class="confirm-label">${state.isVenue ? 'Время аренды' : 'Услуги'}</span>
            <span class="confirm-value">${selectedServicesData.map(s => escapeHtml(s.name)).join(', ')}</span>
        </div>
        <div class="confirm-row">
            <span class="confirm-label">Длительность</span>
            <span class="confirm-value">${totalDuration} мин</span>
        </div>
    `;

    if (state.isSoloBooking) {
        html += `
            <div class="confirm-row">
                <span class="confirm-label">Формат</span>
                <span class="confirm-value">Самостоятельно / Педагог + ученик</span>
            </div>
        `;
    }

    if (selectedAdditionalData.length > 0) {
        const addNames = selectedAdditionalData.map(s => {
            if (s.is_hourly_price) {
                const hours = state.additionalServiceHours[s.id] || maxHours;
                const h = hours === Math.floor(hours) ? Math.floor(hours) + 'ч' : hours.toFixed(1) + 'ч';
                const total = Math.round(s.price * hours);
                return `${escapeHtml(s.name)} (${s.price}₽/ч × ${h} = ${total}₽)`;
            }
            return escapeHtml(s.name);
        });
        html += `
            <div class="confirm-row confirm-section">
                <span class="confirm-label">Доп. услуги</span>
                <span class="confirm-value">${addNames.join(', ')}</span>
            </div>
        `;
    }

    html += `
        <div class="confirm-row">
            <span class="confirm-label">Стоимость</span>
            <span class="confirm-value confirm-total">${formatPrice(totalPrice)}</span>
        </div>
    `;

    // Contact owner button for venue rental
    if (state.isVenue && state.selectedBusiness.owner_username) {
        html += `
            <a href="https://t.me/${state.selectedBusiness.owner_username}" target="_blank" class="contact-owner-btn">
                💬 Связаться с владельцем
            </a>
        `;
    }

    // Если нет Telegram — показать поля для ввода имени и телефона
    const hasAuth = hasAuthenticatedClient();
    if (!hasAuth) {
        html += `
            <div class="confirm-section-title">Ваши данные</div>
            <div class="confirm-row">
                <div class="web-client-field">
                    <label for="web-client-name">Имя *</label>
                    <input type="text" id="web-client-name" placeholder="Введите ваше имя" required>
                </div>
            </div>
            <div class="confirm-row">
                <div class="web-client-field">
                    <label for="web-client-phone">Телефон *</label>
                    <input type="tel" id="web-client-phone" placeholder="+7 (999) 123-45-67" required>
                </div>
            </div>
        `;
    }

    container.innerHTML = html;
}

// Create booking
async function createBooking() {
    const btn = document.getElementById('action-btn');
    btn.classList.add('loading');
    btn.disabled = true;

    try {
        const notes = document.getElementById('booking-notes').value.trim();

        // Проверяем данные клиента для web-бронирования без Telegram
        const hasAuth = hasAuthenticatedClient();
        let clientName = '';
        let clientPhone = '';
        if (!hasAuth) {
            const nameInput = document.getElementById('web-client-name');
            const phoneInput = document.getElementById('web-client-phone');
            clientName = nameInput ? nameInput.value.trim() : '';
            clientPhone = phoneInput ? phoneInput.value.trim() : '';
            if (!clientName || !clientPhone) {
                btn.classList.remove('loading');
                btn.disabled = false;
                alert('Пожалуйста, заполните имя и телефон');
                return;
            }
        }

        // For venue with time pricing, use the employee from price calculation
        let bookingEmployeeId = state.selectedEmployee === 'any' ? null : state.selectedEmployee;
        if (state.isVenue && state.hasTimePricing && state.venuePriceEmployeeId) {
            bookingEmployeeId = state.venuePriceEmployeeId;
        }

        const bookingData = {
            business_id: state.selectedBusiness.id,
            service_ids: state.selectedServices,
            additional_service_ids: state.selectedAdditionalServices,
            additional_service_hours: state.additionalServiceHours,
            is_solo_booking: state.isSoloBooking,
            employee_id: bookingEmployeeId,
            date: formatDateISO(state.selectedDate),
            time: state.selectedSlot,
            notes: notes || null
        };

        // Добавляем данные клиента для web-бронирования
        if (!hasAuth && clientName) {
            bookingData.client_name = clientName;
            bookingData.client_phone = clientPhone;
        }

        const response = await apiCall('/api/client/booking', {
            method: 'POST',
            body: JSON.stringify(bookingData)
        });

        if (response.success) {
            // Проверяем, требуется ли предоплата
            if (response.requires_prepayment && response.prepayment) {
                state.prepaymentBookingId = response.prepayment.first_booking_id;
                state.prepaymentData = response.prepayment;
                showPrepaymentScreen(response.prepayment);
                if (tg) tg.HapticFeedback?.notificationOccurred('warning');
            } else {
                showSuccess(response.message || 'Ваша запись успешно создана!');
                if (tg) tg.HapticFeedback?.notificationOccurred('success');
            }
        } else {
            throw new Error(response.error || 'Ошибка создания записи');
        }
    } catch (error) {
        console.error('Error creating booking:', error);
        btn.classList.remove('loading');
        btn.disabled = false;

        if (tg) {
            tg.HapticFeedback?.notificationOccurred('error');
            tg.showPopup({
                title: 'Ошибка',
                message: error.message || 'Не удалось создать запись. Попробуйте еще раз.',
                buttons: [{ type: 'ok' }]
            });
        } else {
            alert(error.message || 'Ошибка создания записи');
        }
    }
}

// Show prepayment screen
function showPrepaymentScreen(prepayment) {
    const container = document.getElementById('prepayment-info');

    let paymentDetailsHtml = '';
    if (prepayment.payment_phone) {
        paymentDetailsHtml += `
            <div class="payment-detail-item">
                <div class="payment-detail-label">📱 Номер для перевода</div>
                <div class="payment-detail-value">${escapeHtml(prepayment.payment_phone)}</div>
            </div>
        `;
    }
    if (prepayment.payment_details) {
        paymentDetailsHtml += `
            <div class="payment-detail-item">
                <div class="payment-detail-label">📝 Реквизиты</div>
                <div class="payment-detail-value">${escapeHtml(prepayment.payment_details)}</div>
            </div>
        `;
    }

    let amountText = `${prepayment.prepayment_amount} ₽`;
    if (prepayment.prepayment_percent < 100) {
        amountText += ` (${prepayment.prepayment_percent}% от ${prepayment.total_price} ₽)`;
    }

    let cancellationPhotoHtml = '';
    if (prepayment.cancellation_policy_photo) {
        cancellationPhotoHtml = `
            <div class="cancellation-policy-photo">
                <img src="/api/media/${encodeURIComponent(prepayment.cancellation_policy_photo)}"
                     alt="Правила отмены бронирования"
                     onclick="openFullscreen(this)"
                     onerror="this.parentElement.style.display='none'">
            </div>
        `;
    }

    container.innerHTML = `
        <div class="payment-card-header">
            <div class="payment-card-title">${escapeHtml(prepayment.business_name)}</div>
            <div class="payment-card-subtitle">${formatDateRu(state.selectedDate)}, ${state.selectedSlot}</div>
        </div>

        ${cancellationPhotoHtml}

        <div class="payment-amount-section">
            <div class="payment-amount-label">💰 Сумма предоплаты</div>
            <div class="payment-amount-value">${amountText}</div>
        </div>

        <div class="payment-details-section">
            <div class="payment-details-title">Реквизиты для оплаты</div>
            ${paymentDetailsHtml}
        </div>

        <div class="payment-warning">
            ⚠️ После перевода денег нажмите «Оплата отправлена». Бронирование будет подтверждено после проверки оплаты.
        </div>
    `;

    hideElement('bottom-action');
    showStep('prepayment');
}

// Confirm prepayment sent
async function confirmPrepaymentSent() {
    if (!state.prepaymentBookingId) return;

    const btn = document.querySelector('#step-prepayment .primary-btn');
    if (btn) {
        btn.classList.add('loading');
        btn.disabled = true;
    }

    try {
        const response = await apiCall(`/api/client/booking/${state.prepaymentBookingId}/prepayment-sent`, {
            method: 'POST'
        });

        if (response.success) {
            if (tg) tg.HapticFeedback?.notificationOccurred('success');
            showStep('prepayment-pending');
        } else {
            throw new Error(response.error || 'Не удалось отправить подтверждение');
        }
    } catch (error) {
        console.error('Error confirming prepayment:', error);
        if (btn) {
            btn.classList.remove('loading');
            btn.disabled = false;
        }

        if (tg) {
            tg.HapticFeedback?.notificationOccurred('error');
            tg.showPopup({
                title: 'Ошибка',
                message: error.message || 'Не удалось отправить подтверждение',
                buttons: [{ type: 'ok' }]
            });
        } else {
            alert(error.message || 'Не удалось отправить подтверждение');
        }
    }
}

// Show success
function showSuccess(message) {
    document.getElementById('success-message').textContent = message;
    hideElement('bottom-action');
    showStep('success');
}

// Show error
function showError(message) {
    document.getElementById('error-message').textContent = message;
    hideElement('bottom-action');
    showStep('error');
}

// Close WebApp
function closeWebApp() {
    if (tg && tg.initData) {
        tg.close();
    } else {
        // Web-версия: вернуться на главную для новой записи
        window.location.href = '/book';
    }
}

// Utility functions
function showElement(id) {
    document.getElementById(id)?.classList.remove('hidden');
}

function hideElement(id) {
    document.getElementById(id)?.classList.add('hidden');
}

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function formatPrice(price) {
    if (!price) return 'Бесплатно';
    return `${price.toLocaleString('ru-RU')} ₽`;
}

function formatDateISO(date) {
    return date.toISOString().split('T')[0];
}

function formatDateRu(date) {
    const months = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
                    'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];
    const weekdays = ['воскресенье', 'понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота'];

    return `${date.getDate()} ${months[date.getMonth()]}, ${weekdays[date.getDay()]}`;
}

function isSameDay(d1, d2) {
    return d1.getFullYear() === d2.getFullYear() &&
           d1.getMonth() === d2.getMonth() &&
           d1.getDate() === d2.getDate();
}

function getInitials(name) {
    if (!name) return '?';
    return name.split(' ').map(n => n[0]).join('').substring(0, 2).toUpperCase();
}

function pluralize(n, one, two, five) {
    const mod10 = n % 10;
    const mod100 = n % 100;

    if (mod100 >= 11 && mod100 <= 14) return five;
    if (mod10 === 1) return one;
    if (mod10 >= 2 && mod10 <= 4) return two;
    return five;
}

// === Last Booking Functions ===

// Store last bookings data fetched from API
let lastBookingsData = [];
let repeatMenuOpen = false;

async function checkLastBooking() {
    try {
        const data = await apiCall('/api/client/last-bookings');

        if (data.bookings && data.bookings.length > 0) {
            lastBookingsData = data.bookings;
            const btn = document.getElementById('repeat-booking-btn');
            if (btn) btn.classList.remove('hidden');

            // Render menu items
            renderRepeatMenu();
        }
    } catch (e) {
        console.error('Error loading last bookings:', e);
    }
}

function renderRepeatMenu() {
    const menu = document.getElementById('repeat-menu');
    menu.innerHTML = lastBookingsData.map((booking, index) => `
        <div class="repeat-menu-item" onclick="selectRepeatBooking(${index})">
            <span class="repeat-menu-business">${escapeHtml(booking.business_name)}</span>
            <span class="repeat-menu-service">${escapeHtml(booking.service_name)}</span>
        </div>
    `).join('');
}

function toggleRepeatMenu() {
    const menu = document.getElementById('repeat-menu');

    repeatMenuOpen = !repeatMenuOpen;

    if (repeatMenuOpen) {
        menu.classList.remove('hidden');
    } else {
        menu.classList.add('hidden');
    }

    if (tg) tg.HapticFeedback?.selectionChanged();
}

async function selectRepeatBooking(index) {
    const bookingData = lastBookingsData[index];
    if (!bookingData) return;

    // Close menu
    toggleRepeatMenu();

    try {
        // Find business
        const business = state.businesses.find(b => b.id === bookingData.business_id);
        if (!business) {
            if (tg) tg.showPopup({ title: 'Ошибка', message: 'Заведение больше не доступно', buttons: [{ type: 'ok' }] });
            return;
        }

        // Load business services
        const data = await apiCall(`/api/client/business/${business.id}/services`);
        state.selectedBusiness = business;
        state.isVenue = business.type === 'venue_rental';
        state.services = data.services || [];
        state.employees = data.employees || [];

        // Restore service selection
        if (bookingData.service_id && state.services.some(s => s.id === bookingData.service_id)) {
            state.selectedServices = [bookingData.service_id];
        } else {
            if (tg) tg.showPopup({ title: 'Ошибка', message: 'Услуга больше не доступна', buttons: [{ type: 'ok' }] });
            selectBusiness(business.id);
            return;
        }

        // Restore employee selection
        if (bookingData.employee_id && state.employees.some(e => e.id === bookingData.employee_id)) {
            state.selectedEmployee = bookingData.employee_id;
        } else {
            state.selectedEmployee = 'any';
        }

        // Go to date selection
        await goToDateSelection();

        if (tg) tg.HapticFeedback?.selectionChanged();

    } catch (error) {
        console.error('Error repeating booking:', error);
    }
}

// === Business Info Modal ===

async function showBusinessInfo(businessId) {
    const business = state.businesses.find(b => b.id === businessId);
    if (!business) return;

    const modal = document.getElementById('business-modal');
    const photoContainer = document.getElementById('modal-business-photo');
    const nameEl = document.getElementById('modal-business-name');
    const typeEl = document.getElementById('modal-business-type');
    const infoEl = document.getElementById('modal-business-info');

    // Set business name and type
    nameEl.textContent = business.name;
    typeEl.textContent = business.type_name || '';

    // Set photo or icon - try to load from Telegram
    photoContainer.innerHTML = `<div class="modal-photo-icon">${businessIcons[business.type] || '🏢'}</div>`;

    if (business.photos) {
        try {
            const photoIds = JSON.parse(business.photos);
            if (photoIds && photoIds.length > 0) {
                const photoData = await apiCall(`/api/client/photo?file_id=${encodeURIComponent(photoIds[0])}`);
                if (photoData.url) {
                    photoContainer.innerHTML = `<img src="${photoData.url}" alt="${escapeHtml(business.name)}" onerror="this.parentElement.innerHTML='<div class=\\'modal-photo-icon\\'>${businessIcons[business.type] || '🏢'}</div>'">`;
                }
            }
        } catch (e) {
            console.log('Could not load business photo:', e);
        }
    }

    // Build info content
    let infoHtml = '';

    if (business.description) {
        infoHtml += `<div class="modal-info-item">
            <div class="modal-info-label">Описание</div>
            <div class="modal-info-value">${escapeHtml(business.description)}</div>
        </div>`;
    }

    if (business.working_hours) {
        infoHtml += `<div class="modal-info-item">
            <div class="modal-info-label">🕐 Время работы</div>
            <div class="modal-info-value">${escapeHtml(business.working_hours)}</div>
        </div>`;
    }

    if (business.break_time) {
        infoHtml += `<div class="modal-info-item">
            <div class="modal-info-label">☕ Перерыв</div>
            <div class="modal-info-value">${escapeHtml(business.break_time)}</div>
        </div>`;
    }

    if (business.address) {
        infoHtml += `<div class="modal-info-item">
            <div class="modal-info-label">📍 Адрес</div>
            <div class="modal-info-value">${escapeHtml(business.address)}</div>
            <a href="https://yandex.ru/maps/?text=${encodeURIComponent(business.address)}" target="_blank" class="modal-directions-btn">
                🗺 Как добраться
            </a>
        </div>`;
    }

    if (business.phone) {
        infoHtml += `<div class="modal-info-item">
            <div class="modal-info-label">📞 Телефон</div>
            <a href="tel:${business.phone}" class="modal-info-value modal-phone">${escapeHtml(business.phone)}</a>
        </div>`;
    }

    infoEl.innerHTML = infoHtml;

    // Show modal
    modal.classList.remove('hidden');
    document.body.style.overflow = 'hidden';

    if (tg) tg.HapticFeedback?.impactOccurred('light');
}

function closeBusinessModal() {
    const modal = document.getElementById('business-modal');
    modal.classList.add('hidden');
    document.body.style.overflow = '';
}


// === Drawer Functions ===

let drawerOpen = false;
let drawerSwipeStartX = 0;
let drawerSwipeStartY = 0;
let isDraggingDrawer = false;
const DRAWER_WIDTH = 280;

function openDrawer() {
    drawerOpen = true;
    const drawer = document.getElementById('drawer');
    const overlay = document.getElementById('drawer-overlay');

    // Reset any inline styles from swipe
    drawer.style.transition = 'transform 0.25s ease-out';
    drawer.style.transform = 'translateX(0)';
    overlay.style.transition = 'opacity 0.25s ease-out';
    overlay.style.opacity = '0.5';
    overlay.style.pointerEvents = 'auto';

    drawer.classList.add('open');
    overlay.classList.remove('hidden');
    overlay.classList.add('visible');
    document.body.style.overflow = 'hidden';

    // Clean up inline styles after animation
    setTimeout(() => {
        drawer.style.transition = '';
        drawer.style.transform = '';
        overlay.style.transition = '';
        overlay.style.opacity = '';
        overlay.style.pointerEvents = '';
    }, 260);

    if (tg) tg.HapticFeedback?.impactOccurred('light');
}

function closeDrawer() {
    drawerOpen = false;
    const drawer = document.getElementById('drawer');
    const overlay = document.getElementById('drawer-overlay');

    drawer.classList.remove('open');
    drawer.style.transition = 'transform 0.25s ease-out';
    drawer.style.transform = 'translateX(-100%)';
    overlay.style.transition = 'opacity 0.25s ease-out';
    overlay.style.opacity = '0';
    // Immediately disable pointer events so overlay doesn't block clicks
    overlay.style.pointerEvents = 'none';

    setTimeout(() => {
        if (!drawerOpen) {
            overlay.classList.remove('visible');
            overlay.classList.add('hidden');
            drawer.style.transition = '';
            drawer.style.transform = '';
            overlay.style.transition = '';
            overlay.style.opacity = '';
            overlay.style.pointerEvents = '';
        }
    }, 260);
    document.body.style.overflow = '';
}

// Interactive drawer swipe (only on business screen)
function initDrawerSwipe() {
    document.addEventListener('touchstart', onDrawerTouchStart, { passive: true });
    document.addEventListener('touchmove', onDrawerTouchMove, { passive: false });
    document.addEventListener('touchend', onDrawerTouchEnd, { passive: true });
}

function onDrawerTouchStart(e) {
    drawerSwipeStartX = e.touches[0].clientX;
    drawerSwipeStartY = e.touches[0].clientY;
    isDraggingDrawer = false;

    const target = e.target;
    const drawer = document.getElementById('drawer');
    const overlay = document.getElementById('drawer-overlay');

    // Allow swipe to open on business screen
    if (state.currentStep === 'business' && !drawerOpen) {
        isDraggingDrawer = true;
        isDraggingToClose = false;
    }
    // Allow swipe to close when drawer is open
    else if (drawerOpen) {
        // If touch is on the overlay (not on drawer), let click handler work
        if (target === overlay || overlay.contains(target)) {
            isDraggingDrawer = false;
            return;
        }
        // If touch is on the drawer content, allow swipe to close
        if (drawer.contains(target)) {
            isDraggingDrawer = true;
            isDraggingToClose = true;
        }
    }
}

let isDraggingToClose = false;

function onDrawerTouchMove(e) {
    if (!isDraggingDrawer) return;

    const deltaX = e.touches[0].clientX - drawerSwipeStartX;
    const deltaY = Math.abs(e.touches[0].clientY - drawerSwipeStartY);

    // If vertical movement is significant, cancel drawer swipe
    if (deltaY > 80) {
        isDraggingDrawer = false;
        // Reset drawer position
        const drawer = document.getElementById('drawer');
        const overlay = document.getElementById('drawer-overlay');
        if (isDraggingToClose) {
            // Reset to open state
            drawer.style.transition = 'transform 0.2s ease';
            drawer.style.transform = 'translateX(0)';
            overlay.style.opacity = '0.5';
        } else {
            // Reset to closed state
            drawer.style.transition = 'transform 0.2s ease';
            drawer.style.transform = '';
            overlay.style.opacity = '';
            overlay.classList.remove('visible');
            setTimeout(() => overlay.classList.add('hidden'), 200);
        }
        return;
    }

    const drawer = document.getElementById('drawer');
    const overlay = document.getElementById('drawer-overlay');

    // Swipe to CLOSE (drawer is open, swiping left)
    if (isDraggingToClose && deltaX < -5) {
        e.preventDefault();
        const progress = Math.min(Math.abs(deltaX) / DRAWER_WIDTH, 1);
        drawer.style.transform = `translateX(${-progress * DRAWER_WIDTH}px)`;
        drawer.style.transition = 'none';
        overlay.style.opacity = (1 - progress) * 0.5;
    }
    // Swipe to OPEN (drawer is closed, swiping right)
    else if (!isDraggingToClose && deltaX > 5) {
        e.preventDefault();
        const progress = Math.min(deltaX / DRAWER_WIDTH, 1);
        drawer.style.transform = `translateX(${-DRAWER_WIDTH + (progress * DRAWER_WIDTH)}px)`;
        drawer.style.transition = 'none';

        overlay.classList.remove('hidden');
        overlay.classList.add('visible');
        overlay.style.opacity = progress * 0.5;
    }
}

function onDrawerTouchEnd(e) {
    if (!isDraggingDrawer) return;

    const drawer = document.getElementById('drawer');
    const overlay = document.getElementById('drawer-overlay');
    const deltaX = e.changedTouches[0].clientX - drawerSwipeStartX;

    isDraggingDrawer = false;

    // Add smooth transition for finishing animation
    drawer.style.transition = 'transform 0.25s ease-out';
    overlay.style.transition = 'opacity 0.25s ease-out';

    // Handle CLOSING (swipe left when drawer is open)
    if (isDraggingToClose) {
        if (deltaX < -DRAWER_WIDTH * 0.25) {
            // Close the drawer
            closeDrawer();
        } else {
            // Snap back to open
            drawer.style.transform = 'translateX(0)';
            overlay.style.opacity = '0.5';
            setTimeout(() => {
                drawer.style.transition = '';
                overlay.style.transition = '';
            }, 250);
        }
    }
    // Handle OPENING (swipe right when drawer is closed)
    else {
        if (deltaX > DRAWER_WIDTH * 0.25) {
            openDrawer();
        } else {
            // Close smoothly
            drawer.style.transform = `translateX(-100%)`;
            overlay.style.opacity = '0';
            setTimeout(() => {
                overlay.classList.remove('visible');
                overlay.classList.add('hidden');
                drawer.style.transition = '';
                overlay.style.transition = '';
                drawer.style.transform = '';
            }, 250);
        }
    }
}

// Initialize drawer swipe on load
document.addEventListener('DOMContentLoaded', () => {
    initDrawerSwipe();

    // Add tap handler for overlay (more reliable than onclick on mobile)
    const overlay = document.getElementById('drawer-overlay');
    if (overlay) {
        let overlayTouchStart = null;
        overlay.addEventListener('touchstart', (e) => {
            overlayTouchStart = { x: e.touches[0].clientX, y: e.touches[0].clientY };
        }, { passive: true });

        overlay.addEventListener('touchend', (e) => {
            if (!overlayTouchStart) return;
            const deltaX = Math.abs(e.changedTouches[0].clientX - overlayTouchStart.x);
            const deltaY = Math.abs(e.changedTouches[0].clientY - overlayTouchStart.y);
            // If it was a tap (minimal movement), close drawer
            if (deltaX < 10 && deltaY < 10) {
                closeDrawer();
            }
            overlayTouchStart = null;
        }, { passive: true });
    }
});


// === My Bookings Functions ===

async function showMyBookings() {
    closeDrawer();
    showStep('my-bookings');
    await loadMyBookings();
}

let myBookingsData = [];

async function loadMyBookings() {
    const container = document.getElementById('my-bookings-list');
    container.innerHTML = '<div class="skeleton skeleton-card"></div><div class="skeleton skeleton-card"></div>';

    try {
        const data = await apiCall('/api/client/my-bookings');
        myBookingsData = data.bookings || [];
        renderMyBookings(myBookingsData);
    } catch (error) {
        console.error('Error loading bookings:', error);
        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-state-icon">❌</div>
                <div class="empty-state-title">Ошибка загрузки</div>
                <div class="empty-state-text">Не удалось загрузить записи</div>
            </div>
        `;
    }
}

function renderMyBookings(bookings) {
    const container = document.getElementById('my-bookings-list');

    if (bookings.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-state-icon">📋</div>
                <div class="empty-state-title">Нет записей</div>
                <div class="empty-state-text">У вас пока нет активных записей</div>
            </div>
        `;
        return;
    }

    const statusText = {
        'pending': 'Ожидает',
        'confirmed': 'Подтверждена'
    };

    container.innerHTML = bookings.map(booking => {
        const dateObj = new Date(booking.date);
        const dateStr = formatDateRu(dateObj);

        return `
            <div class="booking-card">
                <div class="booking-card-header">
                    <div class="booking-business-name">${escapeHtml(booking.business_name)}</div>
                    <span class="booking-status ${booking.status}">${statusText[booking.status] || booking.status}</span>
                </div>
                <div class="booking-card-body">
                    <div class="booking-info-row">
                        <span>📅</span>
                        <strong>${dateStr}, ${booking.time}</strong>
                    </div>
                    <div class="booking-info-row">
                        <span>💇</span>
                        <span>${booking.services.map(s => escapeHtml(s.name)).join(', ')}</span>
                    </div>
                    <div class="booking-info-row">
                        <span>👤</span>
                        <span>${escapeHtml(booking.employee_name)}</span>
                    </div>
                </div>
                ${booking.can_cancel || booking.can_reschedule ? `
                <div class="booking-actions">
                    ${booking.can_reschedule ? `
                    <button class="booking-action-btn booking-reschedule-btn" onclick="startReschedule(${booking.id})">
                        Перенести
                    </button>
                    ` : ''}
                    ${booking.can_cancel ? `
                    <button class="booking-action-btn booking-cancel-btn" onclick="cancelBooking(${booking.id})">
                        Отменить
                    </button>
                    ` : ''}
                </div>
                ` : ''}
            </div>
        `;
    }).join('');
}

async function cancelBooking(bookingId) {
    if (tg) {
        tg.showConfirm('Вы уверены, что хотите отменить запись?', async (confirmed) => {
            if (confirmed) {
                await performCancelBooking(bookingId);
            }
        });
    } else {
        if (confirm('Вы уверены, что хотите отменить запись?')) {
            await performCancelBooking(bookingId);
        }
    }
}

async function performCancelBooking(bookingId) {
    try {
        const response = await apiCall(`/api/client/booking/${bookingId}/cancel`, {
            method: 'POST'
        });

        if (response.success) {
            if (tg) tg.HapticFeedback?.notificationOccurred('success');
            await loadMyBookings();
        } else {
            throw new Error(response.error || 'Не удалось отменить запись');
        }
    } catch (error) {
        console.error('Error cancelling booking:', error);
        if (tg) {
            tg.HapticFeedback?.notificationOccurred('error');
            tg.showPopup({
                title: 'Ошибка',
                message: error.message || 'Не удалось отменить запись',
                buttons: [{ type: 'ok' }]
            });
        } else {
            alert(error.message || 'Не удалось отменить запись');
        }
    }
}


// === Reschedule Functions ===

async function startReschedule(bookingId) {
    const booking = myBookingsData.find(b => b.id === bookingId);
    if (!booking) return;

    state.rescheduleBookingId = bookingId;
    state.rescheduleBooking = booking;
    state.rescheduleOldDate = booking.date;
    state.rescheduleOldTime = booking.time;
    state.rescheduleDate = new Date();
    state.rescheduleSlot = null;
    state.rescheduleSlots = [];

    // Show info banner
    const banner = document.getElementById('reschedule-info-banner');
    const oldDateObj = new Date(booking.date);
    banner.innerHTML = `
        <div class="reschedule-banner-content">
            <div class="reschedule-banner-title">${escapeHtml(booking.business_name)}</div>
            <div class="reschedule-banner-detail">${booking.services.map(s => escapeHtml(s.name)).join(', ')} — ${escapeHtml(booking.employee_name)}</div>
            <div class="reschedule-banner-old">Текущее время: <strong>${formatDateRu(oldDateObj)}, ${booking.time}</strong></div>
        </div>
    `;

    renderRescheduleCalendar();
    await loadRescheduleSlots();
    showStep('reschedule-date');

    if (tg) tg.HapticFeedback?.selectionChanged();
}

function renderRescheduleCalendar() {
    const container = document.getElementById('reschedule-calendar-strip');
    const today = new Date();
    const days = [];

    for (let i = 0; i < 30; i++) {
        const date = new Date(today);
        date.setDate(today.getDate() + i);
        days.push(date);
    }

    const weekdays = ['Вс', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб'];
    const months = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];

    container.innerHTML = days.map(date => {
        const isSelected = state.rescheduleDate && isSameDay(date, state.rescheduleDate);
        const isToday = isSameDay(date, today);

        return `
            <div class="date-item ${isSelected ? 'selected' : ''} ${isToday ? 'today' : ''}"
                 onclick="selectRescheduleDate('${date.toISOString()}')">
                <div class="date-weekday">${weekdays[date.getDay()]}</div>
                <div class="date-day">${date.getDate()}</div>
                <div class="date-month">${months[date.getMonth()]}</div>
            </div>
        `;
    }).join('');

    setTimeout(() => {
        const selected = container.querySelector('.selected');
        if (selected) {
            selected.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' });
        }
    }, 100);
}

async function selectRescheduleDate(dateStr) {
    state.rescheduleDate = new Date(dateStr);
    state.rescheduleSlot = null;

    renderRescheduleCalendar();
    await loadRescheduleSlots();

    if (tg) tg.HapticFeedback?.selectionChanged();
}

async function loadRescheduleSlots() {
    const slotsContainer = document.getElementById('reschedule-slots-list');
    const noSlotsContainer = document.getElementById('reschedule-no-slots');

    slotsContainer.innerHTML = '<div class="skeleton skeleton-card"></div>';
    hideElement('reschedule-no-slots');

    try {
        const booking = state.rescheduleBooking;
        const dateStr = formatDateISO(state.rescheduleDate);
        const employeeParam = booking.employee_id ? `&employee_id=${booking.employee_id}` : '';
        const servicesParam = booking.service_id;
        // Передаём duration для услуг с duration=0 (уборка и т.п.)
        const durationParam = booking.duration > 0 ? `&duration=${booking.duration}` : '&duration=60';

        const data = await apiCall(
            `/api/client/business/${booking.business_id}/slots?date=${dateStr}&services=${servicesParam}${employeeParam}${durationParam}`
        );

        state.rescheduleSlots = data.slots || [];
        renderRescheduleSlots();
    } catch (error) {
        console.error('Error loading reschedule slots:', error);
        slotsContainer.innerHTML = '';
        noSlotsContainer.querySelector('p').textContent = 'Ошибка загрузки слотов';
        showElement('reschedule-no-slots');
    }
}

function renderRescheduleSlots() {
    const container = document.getElementById('reschedule-slots-list');
    const noSlotsContainer = document.getElementById('reschedule-no-slots');

    const hasAnyAvailable = state.rescheduleSlots.some(s => s.available);
    if (state.rescheduleSlots.length === 0 || !hasAnyAvailable) {
        container.innerHTML = '';
        showElement('reschedule-no-slots');
        return;
    }

    hideElement('reschedule-no-slots');
    container.innerHTML = state.rescheduleSlots.map(slot => `
        <div class="slot-item ${state.rescheduleSlot === slot.time ? 'selected' : ''} ${slot.available ? '' : 'disabled'}"
             onclick="${slot.available ? `selectRescheduleSlot('${slot.time}')` : ''}">
            ${slot.time}
        </div>
    `).join('');
}

function selectRescheduleSlot(time) {
    state.rescheduleSlot = time;
    renderRescheduleSlots();
    updateBottomAction();

    if (tg) tg.HapticFeedback?.selectionChanged();
}

function goToRescheduleConfirm() {
    const booking = state.rescheduleBooking;
    const oldDateObj = new Date(booking.date);
    const newDateObj = state.rescheduleDate;

    const container = document.getElementById('reschedule-confirm-details');
    container.innerHTML = `
        <div class="reschedule-compare">
            <div class="reschedule-compare-header">${escapeHtml(booking.business_name)}</div>
            <div class="reschedule-compare-service">${booking.services.map(s => escapeHtml(s.name)).join(', ')} — ${escapeHtml(booking.employee_name)}</div>

            <div class="reschedule-compare-row old">
                <div class="reschedule-compare-label">Было</div>
                <div class="reschedule-compare-value">
                    <div class="reschedule-compare-date">${formatDateRu(oldDateObj)}</div>
                    <div class="reschedule-compare-time">${booking.time}</div>
                </div>
            </div>

            <div class="reschedule-compare-arrow">→</div>

            <div class="reschedule-compare-row new">
                <div class="reschedule-compare-label">Станет</div>
                <div class="reschedule-compare-value">
                    <div class="reschedule-compare-date">${formatDateRu(newDateObj)}</div>
                    <div class="reschedule-compare-time">${state.rescheduleSlot}</div>
                </div>
            </div>
        </div>
    `;

    showStep('reschedule-confirm');
}

async function confirmReschedule() {
    const btn = document.getElementById('action-btn');
    btn.classList.add('loading');
    btn.disabled = true;

    try {
        const response = await apiCall(`/api/client/booking/${state.rescheduleBookingId}/reschedule`, {
            method: 'POST',
            body: JSON.stringify({
                date: formatDateISO(state.rescheduleDate),
                time: state.rescheduleSlot
            })
        });

        if (response.success) {
            if (tg) tg.HapticFeedback?.notificationOccurred('success');

            // Reset reschedule state
            state.rescheduleBookingId = null;
            state.rescheduleBooking = null;
            state.rescheduleSlot = null;

            // Show my bookings with reloaded data
            showStep('my-bookings');
            await loadMyBookings();

            if (tg) {
                tg.showPopup({
                    title: 'Готово',
                    message: response.message || 'Запись успешно перенесена',
                    buttons: [{ type: 'ok' }]
                });
            }
        } else {
            throw new Error(response.error || 'Ошибка переноса записи');
        }
    } catch (error) {
        console.error('Error rescheduling booking:', error);
        btn.classList.remove('loading');
        btn.disabled = false;

        if (tg) {
            tg.HapticFeedback?.notificationOccurred('error');
            tg.showPopup({
                title: 'Ошибка',
                message: error.message || 'Не удалось перенести запись. Попробуйте еще раз.',
                buttons: [{ type: 'ok' }]
            });
        } else {
            alert(error.message || 'Ошибка переноса записи');
        }
    }
}

function goBackFromReschedule() {
    state.rescheduleBookingId = null;
    state.rescheduleBooking = null;
    state.rescheduleSlot = null;
    showStep('my-bookings');
    if (tg) tg.HapticFeedback?.impactOccurred('light');
}


// === Subscriptions Functions ===

let subscriptionsData = [];
let currentSubscriptionId = null;
let currentClientSubId = null;
let currentPaymentData = null;

async function showSubscriptions() {
    closeDrawer();
    showStep('subscriptions');
    await loadSubscriptions();
}

async function loadSubscriptions() {
    const container = document.getElementById('subscriptions-list');
    container.innerHTML = '<div class="skeleton skeleton-card"></div><div class="skeleton skeleton-card"></div>';

    try {
        const data = await apiCall('/api/client/all-subscriptions');
        subscriptionsData = data.subscriptions || [];
        renderSubscriptions();
    } catch (error) {
        console.error('Error loading subscriptions:', error);
        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-state-icon">❌</div>
                <div class="empty-state-title">Ошибка загрузки</div>
                <div class="empty-state-text">Не удалось загрузить абонементы</div>
            </div>
        `;
    }
}

function renderSubscriptions() {
    const container = document.getElementById('subscriptions-list');

    if (subscriptionsData.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-state-icon">🎫</div>
                <div class="empty-state-title">Нет абонементов</div>
                <div class="empty-state-text">Пока нет доступных абонементов</div>
            </div>
        `;
        return;
    }

    container.innerHTML = subscriptionsData.map(sub => `
        <div class="subscription-card" onclick="showSubscriptionDetails(${sub.id})">
            <div class="subscription-card-header">
                <div class="subscription-name">${escapeHtml(sub.name)}</div>
                <div class="subscription-price">${formatPrice(sub.price)}</div>
            </div>
            <div class="subscription-business">${escapeHtml(sub.business_name)}</div>
            ${sub.savings > 0 ? `
                <div class="subscription-savings">💰 Экономия ${sub.savings}₽ (${sub.savings_percent}%)</div>
            ` : ''}
            <div class="subscription-services">
                <span class="subscription-services-count">${sub.services.length}</span>
                ${pluralize(sub.services.length, 'услуга', 'услуги', 'услуг')} •
                ${sub.validity_days} ${pluralize(sub.validity_days, 'день', 'дня', 'дней')}
            </div>
        </div>
    `).join('');
}

async function showSubscriptionDetails(subscriptionId) {
    currentSubscriptionId = subscriptionId;
    showStep('subscription-details');

    const container = document.getElementById('subscription-details-content');
    container.innerHTML = '<div class="skeleton skeleton-card"></div>';

    try {
        const data = await apiCall(`/api/client/subscription/${subscriptionId}`);
        renderSubscriptionDetails(data.subscription);
    } catch (error) {
        console.error('Error loading subscription details:', error);
        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-state-icon">❌</div>
                <div class="empty-state-title">Ошибка загрузки</div>
            </div>
        `;
    }
}

function renderSubscriptionDetails(sub) {
    const container = document.getElementById('subscription-details-content');

    let servicesHtml = sub.services.map(s => `
        <div class="subscription-service-item">
            <div class="subscription-service-name">${escapeHtml(s.name)}</div>
            <div class="subscription-service-quantity">x${s.quantity}</div>
        </div>
    `).join('');

    container.innerHTML = `
        <div class="subscription-details-card">
            <div class="subscription-details-header">
                <div class="subscription-details-name">${escapeHtml(sub.name)}</div>
                <div class="subscription-details-business">${escapeHtml(sub.business_name)}</div>
            </div>

            <div class="subscription-details-price-row">
                <div class="subscription-details-price">${formatPrice(sub.price)}</div>
                <div class="subscription-details-validity">${sub.validity_days} ${pluralize(sub.validity_days, 'день', 'дня', 'дней')}</div>
            </div>

            ${sub.savings > 0 ? `
                <div style="margin-top: 12px;">
                    <div class="subscription-savings">💰 Экономия ${sub.savings}₽ (${sub.savings_percent}%)</div>
                </div>
            ` : ''}

            <div class="subscription-details-services">
                <div class="subscription-details-services-title">В абонемент входит</div>
                ${servicesHtml}
            </div>

            ${sub.description ? `
                <div class="subscription-description">
                    ${escapeHtml(sub.description)}
                </div>
            ` : ''}
        </div>

        <button class="primary-btn" onclick="buySubscription(${sub.id})" ${!sub.has_payment_details ? 'disabled' : ''}>
            ${sub.has_payment_details ? 'Купить абонемент' : 'Оплата не настроена'}
        </button>
    `;
}

async function buySubscription(subscriptionId) {
    try {
        const response = await apiCall(`/api/client/subscription/${subscriptionId}/buy`, {
            method: 'POST'
        });

        if (response.success) {
            currentClientSubId = response.client_subscription_id;
            currentPaymentData = response;
            showPaymentScreen(response);
        } else {
            throw new Error(response.error || 'Не удалось начать покупку');
        }
    } catch (error) {
        console.error('Error buying subscription:', error);
        if (tg) {
            tg.HapticFeedback?.notificationOccurred('error');
            tg.showPopup({
                title: 'Ошибка',
                message: error.message || 'Не удалось начать покупку',
                buttons: [{ type: 'ok' }]
            });
        } else {
            alert(error.message || 'Не удалось начать покупку');
        }
    }
}

function showPaymentScreen(data) {
    showStep('subscription-payment');

    const container = document.getElementById('payment-info');

    let detailsHtml = data.payment_details.map(detail => `
        <div class="payment-detail-item">
            <div class="payment-detail-label">${escapeHtml(detail.label)}</div>
            <div class="payment-detail-value">${escapeHtml(detail.value)}</div>
        </div>
    `).join('');

    container.innerHTML = `
        <div class="payment-card-header">
            <div class="payment-card-title">${escapeHtml(data.subscription_name)}</div>
            <div class="payment-card-amount">${formatPrice(data.amount)}</div>
        </div>

        <div class="payment-details-section">
            <div class="payment-details-title">Реквизиты для оплаты</div>
            ${detailsHtml}
        </div>

        <div class="payment-warning">
            ⚠️ После перевода денег нажмите «Оплата отправлена» или прикрепите чек для подтверждения.
        </div>
    `;
}

async function confirmPaymentSent() {
    if (!currentClientSubId) return;

    try {
        const response = await apiCall(`/api/client/subscription/${currentClientSubId}/payment-sent`, {
            method: 'POST'
        });

        if (response.success) {
            if (tg) tg.HapticFeedback?.notificationOccurred('success');
            showPaymentSuccess(response.message);
        } else {
            throw new Error(response.error || 'Не удалось отправить подтверждение');
        }
    } catch (error) {
        console.error('Error confirming payment:', error);
        if (tg) {
            tg.HapticFeedback?.notificationOccurred('error');
            tg.showPopup({
                title: 'Ошибка',
                message: error.message || 'Не удалось отправить подтверждение',
                buttons: [{ type: 'ok' }]
            });
        } else {
            alert(error.message || 'Не удалось отправить подтверждение');
        }
    }
}

function showReceiptUpload() {
    document.getElementById('receipt-input').click();
}

async function uploadReceipt(file) {
    if (!file || !currentClientSubId) return;

    try {
        const formData = new FormData();
        formData.append('receipt', file);

        const headers = {};
        if (tg?.initData) {
            headers['X-Telegram-Init-Data'] = tg.initData;
        }

        const response = await fetch(`/api/client/subscription/${currentClientSubId}/upload-receipt`, {
            method: 'POST',
            body: formData,
            headers: headers,
            credentials: 'same-origin'
        });

        const data = await response.json();

        if (data.success) {
            if (tg) tg.HapticFeedback?.notificationOccurred('success');
            showPaymentSuccess(data.message);
        } else {
            throw new Error(data.error || 'Не удалось загрузить чек');
        }
    } catch (error) {
        console.error('Error uploading receipt:', error);
        if (tg) {
            tg.HapticFeedback?.notificationOccurred('error');
            tg.showPopup({
                title: 'Ошибка',
                message: error.message || 'Не удалось загрузить чек',
                buttons: [{ type: 'ok' }]
            });
        } else {
            alert(error.message || 'Не удалось загрузить чек');
        }
    }
}

function showPaymentSuccess(message) {
    hideElement('bottom-action');
    showStep('payment-pending');
    if (tg) tg.HapticFeedback?.notificationOccurred('success');
}

function goToMySubscriptions() {
    showStep('my-subscriptions');
    loadMySubscriptions();
}


// === My Subscriptions Functions ===

async function showMySubscriptions() {
    closeDrawer();
    showStep('my-subscriptions');
    await loadMySubscriptions();
}

async function loadMySubscriptions() {
    const container = document.getElementById('my-subscriptions-list');
    container.innerHTML = '<div class="skeleton skeleton-card"></div><div class="skeleton skeleton-card"></div>';

    try {
        const data = await apiCall('/api/client/my-subscriptions');
        renderMySubscriptions(data.subscriptions || []);
    } catch (error) {
        console.error('Error loading my subscriptions:', error);
        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-state-icon">❌</div>
                <div class="empty-state-title">Ошибка загрузки</div>
                <div class="empty-state-text">Не удалось загрузить абонементы</div>
            </div>
        `;
    }
}

function renderMySubscriptions(subscriptions) {
    const container = document.getElementById('my-subscriptions-list');

    if (subscriptions.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-state-icon">💳</div>
                <div class="empty-state-title">Нет абонементов</div>
                <div class="empty-state-text">У вас пока нет купленных абонементов</div>
            </div>
        `;
        return;
    }

    const statusText = {
        'pending_payment': 'Ожидает оплаты',
        'payment_sent': 'Оплата отправлена',
        'active': 'Активен',
        'expired': 'Истёк',
        'depleted': 'Израсходован',
        'cancelled': 'Отменён'
    };

    container.innerHTML = subscriptions.map(sub => {
        let progressHtml = '';
        if (sub.remaining && sub.remaining.length > 0) {
            const progressItems = sub.remaining.map(r => `
                <div class="subscription-progress-item">
                    <div class="subscription-progress-name">${escapeHtml(r.service)}</div>
                    <div class="subscription-progress-count">${r.total - r.used} / ${r.total}</div>
                </div>
            `).join('');

            progressHtml = `
                <div class="subscription-progress">
                    <div class="subscription-progress-title">Остаток услуг</div>
                    ${progressItems}
                </div>
            `;
        }

        return `
            <div class="my-subscription-card">
                <div class="my-subscription-header">
                    <div class="my-subscription-name">${escapeHtml(sub.name)}</div>
                    <span class="my-subscription-status ${sub.status}">${statusText[sub.status] || sub.status}</span>
                </div>
                <div class="my-subscription-business">${escapeHtml(sub.business_name)}</div>
                ${sub.status === 'active' && sub.days_left !== undefined ? `
                    <div class="my-subscription-expires">
                        Осталось <strong>${sub.days_left}</strong> ${pluralize(sub.days_left, 'день', 'дня', 'дней')}
                    </div>
                ` : ''}
                ${progressHtml}
            </div>
        `;
    }).join('');
}


// === Navigation Helpers ===

function goBackFromDrawerScreen() {
    showStep('business');
    if (tg) tg.HapticFeedback?.impactOccurred('light');
}
