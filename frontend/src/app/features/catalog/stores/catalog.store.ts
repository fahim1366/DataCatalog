import { Injectable, computed, inject, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import {
  KeysetPageInfo,
  SearchHit,
  SearchResponse,
  TableItem,
} from '../models/catalog.models';

@Injectable({
  providedIn: 'root',
})
export class CatalogStore {
  private readonly http = inject(HttpClient);

  // Private state signals
  private readonly _tables = signal<TableItem[]>([]);
  private readonly _searchHits = signal<SearchHit[]>([]);
  private readonly _pageInfo = signal<KeysetPageInfo | null>(null);
  private readonly _isLoading = signal<boolean>(false);
  private readonly _activeFilter = signal<string>('');
  private readonly _error = signal<string | null>(null);

  // Public readonly selectors
  readonly tables = this._tables.asReadonly();
  readonly searchHits = this._searchHits.asReadonly();
  readonly isLoading = this._isLoading.asReadonly();
  readonly pageInfo = this._pageInfo.asReadonly();
  readonly activeFilter = this._activeFilter.asReadonly();
  readonly error = this._error.asReadonly();

  // Computed signals
  readonly hasMore = computed<boolean>(() => this._pageInfo()?.has_next_page ?? false);
  readonly totalHitCount = computed<number>(() => this._searchHits().length);

  /**
   * Executes keyset-paginated search against the catalog API.
   * If reset is true, clears accumulated results and starts from beginning.
   */
  async search(query: string, reset: boolean = true): Promise<void> {
    if (reset) {
      this._searchHits.set([]);
      this._pageInfo.set(null);
      this._activeFilter.set(query);
    }

    this._isLoading.set(true);
    this._error.set(null);

    const cursor = reset ? '' : (this._pageInfo()?.next_cursor || '');
    const url = `/api/v1/search?q=${encodeURIComponent(query)}&limit=50&cursor=${cursor}`;

    try {
      const resp = await firstValueFrom(this.http.get<SearchResponse>(url));
      this._searchHits.update(current => (reset ? resp.items : [...current, ...resp.items]));
      this._pageInfo.set(resp.page_info);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Catalog search request failed';
      this._error.set(msg);
      throw err;
    } finally {
      this._isLoading.set(false);
    }
  }

  /**
   * Appends the next keyset page to the existing search results.
   */
  async loadNextPage(): Promise<void> {
    if (this._isLoading() || !this.hasMore()) {
      return;
    }
    const currentQuery = this._activeFilter();
    await this.search(currentQuery, false);
  }

  /**
   * Updates approved description for a table and synchronizes reactive state.
   */
  async updateDescription(tableId: string, approvedDescription: string): Promise<void> {
    const updated = await firstValueFrom(
      this.http.patch<{ id: string; effective_description: string }>(
        `/api/v1/tables/${tableId}/description`,
        { approved_description: approvedDescription }
      )
    );

    const newDesc = updated.effective_description;

    this._tables.update(list =>
      list.map(t => (t.id === tableId ? { ...t, effective_description: newDesc } : t))
    );

    this._searchHits.update(list =>
      list.map(hit => (hit.id === tableId ? { ...hit, effective_description: newDesc } : hit))
    );
  }

  /**
   * Sets searchHits directly (useful for testing and component state priming).
   */
  setSearchHits(hits: SearchHit[]): void {
    this._searchHits.set(hits);
  }

  /**
   * Sets tables directly.
   */
  setTables(tables: TableItem[]): void {
    this._tables.set(tables);
  }

  /**
   * Resets all store state to initial defaults.
   */
  reset(): void {
    this._tables.set([]);
    this._searchHits.set([]);
    this._pageInfo.set(null);
    this._isLoading.set(false);
    this._activeFilter.set('');
    this._error.set(null);
  }
}

