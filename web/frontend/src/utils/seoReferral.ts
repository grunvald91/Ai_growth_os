/** Keep the signed source when moving between the public company page and booking. */
export function withSeoRef(path: string, reference: string | null | undefined): string {
  return reference ? path + (path.includes('?') ? '&' : '?') + 'seo_ref=' + encodeURIComponent(reference) : path
}
