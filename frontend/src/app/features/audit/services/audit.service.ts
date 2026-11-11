import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import { AuditLogFilterParams, AuditLogResponse } from '../models/audit.models';

@Injectable({
  providedIn: 'root',
})
export class AuditService {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = '/api/v1/audit-logs';

  /**
   * Retrieves keyset-paginated immutable audit logs with multi-column filtering.
   */
  getAuditLogs(filters?: AuditLogFilterParams): Observable<AuditLogResponse> {
    let params = new HttpParams();
    if (filters) {
      if (filters.entity_type) {
        params = params.set('entity_type', filters.entity_type);
      }
      if (filters.entity_id) {
        params = params.set('entity_id', filters.entity_id);
      }
      if (filters.actor_id) {
        params = params.set('actor_id', filters.actor_id);
      }
      if (filters.action) {
        params = params.set('action', filters.action);
      }
      if (filters.start_time) {
        params = params.set('start_time', filters.start_time);
      }
      if (filters.end_time) {
        params = params.set('end_time', filters.end_time);
      }
      if (filters.cursor) {
        params = params.set('cursor', filters.cursor);
      }
      if (filters.limit !== undefined) {
        params = params.set('limit', filters.limit.toString());
      }
    }

    return this.http.get<AuditLogResponse>(this.baseUrl, { params });
  }
}

