export const productImagePattern = '/product-images/[a-z0-9][a-z0-9_\\-]*\\.webp'

export const merchant = {
  id: import.meta.env.VITE_MERCHANT_ID || '3',
  name: import.meta.env.VITE_MERCHANT_NAME || 'Northstar Retail',
}

function csrfToken() {
  const cookie = document.cookie.split('; ').find((entry) => entry.startsWith('csrftoken='))
  return cookie ? decodeURIComponent(cookie.slice('csrftoken='.length)) : ''
}

export function signIn() {
  const next = window.location.pathname + window.location.search
  window.location.assign('/login/?' + new URLSearchParams({ next }))
}

export async function signOut() {
  const response = await fetch('/logout/', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'X-CSRFToken': csrfToken() },
  })
  if (!response.ok) throw new Error('Could not sign out. Refresh the page and try again.')
}

const fieldLabels = {
  delta: 'Adjustment',
  reason: 'Reason',
  non_field_errors: null,
  detail: null,
}

function errorMessage(data, fallback) {
  if (typeof data?.detail === 'string') return data.detail
  if (Array.isArray(data?.detail)) return data.detail.join(' ')
  if (!data || typeof data !== 'object') return fallback
  return Object.entries(data).map(([field, messages]) => {
    const message = Array.isArray(messages) ? messages.join(' ') : String(messages)
    const label = Object.hasOwn(fieldLabels, field)
      ? fieldLabels[field]
      : field.replaceAll('_', ' ')
    return label ? label + ' — ' + message : message
  }).join(' ') || fallback
}

async function request(path, { params, signal, body, method } = {}) {
  const query = new URLSearchParams({ merchant_id: merchant.id, ...params })
  const headers = { Accept: 'application/json' }
  const httpMethod = method || (body ? 'PATCH' : 'GET')
  if (body) {
    headers['Content-Type'] = 'application/json'
    headers['X-CSRFToken'] = csrfToken()
  }
  const response = await fetch('/api/products/' + path + '?' + query, {
    method: httpMethod,
    credentials: 'same-origin',
    signal,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  })
  const data = await response.json().catch(() => null)
  if (!response.ok) {
    if (response.status === 401 || (
      response.status === 403 && data?.detail === 'Authentication credentials were not provided.'
    )) {
      signIn()
      throw new Error('Sign in to continue.')
    }
    throw new Error(errorMessage(data, 'Request failed (' + response.status + '). Please try again.'))
  }
  if (data === null) throw new Error('The server returned an invalid response. Please try again.')
  return data
}

export const products = {
  list(search, page, signal) {
    return request('', { params: { search, page }, signal })
  },
  editor(id, signal) {
    return request(id + '/editor/', { signal })
  },
  detail(id, signal) {
    return request(id + '/', { signal })
  },
  update(id, body) {
    return request(id + '/', { body })
  },
  adjustInventory(productId, variantId, body) {
    return request(productId + '/variants/' + variantId + '/inventory/adjust/', {
      body,
      method: 'POST',
    })
  },
  activity(id, page, signal) {
    return request(id + '/activity/', { params: { page }, signal })
  },
}
