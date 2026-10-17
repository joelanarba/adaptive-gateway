export const API_BASE_URL = process.env.NEXT_PUBLIC_GATEWAY_URL || 'http://localhost:8000';

export async function fetchWithAuth(url, options = {}) {
  const token = sessionStorage.getItem('token');
  const headers = {
    'Content-Type': 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...options.headers,
  };

  const response = await fetch(`${API_BASE_URL}${url}`, {
    ...options,
    headers,
  });

  if (response.status === 401) {
    // Token expired or invalid
    sessionStorage.removeItem('token');
    sessionStorage.removeItem('refresh_token');
    window.dispatchEvent(new Event('adaptive-gateway:unauthorized'));
    return null;
  }

  return response;
}
