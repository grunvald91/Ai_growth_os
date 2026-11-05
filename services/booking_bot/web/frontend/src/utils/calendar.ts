export interface CalendarBooking {
  businessName: string
  serviceName: string
  date: string
  time: string
  durationMinutes?: number | null
  address?: string | null
  description?: string | null
}

const DEFAULT_TIMEZONE = 'Europe/Moscow'

function pad(value: number) {
  return String(value).padStart(2, '0')
}

function wallClockIcs(date: Date) {
  return [
    date.getUTCFullYear(),
    pad(date.getUTCMonth() + 1),
    pad(date.getUTCDate()),
    'T',
    pad(date.getUTCHours()),
    pad(date.getUTCMinutes()),
    pad(date.getUTCSeconds()),
  ].join('')
}

function utcIcs(date: Date) {
  return wallClockIcs(date) + 'Z'
}

function escapeIcs(value: string) {
  return value
    .replace(/\\/g, '\\\\')
    .replace(/\r?\n/g, '\\n')
    .replace(/,/g, '\\,')
    .replace(/;/g, '\\;')
}

export function downloadBookingIcs(booking: CalendarBooking) {
  const [year, month, day] = booking.date.split('-').map(Number)
  const [hours, minutes] = booking.time.split(':').map(Number)

  // UTC constructors are used only as timezone-independent wall-clock arithmetic.
  // The resulting components are written with TZID=Europe/Moscow below.
  const startWall = new Date(Date.UTC(year, month - 1, day, hours, minutes, 0))
  const endWall = new Date(startWall.getTime() + Math.max(booking.durationMinutes || 60, 15) * 60_000)

  const uid = `servgo-${booking.date}-${booking.time.replace(':', '')}-${Date.now()}@servgo`
  const title = `${booking.businessName} — ${booking.serviceName}`
  const lines = [
    'BEGIN:VCALENDAR',
    'VERSION:2.0',
    'PRODID:-//ServGo//Booking Calendar//RU',
    'CALSCALE:GREGORIAN',
    'METHOD:PUBLISH',
    'BEGIN:VTIMEZONE',
    `TZID:${DEFAULT_TIMEZONE}`,
    `X-LIC-LOCATION:${DEFAULT_TIMEZONE}`,
    'BEGIN:STANDARD',
    'TZOFFSETFROM:+0300',
    'TZOFFSETTO:+0300',
    'TZNAME:MSK',
    'DTSTART:19700101T000000',
    'END:STANDARD',
    'END:VTIMEZONE',
    'BEGIN:VEVENT',
    `UID:${uid}`,
    `DTSTAMP:${utcIcs(new Date())}`,
    `DTSTART;TZID=${DEFAULT_TIMEZONE}:${wallClockIcs(startWall)}`,
    `DTEND;TZID=${DEFAULT_TIMEZONE}:${wallClockIcs(endWall)}`,
    `SUMMARY:${escapeIcs(title)}`,
    booking.address ? `LOCATION:${escapeIcs(booking.address)}` : '',
    booking.description ? `DESCRIPTION:${escapeIcs(booking.description)}` : '',
    'BEGIN:VALARM',
    'TRIGGER:-P1D',
    'ACTION:DISPLAY',
    'DESCRIPTION:Напоминание о записи ServGo',
    'END:VALARM',
    'BEGIN:VALARM',
    'TRIGGER:-PT2H',
    'ACTION:DISPLAY',
    'DESCRIPTION:Через 2 часа ваша запись ServGo',
    'END:VALARM',
    'END:VEVENT',
    'END:VCALENDAR',
  ].filter(Boolean)

  const blob = new Blob([lines.join('\r\n') + '\r\n'], { type: 'text/calendar;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `servgo-${booking.date}-${booking.time.replace(':', '-')}.ics`
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}
