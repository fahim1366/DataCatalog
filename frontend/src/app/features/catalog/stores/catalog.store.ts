import { Injectable, computed, inject, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import {
  BulkTagRequest,
  CategoryTreeNode,
  ChangeLogItem,
  ColumnItem,
  KeysetPageInfo,
  PhysicalDatabaseNode,
  SearchHit,
  SearchResponse,
  TableDetail,
  TableItem,
} from '../models/catalog.models';

export interface CatalogFilterOptions {
  database?: string;
  schema?: string;
  classification?: string;
  status?: string;
  tag?: string;
}

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
  private readonly _activeFilterOptions = signal<CatalogFilterOptions>({});
  private readonly _error = signal<string | null>(null);

  // Public readonly selectors
  readonly tables = this._tables.asReadonly();
  readonly searchHits = this._searchHits.asReadonly();
  readonly isLoading = this._isLoading.asReadonly();
  readonly pageInfo = this._pageInfo.asReadonly();
  readonly activeFilter = this._activeFilter.asReadonly();
  readonly activeFilterOptions = this._activeFilterOptions.asReadonly();
  readonly error = this._error.asReadonly();

  // Computed signals
  readonly hasMore = computed<boolean>(() => this._pageInfo()?.has_next_page ?? false);
  readonly totalHitCount = computed<number>(() => this._searchHits().length);

  /**
   * Executes keyset-paginated search against the catalog API.
   * If reset is true, clears accumulated results and starts from beginning.
   */
  async search(
    query: string = '',
    reset: boolean = true,
    filters?: CatalogFilterOptions
  ): Promise<void> {
    if (reset) {
      this._searchHits.set([]);
      this._pageInfo.set(null);
      this._activeFilter.set(query);
      if (filters !== undefined) {
        this._activeFilterOptions.set(filters);
      }
    }

    this._isLoading.set(true);
    this._error.set(null);

    const activeOpts = filters !== undefined ? filters : this._activeFilterOptions();
    const cursor = reset ? '' : (this._pageInfo()?.next_cursor || '');
    let url = `/api/v1/search?q=${encodeURIComponent(query)}&limit=50&cursor=${cursor}`;

    if (activeOpts.database) {
      url += `&database=${encodeURIComponent(activeOpts.database)}`;
    }
    if (activeOpts.schema) {
      url += `&schema=${encodeURIComponent(activeOpts.schema)}`;
    }
    if (activeOpts.classification) {
      url += `&classification=${encodeURIComponent(activeOpts.classification)}`;
    }
    if (activeOpts.status) {
      url += `&status=${encodeURIComponent(activeOpts.status)}`;
    }
    if (activeOpts.tag) {
      url += `&tag=${encodeURIComponent(activeOpts.tag)}`;
    }

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
   * Retrieves full table details including columns and tags.
   */
  async getTableDetails(tableId: string): Promise<TableDetail> {
    return firstValueFrom(this.http.get<TableDetail>(`/api/v1/tables/${tableId}`));
  }

  /**
   * Updates approved description for a column.
   */
  async updateColumnDescription(columnId: string, approvedDescription: string): Promise<ColumnItem> {
    return firstValueFrom(
      this.http.patch<ColumnItem>(`/api/v1/columns/${columnId}/description`, {
        approved_description: approvedDescription,
      })
    );
  }

  /**
   * Updates security classification for a column.
   */
  async updateColumnClassification(columnId: string, classificationLevel: string): Promise<ColumnItem> {
    return firstValueFrom(
      this.http.patch<ColumnItem>(`/api/v1/columns/${columnId}/classification`, {
        classification_level: classificationLevel,
      })
    );
  }

  /**
   * Fetches change log history for a table.
   */
  async getTableHistory(tableId: string): Promise<ChangeLogItem[]> {
    return firstValueFrom(this.http.get<ChangeLogItem[]>(`/api/v1/tables/${tableId}/change-logs`));
  }

  /**
   * Fetches physical database tree.
   */
  async getPhysicalTree(): Promise<PhysicalDatabaseNode[]> {
    return firstValueFrom(this.http.get<PhysicalDatabaseNode[]>('/api/v1/tables/tree/physical'));
  }

  /**
   * Fetches category business hierarchy tree.
   */
  async getCategoryTree(): Promise<CategoryTreeNode[]> {
    return firstValueFrom(this.http.get<CategoryTreeNode[]>('/api/v1/categories/tree'));
  }

  /**
   * Executes bulk tag assignment across entities.
   */
  async bulkTag(request: BulkTagRequest): Promise<{ success: boolean; affected_count: number }> {
    return firstValueFrom(
      this.http.post<{ success: boolean; affected_count: number }>('/api/v1/tags/bulk-assign', request)
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
    this._activeFilterOptions.set({});
    this._error.set(null);
  }
}
