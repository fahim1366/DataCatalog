import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { DatabaseUserItem } from '../models/db-user.models';

@Injectable({
  providedIn: 'root',
})
export class DbUserService {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = '/api/v1/connections';

  /**
   * Retrieves active database users and roles for a specific connection.
   */
  getDatabaseUsers(connectionId: string): Observable<DatabaseUserItem[]> {
    return this.http.get<DatabaseUserItem[]>(`${this.baseUrl}/${connectionId}/db-users`);
  }
}
