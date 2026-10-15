export type User = {
  id: number;
  email: string;
  name: string;
  role: 'ADMIN' | 'SCANNER' | 'DESK';
  gate?: '' | 'GATE_1' | 'GATE_2' | 'GATE_3' | 'GATE_4';
  desk?: '' | 'DESK_1' | 'DESK_2';
  is_active?: boolean;
};

export type GateId = 'GATE_1' | 'GATE_2' | 'GATE_3' | 'GATE_4';

export type ScannerAccount = User & {
  role: 'SCANNER';
  gate: GateId | '';
  is_active: boolean;
};

export type DeskId = 'DESK_1' | 'DESK_2';
export type DeskAccount = User & {
  role: 'DESK';
  desk: DeskId | '';
  is_active: boolean;
};

export type DashboardData = {
  registrations: number;
  tickets_issued: number;
  online_tickets: number;
  on_spot_tickets: number;
  emails_pending_or_failed: number;
  check_ins: number;
  remaining_eligible: number;
  failed_sync_jobs: number;
  google_sheet_imports: {
    worksheet: 'Online_Tickets' | 'OnSpot_Tickets';
    last_synced_at: string | null;
    last_error: string;
    last_result: {
      created?: number;
      updated?: number;
      skipped?: number;
      rejected?: number;
      issued?: number;
    };
  }[];
};

export type TicketRecord = {
  id: string;
  ticket_number: string;
  source: string;
  ticket_type: string;
  attendee_name: string;
  attendee_email: string;
  status: string;
  email_status: string;
  payment_status: string;
  checked_in_at: string | null;
  checkin_method: 'QR' | 'MANUAL' | null;
  eligibility_basis: 'IMPORTED_PAYMENT_STATUS' | 'IMPORTED_EMAIL_SENT' | null;
  registration_desk: string | null;
};

export type SheetSyncStatus = 'PENDING' | 'PROCESSING' | 'SUCCEEDED' | 'FAILED' | 'NOT_CONFIGURED';

export type EventRecord = {
  id: string;
  name: string;
  starts_at: string;
  ends_at: string;
  venue: string;
  is_active: boolean;
};

export type NewEvent = {
  name: string;
  starts_at: string;
  ends_at: string;
  venue: string;
  instructions: string;
};

export type ImportPreviewData = {
  batch_id: string;
  status: string;
  total_rows: number;
  created: number;
  updated: number;
  skipped: number;
  rejected: number;
  email_evidence_rows: number;
  verified_payment_rows: number;
  unknown_columns: string[];
  rows: Array<{
    row: number;
    ticket_number: string;
    action: string;
    errors: string[];
    payment_status: string;
    verified_payment: boolean;
    email_delivery_evidence: boolean;
  }>;
  errors: Array<{ row: number; ticket_number: string; action: string; errors: string[] }>;
  commit_available: boolean;
  error_report_url: string;
  result: Record<string, number>;
  source_worksheet?: string;
};

let csrfToken = '';

async function request<T>(path: string, init: RequestInit = {}, expectedStatuses: number[] = []): Promise<T> {
  const method = (init.method || 'GET').toUpperCase();
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method) && !csrfToken) {
    await api.csrf();
  }
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json');
  }
  if (csrfToken && !['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    headers.set('X-CSRFToken', csrfToken);
  }
  const response = await fetch(`/api${path}`, {
    ...init,
    headers,
    credentials: 'include',
  });
  if (response.status === 204) return undefined as T;
  const payload = await response.json().catch(() => ({}));
  if (!response.ok && !expectedStatuses.includes(response.status)) {
    const validationError = Array.isArray(payload.non_field_errors)
      ? payload.non_field_errors[0]
      : Array.isArray(payload.email)
        ? payload.email[0]
        : undefined;
    const detail = typeof payload.detail === 'string'
      ? payload.detail
      : typeof validationError === 'string'
        ? validationError
        : `Request failed (${response.status})`;
    throw new Error(detail);
  }
  return payload as T;
}

