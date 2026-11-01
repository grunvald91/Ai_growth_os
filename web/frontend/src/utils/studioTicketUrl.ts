/** Server-generated one-time Growth ticket; never navigate to a caller-supplied host. */
export function isTrustedStudioTicketUrl(raw: string): boolean {
  try {
    const parsed = new URL(raw)
    if (parsed.origin !== 'https://servgo.ru' && parsed.origin !== 'https://growth.servgo.ru') return false
    if (parsed.username || parsed.password) return false
    if (parsed.pathname !== '/studio/sso' || parsed.hash) return false
    const ticket = parsed.searchParams.getAll('t')
    if (ticket.length !== 1 || !ticket[0]) return false
    let onlyTicket = true
    parsed.searchParams.forEach((_value, key) => { if (key !== 't') onlyTicket = false })
    return onlyTicket
  } catch {
    return false
  }
}
