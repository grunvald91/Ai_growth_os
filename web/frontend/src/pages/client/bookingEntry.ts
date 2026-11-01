import type { BusinessDetail, EmployeeWithPricing, Service } from '../../api/client'

// Если услугу явно выполняет хотя бы один специалист, доступны только специалисты
// с этой привязкой. В остальных случаях сохраняем историческую семантику service_ids.
export function resourceSupportsService(
  resource: EmployeeWithPricing | null | undefined,
  serviceId: number,
  all: EmployeeWithPricing[],
): boolean {
  if (!resource) return true
  const explicit = all.some((r) => (r.service_ids || []).includes(serviceId))
  if (explicit) return (resource.service_ids || []).includes(serviceId)
  return (resource.service_ids || []).length === 0
}

export type BookingEntryStep = 'home' | 'services' | 'resource' | 'calendar'
export type BookingFlowOrder = 'service_first' | 'resource_first'

// Фильтруем противоположный шаг только пока он следует за уже выбранным параметром.
// Это позволяет поменять услугу/мастера без потери остальных вариантов.
export function getAvailableServices(
  services: Service[],
  resource: EmployeeWithPricing | null,
  employees: EmployeeWithPricing[],
  order: BookingFlowOrder,
): Service[] {
  return order === 'resource_first' && resource
    ? services.filter((service) => resourceSupportsService(resource, service.id, employees))
    : services
}

export function getAvailableResources(
  selectedServices: Service[],
  employees: EmployeeWithPricing[],
  order: BookingFlowOrder,
): EmployeeWithPricing[] {
  return order === 'service_first' && selectedServices.length > 0
    ? employees.filter((resource) => selectedServices.every((service) => resourceSupportsService(resource, service.id, employees)))
    : employees
}

export function resolveBookingEntry(
  business: BusinessDetail,
  serviceId: number | null,
  employeeId: number | null,
): { services: Service[]; resource: EmployeeWithPricing | null; step: BookingEntryStep; order: BookingFlowOrder } {
  const allServices = business.services.filter((service) => service.is_active && !service.is_additional)
  const employees = business.employees || []
  const hasSelection = serviceId !== null || employeeId !== null

  let service = allServices.find((item) => item.id === serviceId)
  let resource = employees.find((item) => item.id === employeeId) || null

  // Противоречивые параметры нельзя превращать в недоступную комбинацию.
  // Приоритет у явно выбранной услуги, а специалиста клиент сможет выбрать заново.
  const incompatibleExplicitPair = !!service && !!resource && !resourceSupportsService(resource, service.id, employees)
  if (incompatibleExplicitPair) resource = null

  // Если специалист оказывает только одну доступную услугу, повторный выбор не нужен.
  if (!service && resource) {
    const suitable = allServices.filter((item) => resourceSupportsService(resource, item.id, employees))
    if (suitable.length === 1) service = suitable[0]
  }

  // Если выбранный специалист не оказывает ни одной активной услуги,
  // не запираем клиента на пустом списке: предложим выбор услуги заново.
  if (!service && resource && allServices.every((item) => !resourceSupportsService(resource, item.id, employees))) {
    resource = null
  }

  // Если услугу оказывает только один специалист, не заставляем выбирать его повторно.
  // Для аренды зал остаётся явным выбором, даже если доступен лишь один.
  if (service && !resource && business.business_type !== 'venue_rental') {
    const suitable = employees.filter((item) => resourceSupportsService(item, service.id, employees))
    if (suitable.length === 1) resource = suitable[0]
  }

  const step: BookingEntryStep = !hasSelection
    ? 'home'
    : !service
      ? 'services'
      : employees.length > 0 && !resource
        ? 'resource'
        : 'calendar'

  // При входе с готовой услугой или специалистом первый шаг уже сделан.
  // Меняем порядок только для этого посещения, не меняя настройку бизнеса.
  const configuredOrder = business.booking_flow_order === 'resource_first' ? 'resource_first' : 'service_first'
  const order: BookingFlowOrder = incompatibleExplicitPair || (serviceId !== null && employeeId === null && service)
    ? 'service_first'
    : employeeId !== null && serviceId === null && resource
      ? 'resource_first'
      : configuredOrder

  return { services: service ? [service] : [], resource, step, order }
}