export const api = {
  async csrf() {
    const data = await request<{ csrfToken: string }>('/csrf/');
    csrfToken = data.csrfToken;
  },
  async me() {
    const user = await request<User | { authenticated: false }>('/auth/me/');
    return 'authenticated' in user ? null : user;
  },
  async login(email: string, password: string) {
    await api.csrf();
    const user = await request<User>('/auth/login/', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    });
    await api.csrf();
    return user;
  },
  logout: () => request<void>('/auth/logout/', { method: 'POST' }),
  dashboard: () => request<DashboardData>('/admin/dashboard/'),
  tickets: (query: string) =>
    request<{ count: number; results: TicketRecord[] }>(
      `/admin/tickets/?page_size=50${query ? `&q=${encodeURIComponent(query)}` : ''}`,
    ),
  events: () => request<EventRecord[]>('/admin/events/'),
  createEvent: (payload: NewEvent) =>
    request<EventRecord>('/admin/events/', { method: 'POST', body: JSON.stringify(payload) }),
  updateEvent: (eventId: string, payload: Partial<Pick<EventRecord, 'is_active'>>) =>
    request<EventRecord>(`/admin/events/${eventId}/`, { method: 'PATCH', body: JSON.stringify(payload) }),
  scannerAccounts: () => request<ScannerAccount[]>('/admin/users/'),
  createScannerAccount: (payload: { name: string; email: string; password: string; gate: GateId }) =>
    request<ScannerAccount>('/admin/users/', { method: 'POST', body: JSON.stringify(payload) }),
  updateScannerAccount: (userId: number, payload: { password?: string; is_active?: boolean; gate?: GateId }) =>
    request<ScannerAccount>(`/admin/users/${userId}/`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deskAccounts: () => request<DeskAccount[]>('/admin/desks/'),
  createDeskAccount: (payload: { name: string; email: string; password: string; desk: DeskId }) =>
    request<DeskAccount>('/admin/desks/', { method: 'POST', body: JSON.stringify(payload) }),
  updateDeskAccount: (userId: number, payload: { password?: string; is_active?: boolean; desk?: DeskId }) =>
    request<DeskAccount>(`/admin/desks/${userId}/`, { method: 'PATCH', body: JSON.stringify(payload) }),
  createOnSpotRegistration: (payload: {
    event_id: string;
    attendee_name: string;
    attendee_email: string;
    attendee_phone: string;
    ticket_type: 'Single ticket' | 'Combo 5+1';
    quantity: number;
    payment_method: 'CASH' | 'CARD' | 'UPI';
    transaction_reference: string;
  }, idempotencyKey: string) =>
    request<{
      registration_id: string;
      ticket: TicketRecord;
      tickets: TicketRecord[];
      quantity: number;
      ticket_count: number;
      amount: string;
      issued: boolean;
      pdf_url: string | null;
      replayed: boolean;
      sheet_sync_status: SheetSyncStatus;
    }>('/admin/registrations/on-spot/', {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKey },
      body: JSON.stringify(payload),
    }),
  scan: (credential: string) =>
    request<{
      result: string;
      ticket_number?: string;
      attendee_name?: string;
      ticket_type?: string;
      checked_in_at?: string | null;
    }>('/scanner/scan/', {
      method: 'POST',
      body: JSON.stringify({ credential }),
    }, [409]),
  validateScan: (credential: string) =>
    request<{
      result: string;
      ticket_id?: string | null;
      ticket_number?: string | null;
      attendee_name?: string | null;
      ticket_type?: string | null;
      checked_in_at?: string | null;
    }>('/scanner/validate/', {
      method: 'POST',
      body: JSON.stringify({ credential }),
    }),
  checkInScan: (credential: string) =>
    request<{
      result: string;
      ticket_number?: string;
      attendee_name?: string;
      ticket_type?: string;
      checked_in_at?: string | null;
    }>('/scanner/check-in/', {
      method: 'POST',
      body: JSON.stringify({ credential }),
    }, [409]),
  scanHistory: () => request<Array<Record<string, string>>>('/scanner/history/'),
  sendTicketEmail: (ticketId: string) =>
    request<{ email_status: string }>(`/admin/tickets/${ticketId}/email/`, { method: 'POST' }),
  manualCheckIn: (ticketId: string, reason: string) =>
    request<{ result: string; ticket: TicketRecord }>(
      `/admin/tickets/${ticketId}/manual-check-in/`,
      { method: 'POST', body: JSON.stringify({ reason }) },
    ),
  async previewImport(file: File, eventId: string) {
    const form = new FormData();
    form.append('file', file);
    form.append('event_id', eventId);
    return request<ImportPreviewData>('/admin/import/preview/', { method: 'POST', body: form });
  },
  previewGoogleSheetImport: (worksheet: 'Online_Tickets' | 'OnSpot_Tickets', eventId: string) =>
    request<ImportPreviewData>('/admin/import/google-sheets/preview/', {
      method: 'POST',
      body: JSON.stringify({ worksheet, event_id: eventId }),
    }),
  commitImport: (batchId: string) =>
    request<{ created: number; updated: number; skipped: number; rejected: number; issued: number; error_report_url: string }>(
      `/admin/import/${batchId}/commit/`,
      { method: 'POST', body: JSON.stringify({ confirm: true }) },
    ),
};
